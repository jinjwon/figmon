#!/usr/bin/env python3
"""
Figmon — 작업 시간으로 포켓몬을 키우는 데스크톱 펫 (macOS · Windows).

- Figma, VS Code, Xcode, 터미널 같은 작업 앱(또는 figma.com, github.com)을 쓰면 경험치와 휴식 잔고가 쌓인다.
- 유튜브/인스타/게임 등 딴짓을 하면 휴식 잔고가 줄어든다.
  잔고가 바닥났는데 계속하면 경험치가 깎인다(진화 단계 아래로는 안 떨어짐).
- (선택) AI 절제 모드: ChatGPT/Claude 등은 하루 무료 시간만큼만, 넘기면 딴짓 취급.

실행:  python3 server.py            (http://localhost:7777)
       Windows에서는 같은 명령으로 서버 + 펫(pet_win.py)이 함께 뜬다.
테스트: FIGMON_SPEED=60 python3 server.py   (1초 = 1분으로 빨리 감기)
"""
import base64
import bisect
import json
import socket
import tempfile
import urllib.request
import zipfile
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.26.0"   # build_app.sh·Windows 빌드가 이 값을 버전으로 씀
if os.environ.get("FIGMON_VERSION_OVERRIDE") and "--updated" not in sys.argv:   # 업데이트 점검(CI)용
    VERSION = os.environ["FIGMON_VERSION_OVERRIDE"]
REPO = "jinjwon/figmon"
PORT = int(os.environ.get("FIGMON_PORT", 7777))
SPEED = float(os.environ.get("FIGMON_SPEED", 1))
TICK = 3  # 초
IS_WIN = sys.platform == "win32"
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # PyInstaller 묶음이면 압축 해제 폴더
DATA_DIR = Path(os.environ.get("FIGMON_HOME", Path.home() / ".figmon"))
STATE_FILE = DATA_DIR / "state.json"
for _stream in (sys.stdout, sys.stderr):  # 한글을 못 찍는 Windows 콘솔에서도 죽지 않게
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stdout is None or sys.stderr is None:  # 콘솔 없는 Windows exe: 로그 파일로
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _lp = DATA_DIR / "server.log"
    try:   # 로그가 끝없이 커지지 않게: 1MB를 넘으면 옛 로그 하나만 남기고 새로 시작
        if _lp.stat().st_size > 1_000_000:
            os.replace(_lp, DATA_DIR / "server.old.log")
    except OSError:
        pass
    _log = open(_lp, "a", encoding="utf-8", buffering=1)
    sys.stdout, sys.stderr = sys.stdout or _log, sys.stderr or _log

MASTER_LV, MAX_LV = 36, 100   # Lv.36 = 완전 성장 (자랑 카드·졸업)
XP_PER_MIN = 3.0   # 작업 1분당 기본 경험치 (설정으로 못 바꿈 — 성장 꼼수 방지)
# 포켓몬마다 다른 성장 속도: 실제 게임의 성장 그룹별 경험치 곡선(레벨의 세제곱 공식)을 그대로 씀.
# 낮은 레벨은 금방 오르고 높을수록 느려짐. 게임 수치를 줄여서 "보통" 그룹의 Lv.36이 1800 경험치(약 10시간)가 되게 함
_XP_SCALE = 1800 / 36 ** 3


def _erratic(n):
    return n ** 3 * (100 - n) / 50 if n < 50 else n ** 3 * (150 - n) / 100 if n < 68 else \
        n ** 3 * ((1911 - 10 * n) // 3) / 500 if n < 98 else n ** 3 * (160 - n) / 100


def _fluctuating(n):
    return n ** 3 * ((n + 1) // 3 + 24) / 50 if n < 15 else n ** 3 * (n + 14) / 50 if n < 36 else n ** 3 * (n // 2 + 32) / 50


GROWTH = {"fast": (lambda n: 4 * n ** 3 / 5, "빠름"), "medium": (lambda n: n ** 3, "보통"),
          "medium-slow": (lambda n: 6 * n ** 3 / 5 - 15 * n ** 2 + 100 * n - 140, "조금 느림"),
          "slow": (lambda n: 5 * n ** 3 / 4, "느림"), "slow-then-very-fast": (_erratic, "변덕 (처음엔 느림)"),
          "fast-then-very-slow": (_fluctuating, "변덕 (나중에 느림)")}
XP_TABLE = {g: [0.0, 0.0] + [round(max(0.0, f(n)) * _XP_SCALE, 2) for n in range(2, MAX_LV + 1)] for g, (f, _) in GROWTH.items()}


def xp_at(lv, growth="medium"):
    """그 레벨이 되는 데 필요한 누적 경험치"""
    t = XP_TABLE.get(growth) or XP_TABLE["medium"]
    return t[max(0, min(MAX_LV, lv))]


def growth_of(pet):
    return LINES[pet["line"]].get("growth", "medium")
# 전설·환상: 졸업한 포켓몬 수로 열리고(능력치가 셀수록 더 많이 필요), 경험치도 조금 덜 받음
RARITY = {"legendary": {"name": "전설", "xp": 0.9}, "mythical": {"name": "환상", "xp": 0.85}}


def xp_mult(line_key):
    return LINES[line_key].get("xp_mult", 1.0)


TYPE_KO = {"normal": "노말", "fire": "불꽃", "water": "물", "grass": "풀", "electric": "전기", "ice": "얼음",
           "fighting": "격투", "poison": "독", "ground": "땅", "flying": "비행", "psychic": "에스퍼", "bug": "벌레",
           "rock": "바위", "ghost": "고스트", "dragon": "드래곤", "dark": "악", "steel": "강철", "fairy": "페어리"}


def grad_stats():
    """졸업한 포켓몬을 세대별·(세대, 타입)별로 센 것 — 전설·환상 해금 조건에 씀"""
    by_gen, by_type = {}, {}
    for d in STATE.get("dex", []):
        l = LINES.get(str(d.get("line")))
        if not l:
            continue
        typ = dex_type(d, l)
        by_gen[l["gen"]] = by_gen.get(l["gen"], 0) + 1
        by_type[f"{l['gen']}:{typ}"] = by_type.get(f"{l['gen']}:{typ}", 0) + 1
    return by_gen, by_type


def dex_type(d, l):
    """졸업한 포켓몬의 타입 (이브이·갈래 진화는 고른 진화형의 타입)"""
    if d.get("branch") in EEVEE_BY_ID:
        return EEVEE_BY_ID[d["branch"]]["type"]
    if l.get("fork") and d.get("fork_choice") is not None:
        return l["fork"]["options"][d["fork_choice"]]["type"]
    return l["type"]


def arceus_unlock():
    """아르세우스 해금: 여러 조건을 모두 채워야 함 (모든 타입의 신이라 가장 어려움)"""
    dex = STATE.get("dex", [])
    types, gen4, trio = set(), 0, 0
    for d in dex:
        l = LINES.get(str(d.get("line")))
        if not l:
            continue
        types.add(dex_type(d, l))
        gen4 += l["gen"] == 4
        trio += str(d.get("line")) in CREATION_TRIO
    conds = [{"label": "졸업한 포켓몬", "have": len(dex), "need": 12},
             {"label": "졸업시킨 타입 종류", "have": len(types), "need": 10},
             {"label": "4세대 포켓몬 졸업", "have": gen4, "need": 4},
             {"label": "창조의 신(디아루가·펄기아·기라티나) 졸업", "have": trio, "need": 1}]
    return {"open": all(c["have"] >= c["need"] for c in conds), "conds": conds, "gen": 4,
            "need": 12, "have": len(dex), "type": None, "type_have": 0}


def unlock_status(line_key):
    """전설·환상 해금: 같은 세대 졸업 N마리 + (있으면) 같은 타입 1마리"""
    l = LINES[line_key]
    if l.get("unlock_special"):
        return arceus_unlock()
    if not l.get("unlock_need"):
        return {"open": True}
    by_gen, by_type = grad_stats()
    have = by_gen.get(l["gen"], 0)
    t = l.get("unlock_type")
    t_have = by_type.get(f"{l['gen']}:{t}", 0) if t else 0
    return {"open": have >= l["unlock_need"] and (not t or t_have >= 1), "need": l["unlock_need"], "have": have,
            "type": t, "type_have": t_have, "gen": l["gen"]}


def is_unlocked(line_key):
    return unlock_status(line_key)["open"]
EVO_LEVELS = {1: [], 2: [20], 3: [16, 36]}   # 진화 데이터가 없을 때만 쓰는 진화 레벨
# 레벨이 아닌 조건으로 진화하는 포켓몬은 비슷한 시점의 레벨로 바꿈. 첫 진화면 그 레벨, 두 번째 진화면 앞 진화 + 그만큼 (최소 30)
EVO_FIRST = {"friend": 20, "stone": 25, "trade": 30, "other": 25}
EVO_NEXT = {"friend": 10, "stone": 12, "trade": 14, "other": 12}

DESIGN_APPS = ["Figma", "Sketch", "Framer", "Adobe Illustrator", "Adobe Photoshop", "Adobe XD", "Penpot",
               "Photoshop", "Illustrator", "XD"]  # 아래 소문자/64 이름들은 Windows 실행 파일 이름
DEV_APPS = ["Code", "Visual Studio Code", "Cursor", "Windsurf", "Zed", "Xcode", "Android Studio",
            "IntelliJ IDEA", "PyCharm", "WebStorm", "GoLand", "Rider", "CLion", "PhpStorm", "RubyMine",
            "DataGrip", "Fleet", "Sublime Text", "Nova", "Terminal", "iTerm2", "Warp", "Ghostty",
            "Alacritty", "kitty", "WezTerm", "Docker Desktop", "Postman", "Insomnia", "TablePlus",
            "Simulator", "GitHub Desktop", "Fork", "Tower", "Sourcetree",
            "devenv", "idea64", "pycharm64", "webstorm64", "goland64", "rider64", "clion64", "phpstorm64",
            "rubymine64", "datagrip64", "studio64", "sublime_text", "notepad++", "WindowsTerminal",
            "powershell", "pwsh", "cmd", "wezterm-gui", "GitHubDesktop", "Unity", "Godot"]
DESIGN_DOMAINS = ["figma.com", "framer.com", "penpot.app"]
DEV_DOMAINS = ["github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com", "developer.mozilla.org",
               "developer.apple.com", "vercel.com", "netlify.com", "codesandbox.io", "stackblitz.com",
               "replit.com", "npmjs.com", "pypi.org", "docs.python.org"]
PRESETS = {
    "design": {"work_apps": DESIGN_APPS, "work_domains": DESIGN_DOMAINS},
    "dev": {"work_apps": DEV_APPS, "work_domains": DEV_DOMAINS},
    "all": {"work_apps": DESIGN_APPS + DEV_APPS, "work_domains": DESIGN_DOMAINS + DEV_DOMAINS},
}

DEFAULT_CONFIG = {
    "work_apps": DESIGN_APPS + DEV_APPS,
    "work_domains": DESIGN_DOMAINS + DEV_DOMAINS,
    "distract_apps": ["Steam", "League of Legends", "Battle.net", "Minecraft",
                      "Roblox", "Genshin Impact", "Hearthstone", "Overwatch", "WutheringWaves",
                      "LeagueClient", "RiotClient", "VALORANT", "RobloxPlayer", "GenshinImpact",
                      "MapleStory", "LOSTARK", "Client-Win64-Shipping",
                      # 설치형 앱·웹앱으로 쓰는 딴짓 서비스 (브라우저가 아니라 주소를 못 볼 때 이름으로)
                      "Instagram", "YouTube", "TikTok", "Netflix", "Twitch", "Facebook", "Threads",
                      "Disney+", "TVING", "티빙", "wavve", "웨이브", "WATCHA", "왓챠", "Coupang Play", "쿠팡플레이",
                      "CHZZK", "치지직", "SOOP", "Games"],
    "distract_domains": ["youtube.com", "youtu.be", "instagram.com", "tiktok.com",
                         "netflix.com", "twitch.tv", "chzzk.naver.com", "x.com",
                         "twitter.com", "facebook.com", "afreecatv.com", "sooplive.co.kr", "threads.net",
                         "disneyplus.com", "tving.com", "wavve.com", "watcha.com", "coupangplay.com"],
    "xp_per_work_min": 3.0,      # 작업 1분당 경험치
    "break_earn_ratio": 5.0,     # 작업 N분 = 휴식 1분
    "bank_max_min": 60.0,        # 휴식 잔고 최대치
    "penalty_xp_per_min": 3.0,   # 잔고 없이 딴짓할 때 분당 감소 경험치
    "grace_sec": 20,             # 경고 후 페널티까지 유예
    "idle_limit_sec": 120,       # 이만큼 입력이 없으면 작업 시간에서 제외
    "detect_games": True,        # 게임 분류 앱·Steam/Epic 게임은 목록에 없어도 딴짓으로
    # 사용성 / 접근성
    "alert_dialog": True,        # 잔고 없을 때 "작업으로 돌아갈게" 팝업 (끄면 알림만)
    "pet_size": 1.0,             # 데스크톱 펫 크기 배율
    "auto_summon": True,         # 한동안 안 하다가 작업 앱을 켜면 펫이 걸어 나옴
    "auto_update": True,         # GitHub 새 버전 자동 설치
    # AI 절제 모드
    "ai_limit_enabled": False,
    "ai_daily_free_min": 30.0,   # 하루 무료 AI 시간, 넘기면 딴짓으로 취급
    "ai_apps": ["ChatGPT", "Claude", "Codex", "Perplexity", "Gemini", "Copilot", "Microsoft Copilot", "Grok"],
    "ai_domains": ["chatgpt.com", "chat.openai.com", "claude.ai", "gemini.google.com", "perplexity.ai",
                   "grok.com", "copilot.microsoft.com", "chat.deepseek.com", "poe.com", "wrtn.ai",
                   "clova-x.naver.com", "aistudio.google.com"],
}

# 1~9세대 스타팅 포켓몬 (도감번호, 한글 이름)
_RAW = [
    (1, "grass", [(1, "이상해씨"), (2, "이상해풀"), (3, "이상해꽃")]),
    (1, "fire", [(4, "파이리"), (5, "리자드"), (6, "리자몽")]),
    (1, "water", [(7, "꼬부기"), (8, "어니부기"), (9, "거북왕")]),
    (2, "grass", [(152, "치코리타"), (153, "베이리프"), (154, "메가니움")]),
    (2, "fire", [(155, "브케인"), (156, "마그케인"), (157, "블레이범")]),
    (2, "water", [(158, "리아코"), (159, "엘리게이"), (160, "장크로다일")]),
    (3, "grass", [(252, "나무지기"), (253, "나무돌이"), (254, "나무킹")]),
    (3, "fire", [(255, "아차모"), (256, "영치코"), (257, "번치코")]),
    (3, "water", [(258, "물짱이"), (259, "늪짱이"), (260, "대짱이")]),
    (4, "grass", [(387, "모부기"), (388, "수풀부기"), (389, "토대부기")]),
    (4, "fire", [(390, "불꽃숭이"), (391, "파이숭이"), (392, "초염몽")]),
    (4, "water", [(393, "팽도리"), (394, "팽태자"), (395, "엠페르트")]),
    (5, "grass", [(495, "주리비얀"), (496, "샤비"), (497, "샤로다")]),
    (5, "fire", [(498, "뚜꾸리"), (499, "차오꿀"), (500, "염무왕")]),
    (5, "water", [(501, "수댕이"), (502, "쌍검자비"), (503, "대검귀")]),
    (6, "grass", [(650, "도치마론"), (651, "도치보구"), (652, "브리가론")]),
    (6, "fire", [(653, "푸호꼬"), (654, "테르나"), (655, "마폭시")]),
    (6, "water", [(656, "개구마르"), (657, "개굴반장"), (658, "개굴닌자")]),
    (7, "grass", [(722, "나몰빼미"), (723, "빼미스로우"), (724, "모크나이퍼")]),
    (7, "fire", [(725, "냐오불"), (726, "냐오히트"), (727, "어흥염")]),
    (7, "water", [(728, "누리공"), (729, "키요공"), (730, "누리레느")]),
    (8, "grass", [(810, "흥나숭"), (811, "채키몽"), (812, "고릴타")]),
    (8, "fire", [(813, "염버니"), (814, "래비풋"), (815, "에이스번")]),
    (8, "water", [(816, "울머기"), (817, "누겔레온"), (818, "인텔리레온")]),
    (9, "grass", [(906, "나오하"), (907, "나로테"), (908, "마스카나")]),
    (9, "fire", [(909, "뜨아거"), (910, "악뜨거"), (911, "라우드본")]),
    (9, "water", [(912, "꾸왁스"), (913, "아꾸왁"), (914, "웨이니발")]),
]
# 1세대 151마리 (스타팅 제외 나머지 진화 계열). 키는 첫 단계 도감번호, 이브이는 진화 방향별로 따로.
_GEN1 = [
    ("10", "bug", [(10, "캐터피"), (11, "단데기"), (12, "버터플")]),
    ("13", "bug", [(13, "뿔충이"), (14, "딱충이"), (15, "독침붕")]),
    ("16", "normal", [(16, "구구"), (17, "피죤"), (18, "피죤투")]),
    ("19", "normal", [(19, "꼬렛"), (20, "레트라")]),
    ("21", "normal", [(21, "깨비참"), (22, "깨비드릴조")]),
    ("23", "poison", [(23, "아보"), (24, "아보크")]),
    ("25", "electric", [(25, "피카츄"), (26, "라이츄")]),
    ("27", "ground", [(27, "모래두지"), (28, "고지")]),
    ("29", "poison", [(29, "니드런♀"), (30, "니드리나"), (31, "니드퀸")]),
    ("32", "poison", [(32, "니드런♂"), (33, "니드리노"), (34, "니드킹")]),
    ("35", "fairy", [(35, "삐삐"), (36, "픽시")]),
    ("37", "fire", [(37, "식스테일"), (38, "나인테일")]),
    ("39", "normal", [(39, "푸린"), (40, "푸크린")]),
    ("41", "poison", [(41, "주뱃"), (42, "골뱃"), (169, "크로뱃")]),
    ("43", "grass", [(43, "뚜벅쵸"), (44, "냄새꼬"), (45, "라플레시아")]),
    ("46", "bug", [(46, "파라스"), (47, "파라섹트")]),
    ("48", "bug", [(48, "콘팡"), (49, "도나리")]),
    ("50", "ground", [(50, "디그다"), (51, "닥트리오")]),
    ("52", "normal", [(52, "나옹"), (53, "페르시온")]),
    ("54", "water", [(54, "고라파덕"), (55, "골덕")]),
    ("56", "fighting", [(56, "망키"), (57, "성원숭")]),
    ("58", "fire", [(58, "가디"), (59, "윈디")]),
    ("60", "water", [(60, "발챙이"), (61, "슈륙챙이"), (62, "강챙이")]),
    ("63", "psychic", [(63, "캐이시"), (64, "윤겔라"), (65, "후딘")]),
    ("66", "fighting", [(66, "알통몬"), (67, "근육몬"), (68, "괴력몬")]),
    ("69", "grass", [(69, "모다피"), (70, "우츠동"), (71, "우츠보트")]),
    ("72", "water", [(72, "왕눈해"), (73, "독파리")]),
    ("74", "rock", [(74, "꼬마돌"), (75, "데구리"), (76, "딱구리")]),
    ("77", "fire", [(77, "포니타"), (78, "날쌩마")]),
    ("79", "water", [(79, "야돈"), (80, "야도란")]),
    ("81", "electric", [(81, "코일"), (82, "레어코일"), (462, "자포코일")]),
    ("83", "normal", [(83, "파오리")]),
    ("84", "normal", [(84, "두두"), (85, "두트리오")]),
    ("86", "water", [(86, "쥬쥬"), (87, "쥬레곤")]),
    ("88", "poison", [(88, "질퍽이"), (89, "질뻐기")]),
    ("90", "water", [(90, "셀러"), (91, "파르셀")]),
    ("92", "ghost", [(92, "고오스"), (93, "고우스트"), (94, "팬텀")]),
    ("95", "rock", [(95, "롱스톤"), (208, "강철톤")]),
    ("96", "psychic", [(96, "슬리프"), (97, "슬리퍼")]),
    ("98", "water", [(98, "크랩"), (99, "킹크랩")]),
    ("100", "electric", [(100, "찌리리공"), (101, "붐볼")]),
    ("102", "grass", [(102, "아라리"), (103, "나시")]),
    ("104", "ground", [(104, "탕구리"), (105, "텅구리")]),
    ("106", "fighting", [(106, "시라소몬")]),
    ("107", "fighting", [(107, "홍수몬")]),
    ("108", "normal", [(108, "내루미"), (463, "내룸벨트")]),
    ("109", "poison", [(109, "또가스"), (110, "또도가스")]),
    ("111", "ground", [(111, "뿔카노"), (112, "코뿌리"), (464, "거대코뿌리")]),
    ("113", "normal", [(113, "럭키")]),
    ("114", "grass", [(114, "덩쿠리"), (465, "덩쿠림보")]),
    ("115", "normal", [(115, "캥카")]),
    ("116", "water", [(116, "쏘드라"), (117, "시드라"), (230, "킹드라")]),
    ("118", "water", [(118, "콘치"), (119, "왕콘치")]),
    ("120", "water", [(120, "별가사리"), (121, "아쿠스타")]),
    ("122", "psychic", [(122, "마임맨")]),
    ("123", "bug", [(123, "스라크"), (212, "핫삼")]),
    ("124", "ice", [(124, "루주라")]),
    ("125", "electric", [(125, "에레브")]),
    ("126", "fire", [(126, "마그마")]),
    ("127", "bug", [(127, "쁘사이저")]),
    ("128", "normal", [(128, "켄타로스")]),
    ("129", "water", [(129, "잉어킹"), (130, "갸라도스")]),
    ("131", "water", [(131, "라프라스")]),
    ("132", "normal", [(132, "메타몽")]),
    ("133", "normal", [(133, "이브이")]),   # 진화형은 아래 EEVEE_BRANCHES에서 시간대에 따라 고름
    # 1.6~1.7에서 쓰던 '진화 방향 미리 고르기' 이브이 (기존 저장 데이터용, 선택 창에는 안 보임)
    ("133-water", "water", [(133, "이브이"), (134, "샤미드")]),
    ("133-electric", "electric", [(133, "이브이"), (135, "쥬피썬더")]),
    ("133-fire", "fire", [(133, "이브이"), (136, "부스터")]),
    ("137", "normal", [(137, "폴리곤"), (233, "폴리곤2"), (474, "폴리곤Z")]),
    ("138", "rock", [(138, "암나이트"), (139, "암스타")]),
    ("140", "rock", [(140, "투구"), (141, "투구푸스")]),
    ("142", "rock", [(142, "프테라")]),
    ("143", "normal", [(143, "잠만보")]),
    ("144", "ice", [(144, "프리져")]),
    ("145", "electric", [(145, "썬더")]),
    ("146", "fire", [(146, "파이어")]),
    ("147", "dragon", [(147, "미뇽"), (148, "신뇽"), (149, "망나뇽")]),
    ("150", "psychic", [(150, "뮤츠")]),
    ("151", "psychic", [(151, "뮤")]),
]
# 나중에 2세대 이후 진화형이 붙은 계열의 예전 단계 수 (1.24까지): 예전 저장 변환용
OLD_STAGES = {"41": 2, "81": 2, "95": 1, "108": 1, "111": 2, "114": 1, "116": 2, "123": 1, "137": 1}
LINES = {str(st[0][0]): {"gen": g, "type": t, "stages": [list(x) for x in st], "starter": True} for g, t, st in _RAW}
LINES.update({k: {"gen": 1, "type": t, "stages": [list(x) for x in st], "starter": False} for k, t, st in _GEN1})
MEGA = {}
# 2~9세대: tools/build_pokedex.py가 PokeAPI 데이터로 만든 pokedex.json (스타팅처럼 이미 있는 계열은 그대로 둠)
try:
    _DEX = json.loads((HERE / "pokedex.json").read_text(encoding="utf-8"))
    for _k, _l in _DEX["lines"].items():
        if _k not in LINES:
            LINES[_k] = {**_l, "starter": False}
    _LEGEND, _MYTH = set(_DEX.get("legendary", [])), set(_DEX.get("mythical", []))
    for _l in LINES.values():
        _ids = [i for i, _ in _l["stages"]]
        _l["rarity"] = "mythical" if any(i in _MYTH for i in _ids) else "legendary" if any(i in _LEGEND for i in _ids) else None
        _l["legendary"] = bool(_l["rarity"])
    _DEX_INFO = _DEX.get("info", {})
    MEGA = {int(k): v for k, v in _DEX.get("mega", {}).items()}   # 종 id → [[모습 id, 이름, 타입], ...]
    EVO = _DEX.get("evo", {})   # 종 id → [방식, 최소 레벨]: 실제 게임의 진화 조건
except Exception as _e:
    EVO = {}
    print("pokedex.json을 읽지 못했어요:", _e)


def evo_path(ids):
    """진화 경로(종 id들)의 각 단계가 시작되는 레벨과 진화 방식. 실제 게임 레벨 그대로 (예: 미뇽 → Lv.30 신뇽 → Lv.55 망나뇽)"""
    if not EVO:
        return [0] + EVO_LEVELS[min(3, len(ids))][:len(ids) - 1], [None] * len(ids)
    lv, how = [0], [None]
    for i, sid in enumerate(ids[1:]):
        h, need = EVO.get(str(sid), ["other", None])
        if h == "level":
            need = need
        elif i == 0:
            need = EVO_FIRST[h]
        else:
            need = max(lv[-1] + EVO_NEXT[h], 30)
        lv.append(max(need, lv[-1] + 1))
        how.append(h)
    return lv, how


for _l in LINES.values():
    _ids = [i for i, _ in _l["stages"]]
    _l["evo_levels"], _l["evo_how"] = evo_path(_ids)   # 각 단계가 시작되는 레벨
    _last = _l["evo_levels"][-1]
    if _l.get("fork"):   # 갈래마다 진화 레벨이 다를 수 있음 (랄토스 → Lv.30 가디안 / 돌 엘레이드)
        for _o in _l["fork"]["options"]:
            _o["evo_levels"], _o["evo_how"] = evo_path(_ids + [i for i, _ in _o["stages"]])
        _l["fork_lv"] = min(o["evo_levels"][len(_ids)] for o in _l["fork"]["options"])
        _last = max(o["evo_levels"][-1] for o in _l["fork"]["options"])
    _l["master_lv"] = max(MASTER_LV, _last)   # 마지막 진화가 늦은 포켓몬(망나뇽 Lv.55)은 그때가 완전 성장


def _difficulty(l):
    """성장 그룹·전설 여부로 경험치 배율, 해금에 필요한 졸업 수, 완전 성장까지 시간"""
    info = (_DEX_INFO if "_DEX_INFO" in globals() else {})
    growth, bst = (info.get(str(l["stages"][0][0])) or ["medium", 0])[0], max(
        [(info.get(str(i)) or ["", 0])[1] for i, _ in l["stages"]] + [0])
    mult, need = 1.0, 0
    if l.get("rarity"):
        mult *= RARITY[l["rarity"]]["xp"]
        need = 1 if bst < 600 else 2 if bst < 680 else 3      # 능력치 합계가 높을수록 더 많이 졸업해야
        need += 1 if l["rarity"] == "mythical" else 0
    l.update(growth=growth, growth_name=GROWTH.get(growth, (0, "보통"))[1], xp_mult=round(mult, 3),
             unlock_need=need, bst=bst, hours=round(xp_at(l["master_lv"], growth) / (XP_PER_MIN * mult) / 60))
for _k in ("133-water", "133-electric", "133-fire"):   # 예전 저장용 이브이 계열: 이브이와 같은 Lv.20
    LINES[_k].update(hidden=True, evo_levels=[0, 20])
for _l in LINES.values():
    _difficulty(_l)
# 전설·환상마다 같은 타입 조건: 그 세대에 그 타입의 일반 포켓몬이 있을 때만 (못 여는 전설이 없게)
_COMMON_TYPES = {(l["gen"], l["type"]) for l in LINES.values() if not l.get("rarity") and not l.get("hidden")}
for _l in LINES.values():
    if _l.get("unlock_need"):
        _l["unlock_type"] = _l["type"] if (_l["gen"], _l["type"]) in _COMMON_TYPES else None

# 아르세우스: 모든 타입의 신. 따로 아주 어렵게 열리고 아주 느리게 자람. 함께 일한 시간만큼 타입(플레이트)이 늘어남
ARCEUS = "493"
ARCEUS_XP = 0.7       # 경험치 배율 (느림 그룹 곡선과 합쳐 완전 성장까지 약 18시간)
ARC_STEP_H = 3        # 아르세우스와 3시간 일할 때마다 새 타입 하나
ARC_TYPES = ["fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground", "flying",
             "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]
CREATION_TRIO = {"483", "484", "487"}
if ARCEUS in LINES:
    LINES[ARCEUS].update(xp_mult=ARCEUS_XP, unlock_special=True, unlock_need=12, unlock_type=None,
                         hours=round(xp_at(LINES[ARCEUS]["master_lv"], LINES[ARCEUS]["growth"]) / (XP_PER_MIN * ARCEUS_XP) / 60))


def arceus_forms(pet):
    n = min(len(ARC_TYPES), int(pet.get("work_sec", 0) // (ARC_STEP_H * 3600)))
    return ["normal"] + ARC_TYPES[:n]


def arceus_cur(pet):
    forms = arceus_forms(pet)
    return pet.get("arc_form") if pet.get("arc_form") in forms else forms[-1]


# 메가진화: 마지막 모습에서 Lv.45까지 키우면 (X/Y가 있으면 열심히 → X, 딴짓 줄이기 → Y)
MEGA_LV = 45   # 성장 곡선이 높은 레벨에서 가팔라서 60은 너무 멀어짐 (약 18~24시간)


def mega_options(line, st):
    return MEGA.get(line["stages"][st - 1][0], []) if st == len(line["stages"]) else []


def mega_pick(pet, opts):
    if len(opts) < 2:
        return 0
    sc = habit_scores(pet)
    return 0 if sc["hustle"] >= sc["focus"] else 1


def appearance(pet, lv, line, st):
    """지금 보이는 모습: (모습 id, 이름, 타입, 메가진화 중인지)"""
    sid, name = line["stages"][st - 1]
    if pet["line"] == ARCEUS:
        t = arceus_cur(pet)
        return (f"493-{t}", f"아르세우스 ({TYPE_KO[t]})", t, False) if t != "normal" else (sid, name, "normal", False)
    opts = mega_options(line, st)
    if opts and pet.get("mega") is not None and pet.get("mega_on", True) and lv >= mega_lv_of(pet):
        mid, mname, mtype = opts[min(pet["mega"], len(opts) - 1)]
        return mid, mname, mtype, True
    return sid, name, line["type"], False

# 이브이: 이브이로 일한 시간을 시간대별로 모아서, Lv.20에 많이 일한 시간대의 진화형 중에서 직접 고른다
EEVEE_LV = 20
EEVEE_SHARE = 0.25   # 이 비율 이상 일한 시간대의 진화형이 열림 (가장 많이 일한 시간대는 항상)
DAYPARTS = {"morning": "아침", "noon": "점심", "evening": "저녁", "night": "새벽"}
EEVEE_BRANCHES = [
    {"id": 470, "name": "리피아", "type": "grass", "daypart": "morning"},
    {"id": 700, "name": "님피아", "type": "fairy", "daypart": "morning"},
    {"id": 196, "name": "에브이", "type": "psychic", "daypart": "noon"},
    {"id": 135, "name": "쥬피썬더", "type": "electric", "daypart": "noon"},
    {"id": 136, "name": "부스터", "type": "fire", "daypart": "evening"},
    {"id": 134, "name": "샤미드", "type": "water", "daypart": "evening"},
    {"id": 197, "name": "블래키", "type": "dark", "daypart": "night"},
    {"id": 471, "name": "글레이시아", "type": "ice", "daypart": "night"},
]
EEVEE_BY_ID = {b["id"]: b for b in EEVEE_BRANCHES}
LINES["133"].update(branches=EEVEE_BRANCHES, branch_lv=EEVEE_LV, evo_levels=[0, EEVEE_LV])


def daypart_now(t=None):
    h = time.localtime(t).tm_hour
    return "morning" if 6 <= h < 11 else "noon" if 11 <= h < 17 else "evening" if 17 <= h < 21 else "night"


# 진화가 갈라지는 포켓몬: 키우는 습관으로 정해짐 (실제 게임 조건을 작업 습관으로 바꿈)
FORK_COND = {
    195: "day", 980: "night",                 # 우파 → 누오(낮) / 토오(밤)
    903: "day", 461: "night",                 # 포푸니 → 포푸니크(낮) / 포푸니라(밤, 실제로도 밤에 진화)
    106: "hustle", 107: "focus", 237: "balance",   # 배루키 → 시라소몬(공격) / 홍수몬(방어) / 카포에라(균형)
    267: "day", 269: "night",                 # 개무소 → 뷰티플라이(나비, 낮) / 독케일(나방, 밤)
    282: "focus", 475: "hustle",              # 랄토스 → 가디안 / 엘레이드
    291: "hustle", 292: "night",              # 토중몬 → 아이스크(빠름) / 껍질몬(유령)
    362: "day", 478: "night",                 # 눈꼬마 → 얼음귀신 / 눈여아
    367: "hustle", 368: "focus",              # 진주몽 → 헌테일(공격) / 분홍장이(방어)
    414: "hustle", 413: "focus",              # 도롱충이 → 나메일(날아다님) / 도롱마담(머무름)
    563: "day", 867: "night",                 # 데스마스 → 데스니칸 / 데스판
    791: "day", 792: "night",                 # 코스모그 → 솔가레오(해) / 루나아라(달)
    841: "hustle", 842: "balance", 1019: "focus",  # 과사삭벌레 → 애프룡 / 단지래플 / 과미드라
    936: "day", 937: "night",                 # 카르본 → 카디나르마 / 파라블레이즈
}
FORK_LABEL = {"day": "☀️ 낮(6~18시)에 많이 작업", "night": "🌙 밤(18~6시)에 많이 작업",
              "hustle": "🔥 하루에 오래 작업 (평균 4시간이면 만점)", "focus": "🎯 딴짓 줄이기 (딴짓 비율이 낮을수록)",
              "balance": "🍿 휴식 잔고는 잘 쓰고 페널티는 적게"}
for _l in LINES.values():
    if _l.get("fork"):
        _opts = _l["fork"]["options"]
        _default = ["day", "night"] if len(_opts) == 2 else ["hustle", "focus", "balance", "day", "night"]
        for _i, _o in enumerate(_opts):
            _o["cond"] = FORK_COND.get(_o["final"], _default[_i % len(_default)])
            _o["label"] = FORK_LABEL[_o["cond"]]


def habit_scores(pet):
    """키우는 동안의 습관 점수 (0~1). 갈래 진화를 정하는 데 씀"""
    st = pet.get("stats") or {}
    w, dis = st.get("work", 0.0), st.get("distract", 0.0)
    days = max(1, len(st.get("days", [])))
    day_share = st.get("day", 0.0) / w if w else 0.5
    return {"day": round(day_share, 3), "night": round(1 - day_share, 3),
            "hustle": round(min(1.0, (w / days) / 240), 3),
            "focus": round(max(0.0, 1 - ((dis / (w + dis)) if w + dis else 0) / 0.25), 3),
            "balance": round(min(1.0, st.get("brk", 0.0) / max(1.0, w / 10)) * (1 - min(1.0, st.get("pen", 0.0) / 30)), 3)}


def fork_pick(pet):
    """지금 습관대로라면 갈 진화형 번호 (점수가 가장 높은 쪽, 같으면 앞쪽)"""
    sc, opts = habit_scores(pet), LINES[pet["line"]]["fork"]["options"]
    return max(range(len(opts)), key=lambda i: (sc[opts[i]["cond"]], -i))


def pet_line(pet):
    """키우는 포켓몬의 실제 진화 계열. 이브이는 고른 진화형이, 갈래 진화는 정해진 진화형이 뒤에 붙는다"""
    base = LINES[pet["line"]]
    if base.get("fork"):
        i = pet.get("fork_choice")
        if i is None:
            return base
        o = base["fork"]["options"][i]
        st = base["stages"] + o["stages"]
        return {**base, "type": o["type"], "stages": st, "evo_levels": o["evo_levels"], "evo_how": o["evo_how"]}
    if not base.get("branches"):
        return base
    b = EEVEE_BY_ID.get(pet.get("branch"))
    if b:
        return {**base, "type": b["type"], "stages": base["stages"] + [[b["id"], b["name"]]], "evo_levels": [0, EEVEE_LV]}
    return {**base, "evo_levels": [0]}


def master_of(pet):
    """완전 성장(졸업) 레벨: 기본 Lv.36, 마지막 진화가 더 늦으면 그 레벨"""
    l = LINES[pet["line"]]
    if pet.get("grad_lv"):   # 진화형이 늘기 전에 이미 완전 성장한 포켓몬은 예전 레벨 그대로
        return pet["grad_lv"]
    if l.get("fork") and pet.get("fork_choice") is not None:
        return max(MASTER_LV, l["fork"]["options"][pet["fork_choice"]]["evo_levels"][-1])
    return l.get("master_lv", MASTER_LV)


def mega_lv_of(pet):
    return max(MEGA_LV, master_of(pet))


def eevee_options(pet):
    """시간대별 작업 비율과, 지금 고를 수 있는 진화형"""
    mins = pet.get("eevee") or {}
    total = sum(mins.values()) or 0
    share = {k: (mins.get(k, 0) / total if total else 0) for k in DAYPARTS}
    top = max(share, key=share.get) if total else None
    opts = [{**b, "share": round(share[b["daypart"]], 3), "daypart_ko": DAYPARTS[b["daypart"]],
             "open": bool(total) and (share[b["daypart"]] >= EEVEE_SHARE or b["daypart"] == top)} for b in EEVEE_BRANCHES]
    return {"minutes": {k: round(mins.get(k, 0), 1) for k in DAYPARTS}, "share": share, "options": opts}

BROWSERS = {
    "Google Chrome": "chromium", "Arc": "chromium", "Brave Browser": "chromium",
    "Microsoft Edge": "chromium", "Whale": "chromium", "Naver Whale": "chromium",
    "Chromium": "chromium", "Vivaldi": "chromium", "Opera": "chromium",
    "Safari": "safari", "Safari Technology Preview": "safari",
}

# Windows: 브라우저 탭 주소 대신 창 제목으로 사이트를 알아낸다 ("영상 제목 - YouTube - Chrome")
WIN_BROWSERS = {"chrome", "msedge", "firefox", "brave", "whale", "opera", "vivaldi", "arc", "zen"}
TITLE_HINTS = {
    "x.com": ["/ X"], "twitter.com": ["Twitter"], "youtu.be": [], "chzzk.naver.com": ["치지직", "CHZZK"],
    "afreecatv.com": ["아프리카TV", "AfreecaTV"], "sooplive.co.kr": ["SOOP"], "chatgpt.com": ["ChatGPT"],
    "chat.openai.com": ["ChatGPT"], "claude.ai": ["Claude"], "gemini.google.com": ["Gemini"],
    "aistudio.google.com": ["AI Studio"], "copilot.microsoft.com": ["Copilot"], "chat.deepseek.com": ["DeepSeek"],
    "clova-x.naver.com": ["CLOVA X"], "poe.com": ["Poe"], "wrtn.ai": ["뤼튼", "wrtn"],
    "developer.mozilla.org": ["MDN"], "developer.apple.com": ["Apple Developer"], "docs.python.org": ["Python documentation"],
    "stackoverflow.com": ["Stack Overflow"], "npmjs.com": ["npm"],
}

LOCK = threading.Lock()
LIVE = {"mode": "neutral", "app": "", "label": "", "idle": 0, "last_work": None, "front_hwnd": 0,
        "summon": 0, "summon_label": "", "last_work_at": 0.0, "update": None, "recent": [],
        "distract_since": None, "penalty_since": None, "last_dialog": 0.0}


# ---------- 레벨 계산 ----------
def level_of(xp, growth="medium"):
    t = XP_TABLE.get(growth) or XP_TABLE["medium"]
    return max(0, bisect.bisect_right(t, xp + 1e-9) - 1)


def stage_of(lv, line="1"):
    """지금 몇 번째 모습인지 (1부터). line은 계열 키나 pet_line() 결과"""
    l = line if isinstance(line, dict) else LINES[line]
    return sum(1 for need in l["evo_levels"] if lv >= need)


def stage_floor_xp(stage, line="1"):
    l = line if isinstance(line, dict) else LINES[line]
    return xp_at(l["evo_levels"][stage - 1], l.get("growth", "medium"))


def species_name(pet, stage):
    return pet_line(pet)["stages"][stage - 1][1]


# ---------- 상태 저장 ----------
def fresh_state():
    return {"config": dict(DEFAULT_CONFIG), "pet": None, "bank_sec": 0.0, "paused_until": 0.0,
            "total_work_sec": 0.0, "dex": [], "events": [], "today": new_today(), "xp_curve": 1, "evo_v": 2}


def migrate_xp_curve(s):
    """예전(레벨당 50 경험치) 저장을 성장 곡선으로 바꿈: 레벨과 레벨 안 진행도는 그대로 둠"""
    if s.get("xp_curve") == 1:
        return
    pet = s.get("pet")
    if pet and pet.get("line") in LINES:
        xp, g = float(pet.get("xp", 0)), growth_of(pet)
        lv = min(MAX_LV, int(xp // 50))
        frac = 0.0 if lv >= MAX_LV else (xp - lv * 50) / 50
        pet["xp"] = xp_at(lv, g) + frac * (xp_at(lv + 1, g) - xp_at(lv, g))
    s["xp_curve"] = 1


def migrate_evo_levels(s):
    """예전엔 모두 Lv.16/20/36에 진화했음. 진화 레벨이 실제 게임 값으로 바뀌어도 이미 진화한 모습은 그대로 둠
    (모자란 만큼 레벨을 올려 줌)"""
    if s.get("evo_v", 0) >= 1:
        return migrate_new_stages(s)
    pet = s.get("pet")
    if pet and pet.get("line") in LINES and not LINES[pet["line"]].get("branches"):
        base, line, g = LINES[pet["line"]], pet_line(pet), growth_of(pet)
        n = OLD_STAGES.get(pet["line"], len(line["stages"]))
        if base.get("fork") and pet.get("fork_choice") is None:
            n += max(len(o["stages"]) for o in base["fork"]["options"])
        old = [0] + EVO_LEVELS[min(3, n)]
        lv = level_of(pet["xp"], g)
        was = min(len(line["stages"]), n, sum(1 for need in old if lv >= need))
        if stage_of(lv, line) < was:
            pet["xp"] = max(pet["xp"], xp_at(line["evo_levels"][was - 1], g))
    s["evo_v"] = 1
    migrate_new_stages(s)


def migrate_new_stages(s):
    """롱스톤 → 강철톤, 골뱃 → 크로뱃처럼 진화형이 새로 붙은 계열: 이미 진화 레벨을 넘긴 포켓몬은
    레벨은 그대로 두고 진화한 모습이 됨 (말없이 바뀌지 않게 기록을 남김).
    마지막 진화가 늦어져 완전 성장 레벨이 올라간 계열(킹드라 Lv.46 등)은 이미 완전 성장했으면 예전 레벨 그대로"""
    if s.get("evo_v", 0) >= 2:
        return
    pet = s.get("pet")
    if pet and pet.get("line") in OLD_STAGES and pet.get("line") in LINES:
        line, n = LINES[pet["line"]], OLD_STAGES[pet["line"]]
        lv = level_of(pet.get("xp", 0), growth_of(pet))
        old_master = max(MASTER_LV, line["evo_levels"][n - 1])
        if lv >= old_master and master_of(pet) > old_master:
            pet["grad_lv"] = old_master
        st = stage_of(lv, line)
        if st > n:
            s.setdefault("events", []).append({"t": time.time(), "kind": "evo", "msg":
                f"{line['stages'][n - 1][1]} → {line['stages'][st - 1][1]} 진화! (새 진화형이 생겼어요)"})
    s["evo_v"] = 2


def new_today():
    # apps: 앱·사이트별 시간 {이름: {kind, sec, break_sec, penalty_sec, xp_lost}}, penalties: 페널티 받은 구간
    return {"date": date.today().isoformat(), "work_sec": 0.0, "distract_sec": 0.0, "xp_lost": 0.0, "ai_sec": 0.0,
            "break_sec": 0.0, "apps": {}, "penalties": []}


HISTORY_DAYS = 60


RECENT_SKIP = {"", "figmon", "127.0.0.1", "localhost", "loginwindow", "dock", "explorer", "searchhost",
               "shellexperiencehost", "lockapp", "startmenuexperiencehost"}


def remember_recent(app, url, label, kind):
    """설정의 '최근 쓴 앱·사이트에서 바로 추가'용: 최근에 앞에 띄운 앱·사이트 12개"""
    if url:
        name, typ = re.sub(r"^www\.", "", re.sub(r"^[a-z]+://", "", url.lower()).split("/")[0].split(":")[0]), "site"
    elif IS_WIN and app.lower() in WIN_BROWSERS:
        if kind == "neutral":
            return   # 창 제목만 보이는 브라우저 일반 탭은 사이트 이름을 알 수 없음
        name, typ = label, "site"
    else:
        name, typ = app, "app"
    if name.lower() in RECENT_SKIP:
        return
    rec = [r for r in LIVE["recent"] if r["name"] != name]
    LIVE["recent"] = [{"name": name, "type": typ, "kind": kind}] + rec[:11]


def roll_day():
    """날짜가 바뀌면 어제 기록을 history에 넣고 오늘을 새로 시작"""
    s = STATE
    if s["today"]["date"] == date.today().isoformat():
        return
    old = s["today"]
    if old.get("work_sec", 0) or old.get("distract_sec", 0) or old.get("ai_sec", 0):
        apps = sorted(old.get("apps", {}).items(), key=lambda kv: -kv[1]["sec"])[:25]   # 많이 쓴 25개만 보관
        s.setdefault("history", []).append({**old, "apps": dict(apps), "penalties": old.get("penalties", [])[-50:]})
        del s["history"][:-HISTORY_DAYS]
    s["today"] = new_today()


def track(label, kind, dt, part="", xp=0.0):
    """오늘 어떤 앱·사이트에서 얼마나 썼는지 기록 (part: break=휴식 잔고로 쉼, penalty=페널티)"""
    name = re.sub(r"^www\.", "", label or "알 수 없음")[:40]
    e = STATE["today"].setdefault("apps", {}).setdefault(
        name, {"kind": kind, "sec": 0.0, "break_sec": 0.0, "penalty_sec": 0.0, "xp_lost": 0.0})
    e["kind"] = kind
    e["sec"] += dt
    if part:
        e[part + "_sec"] += dt
    e["xp_lost"] += xp


def merge_new_defaults(cfg):
    """새 버전에서 기본 목록에 추가된 앱·사이트를 기존 사용자 설정에도 넣는다.
    한 번 본 기본 항목은 기억해 두어서, 사용자가 일부러 지운 건 다시 넣지 않는다."""
    known = set(cfg.get("_known_defaults", []))
    for k, v in DEFAULT_CONFIG.items():
        if isinstance(v, list):
            cur = cfg.setdefault(k, [])
            for item in v:
                if f"{k}:{item}" not in known and item not in cur:
                    cur.append(item)
    cfg["_known_defaults"] = [f"{k}:{i}" for k, v in DEFAULT_CONFIG.items() if isinstance(v, list) for i in v]


def load_state():
    s = fresh_state()
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s.update(saved)
            s["xp_curve"] = saved.get("xp_curve", 0)   # 예전 저장엔 없음 → 아래에서 변환
            s["evo_v"] = saved.get("evo_v", 0)
            s["config"] = {**DEFAULT_CONFIG, **s.get("config", {})}
            merge_new_defaults(s["config"])
            s["today"] = {**new_today(), **s["today"]}
            s.setdefault("history", [])
            migrate_xp_curve(s)
            migrate_evo_levels(s)
        except Exception as e:
            print("상태 파일을 읽지 못해 새로 시작합니다:", e)
    return s


_LAST_SAVE = [0.0]


def save(force=False):
    """상태 저장. 감지 루프(3초마다)에서는 15초에 한 번만 쓰고, 사용자 조작·종료 때는 바로 씀"""
    if not force and time.time() - _LAST_SAVE[0] < 15:
        return
    _LAST_SAVE[0] = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, ensure_ascii=False), encoding="utf-8")  # Windows 기본 인코딩은 한글을 못 담음
    tmp.replace(STATE_FILE)


STATE = load_state()


def log(kind, msg):
    _LAST_SAVE[0] = 0.0   # 진화·레벨업 같은 일이 생기면 다음 저장을 바로
    STATE["events"].append({"t": time.time(), "kind": kind, "msg": msg})
    del STATE["events"][:-60]


# ---------- macOS 감지 ----------
def sh(args, timeout=2):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              creationflags=0x08000000 if IS_WIN else 0).stdout.strip()  # CREATE_NO_WINDOW
    except Exception:
        return ""


def front_app():
    """macOS: 앞에 있는 앱의 (이름, 번들 위치)"""
    asn = sh(["lsappinfo", "front"])
    if not asn:
        return "", ""
    out = sh(["lsappinfo", "info", "-only", "name", "-only", "bundlepath", asn])   # 한 번에
    name, path = parse_lsappinfo(out)
    if not name and not _FRONT_WARNED[0]:   # 출력 형식이 또 바뀌면 조용히 멈추지 말고 로그에 남김
        _FRONT_WARNED[0] = True
        print("앞에 있는 앱의 이름을 읽지 못했어요 (lsappinfo 출력):", out[:200].replace("\n", " | "))
    return name, path


_FRONT_WARNED = [False]


def parse_lsappinfo(out):
    """lsappinfo info 출력에서 (앱 이름, 번들 위치). macOS 26 이하와 macOS 27 이후의 출력 형식을 모두 읽는다.
    옛 형식:  "LSDisplayName"="Brave Browser"  /  "LSBundlePath"="/Applications/Brave Browser.app"
    새 형식:  "Brave Browser" ASN:0x0-0x3e03e: (in front)  /  bundle path="/Applications/Brave Browser.app"
    (새 형식에서 못 읽으면 앱 이름이 항상 비어서 유튜브 같은 딴짓이 하나도 잡히지 않았음)"""
    m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', out) or re.match(r'\s*(?:\d+\)\s*)?"(.*)"\s+ASN:', out)
    b = re.search(r'"LSBundlePath"\s*=\s*"([^"]*)"', out) or re.search(r'bundle path="([^"]*)"', out)
    return (m.group(1) if m else ""), (b.group(1) if b else "")


GAME_PATHS = ("/steamapps/common/", "\\steamapps\\common\\", "/epic games/", "\\epic games\\",
              "\\riot games\\", "\\xboxgames\\", "\\gog galaxy\\games\\", "/gog galaxy/games/")
_APP_INFO = {}


def app_info(path):
    """번들 정보로 알아낸 (웹앱 원래 주소, 게임인지). 크롬·브레이브·엣지 웹앱은 CrAppModeShortcutURL에 주소가 있다"""
    if not path:
        return "", False
    if path not in _APP_INFO:
        url, game = "", any(g in path.lower() for g in GAME_PATHS)
        try:
            with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
                info = plistlib.load(f)
            url = info.get("CrAppModeShortcutURL", "") or ""
            game = game or "games" in str(info.get("LSApplicationCategoryType", ""))
        except Exception:
            pass
        _APP_INFO[path] = (url, game)
    return _APP_INFO[path]


def browser_url(app):
    kind = BROWSERS.get(app)
    if not kind:
        return ""
    prop = "URL of current tab of front window" if kind == "safari" else "URL of active tab of front window"
    return sh(["osascript", "-e", f'tell application "{app}" to get {prop}'])


_CG_IDLE = None
if sys.platform == "darwin":   # 입력 없는 시간: 외부 명령(ioreg) 대신 시스템 함수로 바로
    try:
        import ctypes as _ct
        import ctypes.util as _ctu
        _CG_IDLE = _ct.cdll.LoadLibrary(_ctu.find_library("ApplicationServices")).CGEventSourceSecondsSinceLastEventType
        _CG_IDLE.restype, _CG_IDLE.argtypes = _ct.c_double, [_ct.c_int32, _ct.c_uint32]
    except Exception:
        _CG_IDLE = None


_CG_KEYS = None
if sys.platform == "darwin":   # 키를 누른 "횟수"만 (무슨 키인지는 모름 · 권한 필요 없음) → 펫이 타이핑 박자에 맞춰 움직임
    try:
        _CG_KEYS = _ct.cdll.LoadLibrary(_ctu.find_library("ApplicationServices")).CGEventSourceCounterForEventType
        _CG_KEYS.restype, _CG_KEYS.argtypes = _ct.c_uint32, [_ct.c_int32, _ct.c_uint32]
    except Exception:
        _CG_KEYS = None


def key_count():
    return int(_CG_KEYS(1, 10)) if _CG_KEYS else -1   # 10 = kCGEventKeyDown


def key_idle():
    """마지막으로 키를 누른 뒤 지난 초 (카운터가 막힌 환경에서도 이건 됨)"""
    return round(float(_CG_IDLE(1, 10)), 2) if _CG_IDLE else -1


def idle_seconds():
    if _CG_IDLE:
        return float(_CG_IDLE(1, 0xFFFFFFFF))   # HID 시스템 상태, 모든 입력
    m = re.search(r'"HIDIdleTime" = (\d+)', sh(["ioreg", "-c", "IOHIDSystem", "-d", "4"]))
    return int(m.group(1)) / 1e9 if m else 0.0


if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetTickCount.restype = wintypes.DWORD

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def win_front():
    """Windows: 앞에 있는 창의 (실행 파일 이름, 창 제목, hwnd)"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", 0
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = full = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            full = path.value
            name = Path(full).stem
        kernel32.CloseHandle(h)
    LIVE["front_path"] = full
    return name, buf.value, hwnd


def win_idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    user32.GetLastInputInfo(ctypes.byref(li))
    return ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def sample():
    """지금 앞에 있는 (앱 이름, 탭 주소, 창 제목, 입력 없는 초). 게임 여부는 LIVE["front_game"]에"""
    if IS_WIN:
        app, title, hwnd = win_front()
        LIVE["front_hwnd"] = hwnd
        LIVE["front_game"] = any(g in LIVE.get("front_path", "").lower() for g in GAME_PATHS)
        return app, "", title, win_idle_seconds()
    app, path = front_app()
    shortcut_url, game = app_info(path)
    LIVE["front_game"] = game
    # 웹앱(크롬으로 만든 Instagram 앱 등)은 원래 주소로 판단
    return app, browser_url(app) or shortcut_url, "", idle_seconds()


def title_keywords(domain):
    if domain in TITLE_HINTS:
        return TITLE_HINTS[domain]
    name = domain.split(".")[0]
    return [name] if len(name) >= 4 else []


def classify_title(title, cfg):
    """브라우저 창 제목으로 분류. 맨 끝(브라우저 이름)을 빼고 마지막 조각(보통 사이트 이름)을 먼저 본다."""
    parts = [p.strip() for p in re.split(r"\s[-–—|·]\s", title) if p.strip()]
    if len(parts) > 1:
        parts = parts[:-1]
    site = parts[-1] if parts else title
    for text in (site, title):
        low = text.lower()
        for kind in ("ai", "work", "distract"):
            for d in cfg[kind + "_domains"]:
                hit = next((k for k in title_keywords(d) if k.lower() in low), None)
                if hit:
                    return kind, d
    return "neutral", (parts[0] if parts else title)[:40]


WIN_TITLE_APPS = WIN_BROWSERS | {"applicationframehost", "wwahost"}   # 스토어 앱은 창 제목으로


def classify(app, url, cfg, title="", game=False):
    if title and app.lower() in WIN_TITLE_APPS:
        kind, label = classify_title(title, cfg)
        if kind != "neutral" or app.lower() in WIN_BROWSERS:
            return kind, label
        app = title   # 스토어 앱: 창 제목("Netflix")을 앱 이름처럼 비교
    host = re.sub(r"^[a-z]+://", "", url.lower()).split("/")[0].split(":")[0] if url else ""
    dom = lambda lst: bool(host) and any(host == d or host.endswith("." + d) for d in lst)
    # 앱 이름은 정확히 같거나 앞부분이 같으면 인정 ("IntelliJ IDEA" ↔ "IntelliJ IDEA CE")
    appm = lambda lst: bool(app) and any(a and app.lower().startswith(a.lower()) for a in lst)
    label = host or app
    # AI를 먼저 본다 ("Codex"가 작업 앱 "Code"로 잡히지 않게). AI 절제 모드가 꺼져 있으면 step()에서 neutral 처리
    if url:  # 브라우저·웹앱은 열린 사이트로 먼저 판단
        for kind in ("ai", "work", "distract"):
            if dom(cfg[kind + "_domains"]):
                return kind, label
    for kind in ("ai", "work", "distract"):
        if appm(cfg[kind + "_apps"]):
            return kind, (label if not url else app)
    if game and cfg.get("detect_games", True):   # 목록에 없어도 게임이면 딴짓
        return "distract", app
    return "neutral", label


# ---------- 알림 / 반응 ----------
def esc(t):
    return t.replace("\\", "\\\\").replace('"', '\\"')


PS_TOAST = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $x.GetElementsByTagName('text')
$t.Item(0).AppendChild($x.CreateTextNode('@TITLE@')) > $null
$t.Item(1).AppendChild($x.CreateTextNode('@MSG@')) > $null
$n = [Windows.UI.Notifications.ToastNotification]::new($x)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)
"""


def notify(title, msg):
    if IS_WIN:  # Windows 토스트 알림 (PowerShell 내장 API)
        q = lambda t: t.replace("'", "''")
        ps = PS_TOAST.replace("@TITLE@", q("Figmon · " + title)).replace("@MSG@", q(msg))
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        threading.Thread(target=sh, args=(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                                           "-EncodedCommand", enc], 15), daemon=True).start()
        return
    threading.Thread(target=sh, args=(["osascript", "-e",
        f'display notification "{esc(msg)}" with title "Figmon" subtitle "{esc(title)}" sound name "Pop"'],),
        daemon=True).start()


def ask_return(msg):
    if not STATE["config"]["alert_dialog"]:
        return
    now = time.time()
    if now - LIVE["last_dialog"] < 90:
        return
    LIVE["last_dialog"] = now

    def run_win():
        # 예/아니요 | 경고 아이콘 | 맨 위 | 시스템 모달
        r = user32.MessageBoxW(None, msg + "\n\n작업으로 돌아갈까요? (아니요 = 경험치 감소)", "Figmon", 0x4 | 0x30 | 0x40000 | 0x1000)
        back = LIVE["last_work"]
        if r == 6 and back and back[0] == "hwnd" and back[1]:
            user32.ShowWindow(back[1], 9)  # SW_RESTORE
            user32.SetForegroundWindow(back[1])

    if IS_WIN:
        threading.Thread(target=run_win, daemon=True).start()
        return

    def run():
        out = sh(["osascript", "-e",
                  f'display dialog "{esc(msg)}" with title "Figmon" '
                  f'buttons {{"그래도 할래 (경험치 감소)", "작업으로 돌아갈게"}} default button 2 '
                  f'with icon caution giving up after 30'], timeout=40)
        if "작업으로 돌아갈게" in out:
            back = LIVE["last_work"]
            if back and back[0] == "app":
                sh(["open", "-a", back[1]])
            elif back:
                sh(["open", back[1]])
    threading.Thread(target=run, daemon=True).start()


def fmt(sec):
    sec = int(sec)
    return f"{sec // 60}분 {sec % 60}초" if sec >= 60 else f"{sec}초"


# ---------- 게임 루프 ----------
def step(app, url, idle, dt, title=""):
    s, cfg, now = STATE, STATE["config"], time.time()
    roll_day()
    kind, label = classify(app, url, cfg, title, LIVE.get("front_game", False))
    remember_recent(app, url, label, kind)
    if now < s.get("paused_until", 0):  # 일시정지 중엔 아무것도 쌓거나 깎지 않음
        LIVE.update(app=app, label=label, idle=int(idle), mode="paused", distract_since=None, penalty_since=None)
        save()
        return
    if kind == "work" and idle > cfg["idle_limit_sec"]:
        kind = "idle"
    if kind == "work":
        LIVE["last_work"] = ("url", url) if url else ("hwnd", LIVE["front_hwnd"]) if IS_WIN else ("app", app)
        # 10분 넘게 쉬다가 작업 앱을 켜면 펫을 불러냄 (실행 직후 첫 작업 포함)
        if cfg["auto_summon"] and now - LIVE["last_work_at"] > 600:
            LIVE["summon"] += 1
            LIVE["summon_label"] = label
        LIVE["last_work_at"] = now
    ai_over = False
    if kind == "ai":
        s["today"]["ai_sec"] += dt          # AI 시간은 절제 모드가 꺼져 있어도 기록
        if not cfg["ai_limit_enabled"]:
            track(label, "ai", dt)
            kind = "neutral"
        else:
            ai_over = s["today"]["ai_sec"] > cfg["ai_daily_free_min"] * 60
            if ai_over:
                kind = "distract"
            else:
                track(label, "ai", dt)
    LIVE.update(app=app, label=label, idle=int(idle))
    pet = s["pet"]
    before = level_of(pet["xp"], growth_of(pet)) if pet else None
    name = species_name(pet, stage_of(before, pet_line(pet))) if pet else "포켓몬"

    if kind != "distract":
        LIVE["distract_since"] = LIVE["penalty_since"] = None

    if kind == "work":
        mode = "work"
        s["today"]["work_sec"] += dt
        s["total_work_sec"] += dt
        track(label, "work", dt)
        s["bank_sec"] = min(cfg["bank_max_min"] * 60, s["bank_sec"] + dt / max(cfg["break_earn_ratio"], 0.1))
        if pet:
            pet["xp"] += dt / 60 * XP_PER_MIN * xp_mult(pet["line"])
            pet["work_sec"] += dt
            if LINES[pet["line"]].get("branches") and not pet.get("branch"):
                ev = pet.setdefault("eevee", {})
                part = daypart_now(now)
                ev[part] = ev.get(part, 0) + dt / 60
    elif kind == "distract":
        s["today"]["distract_sec"] += dt
        first = LIVE["distract_since"] is None
        if first:
            LIVE["distract_since"] = now
        if s["bank_sec"] > 0:
            mode = "break"
            if first:
                notify("🍿 휴식 시작", f"{label} · 휴식 잔고 {fmt(s['bank_sec'])} 남음")
                log("break", f"{label} — 휴식 잔고로 쉬는 중")
            s["bank_sec"] = max(0.0, s["bank_sec"] - dt)
            s["today"]["break_sec"] = s["today"].get("break_sec", 0) + dt
            track(label, "distract", dt, "break")
        else:
            if LIVE["penalty_since"] is None:
                LIVE["penalty_since"] = now
                g = cfg["grace_sec"]
                if first and ai_over:
                    msg = f"{name}: 오늘 AI 무료 시간을 다 썼어! 네 머리로도 해보자 🧠 {g}초 뒤부터 경험치가 깎여"
                    log("warn", f"{label} — 오늘 AI 시간 초과")
                elif first:
                    msg = f"{name}: 앗, {label}…? 휴식 잔고가 0이야! {g}초 뒤부터 경험치가 깎여 😢"
                    log("warn", f"{label} 진입 — 휴식 잔고 없음")
                else:
                    msg = f"{name}: 휴식 잔고를 다 썼어! {g}초 안에 작업으로 돌아가자 🙏"
                    log("warn", "휴식 잔고 소진")
                ask_return(msg)
                notify("⚠️ 휴식 잔고 없음", msg)
            if now - LIVE["penalty_since"] >= cfg["grace_sec"]:
                mode = "penalty"
                loss = 0.0
                if pet:
                    loss = dt / 60 * cfg["penalty_xp_per_min"]
                    floor = stage_floor_xp(stage_of(level_of(pet["xp"], growth_of(pet)), pet_line(pet)), pet_line(pet))
                    loss = min(loss, max(0.0, pet["xp"] - floor))
                    pet["xp"] -= loss
                    s["today"]["xp_lost"] += loss
                track(label, "distract", dt, "penalty", loss)
                pens = s["today"].setdefault("penalties", [])
                if LIVE["mode"] != "penalty" or not pens:   # 페널티 구간 시작
                    pens.append({"start": now, "label": re.sub(r"^www\.", "", label), "ai": ai_over, "sec": 0.0, "xp": 0.0})
                    del pens[:-100]
                pens[-1]["sec"] += dt
                pens[-1]["xp"] += loss
                pens[-1]["end"] = now
            else:
                mode = "warning"
                track(label, "distract", dt)
    elif kind == "ai":
        mode = "ai"
    elif kind == "idle":
        mode = "idle"
    else:
        mode = "neutral"

    if mode == "penalty" and LIVE["mode"] != "penalty":
        log("penalty", f"페널티 시작! {label} 때문에 경험치가 줄고 있어요")
    LIVE["mode"] = mode

    if pet:   # 키우는 동안의 습관 (갈래 진화를 정하는 데 씀)
        st = pet.setdefault("stats", {"work": 0.0, "distract": 0.0, "brk": 0.0, "pen": 0.0, "day": 0.0, "days": []})
        m = dt / 60
        if mode == "work":
            st["work"] += m
            if 6 <= time.localtime(now).tm_hour < 18:
                st["day"] += m
            today_s = s["today"]["date"]
            if not st["days"] or st["days"][-1] != today_s:
                st["days"] = (st["days"] + [today_s])[-120:]
        elif mode in ("break", "warning", "penalty"):
            st["distract"] += m
            if mode == "break":
                st["brk"] += m
        if mode == "penalty":
            st["pen"] += loss if "loss" in locals() else 0.0
        line = LINES[pet["line"]]
        if line.get("fork") and pet.get("fork_choice") is None and level_of(pet["xp"], growth_of(pet)) >= line["fork_lv"]:
            i = fork_pick(pet)
            pet["fork_choice"] = i
            o = line["fork"]["options"][i]
            log("evo", f"키운 습관이 진화를 정했어요: {o['label']} → {o['stages'][0][1]}")

    if pet:
        after = level_of(pet["xp"], growth_of(pet))
        if after != before:
            sb, sa = stage_of(before, pet_line(pet)), stage_of(after, pet_line(pet))
            if sa > sb:
                msg = f"{species_name(pet, sb)} → {species_name(pet, sa)} 진화!"
                log("evo", msg)
                notify("✨ 진화!", msg)
            if LINES[pet["line"]].get("branches") and not pet.get("branch") and before < EEVEE_LV <= after:
                log("evo", "이브이가 진화하려고 해요! 대시보드에서 진화형을 골라 주세요")
                notify("✨ 진화할 수 있어요", "이브이가 진화하려고 해요! Figmon 대시보드에서 골라 주세요")
            if before < master_of(pet) <= after:
                log("grad", f"Lv.{master_of(pet)} 완전 성장! 자랑 카드를 만들 수 있어요")
                notify("🏆 완전 성장", "Figmon 창에서 자랑 카드를 만들어 자랑해보세요!")
            elif sa > sb:
                pass
            elif after > before:
                log("lvup", f"Lv.{after} 달성!")
            else:
                log("lvdown", f"Lv.{after}로 떨어졌어요…")
        line_now = pet_line(pet)
        st_now = stage_of(after, line_now)
        opts = mega_options(line_now, st_now)
        if opts and after >= mega_lv_of(pet) and pet.get("mega") is None:
            pet["mega"], pet["mega_on"] = mega_pick(pet, opts), True
            msg = f"{line_now['stages'][st_now - 1][1]} → {opts[pet['mega']][1]} 메가진화!"
            log("evo", msg)
            notify("💎 메가진화!", msg)
        if pet["line"] == ARCEUS and len(arceus_forms(pet)) > pet.get("arc_seen", 1):
            pet["arc_seen"] = len(arceus_forms(pet))
            t = arceus_forms(pet)[-1]
            log("evo", f"아르세우스가 {TYPE_KO[t]} 타입의 힘을 얻었어요! ({pet['arc_seen']}/18)")
            notify("✨ 새 타입", f"아르세우스가 {TYPE_KO[t]} 타입의 힘을 얻었어요!")
    save()


def loop():
    last = time.time()
    while True:
        time.sleep(TICK)
        now = time.time()
        dt = min(now - last, TICK * 4) * SPEED  # 잠자기 모드 후 폭주 방지
        last = now
        app, url, title, idle = sample()
        with LOCK:
            try:
                step(app, url, idle, dt, title)
            except Exception as e:
                print("tick error:", e, file=sys.stderr)


# ---------- API ----------
def view():
    s, cfg = STATE, STATE["config"]
    live = {k: LIVE[k] for k in ("mode", "app", "label", "idle", "summon", "summon_label")}
    if LIVE["penalty_since"]:
        live["grace_left"] = max(0, cfg["grace_sec"] - (time.time() - LIVE["penalty_since"]))
    out = {"config": cfg, "bank_sec": s["bank_sec"], "today": s["today"], "paused_until": s.get("paused_until", 0),
           "total_work_sec": s["total_work_sec"], "dex": s["dex"],
           "events": s["events"][-25:][::-1], "live": live, "speed": SPEED, "pet": None, "version": VERSION,
           "update": LIVE["update"],
           "recent": LIVE["recent"],
           "unlock": {"graduated": len(s["dex"]), "by_gen": grad_stats()[0], "by_type": grad_stats()[1],
                      "special": {ARCEUS: arceus_unlock()} if ARCEUS in LINES else {}}, "platform": "windows" if IS_WIN else "mac", "releases_url": RELEASES_URL}
    pet = s["pet"]
    if pet:
        xp = pet["xp"]
        g = growth_of(pet)
        lv = level_of(xp, g)
        line = pet_line(pet)
        st = stage_of(lv, line)
        lv_list = line["evo_levels"]
        nxt = lv_list[st] if st < len(lv_list) else None
        rate = max(XP_PER_MIN * xp_mult(pet["line"]), 0.01)
        aid, aname, atype, is_mega = appearance(pet, lv, line, st)
        out["pet"] = {**pet, "level": lv, "stage": st, "type": atype, "gen": line["gen"],
                      "stages": line["stages"], "species": [aid, aname],
                      "name": pet.get("nickname") or aname,
                      "lv_progress": 1 if lv >= MAX_LV else (xp - xp_at(lv, g)) / max(xp_at(lv + 1, g) - xp_at(lv, g), 1e-9),
                      "evo_levels": lv_list, "final": st == len(line["stages"]),
                      "rarity": LINES[pet["line"]].get("rarity"), "xp_mult": xp_mult(pet["line"]), "xp_rate": round(rate, 2),
                      "growth_name": LINES[pet["line"]].get("growth_name", "보통"),
                      "mastered": lv >= master_of(pet), "master_lv": master_of(pet),
                      "next_evo_lv": nxt,
                      "min_to_evo": max(0.0, xp_at(nxt, g) - xp) / rate if nxt else None,
                      "min_to_master": max(0.0, (xp_at(master_of(pet), g) - xp) / rate)}
        mo = MEGA.get(line["stages"][-1][0], []) if not (LINES[pet["line"]].get("fork") and pet.get("fork_choice") is None) else []
        if mo:   # 메가진화할 수 있는 계열
            out["pet"]["mega_info"] = {"lv": mega_lv_of(pet), "options": [{"id": i, "name": n, "type": t} for i, n, t in mo],
                                       "choice": pet.get("mega"), "lean": mega_pick(pet, mo), "on": pet.get("mega_on", True),
                                       "active": is_mega, "ready": st == len(line["stages"]) and lv >= mega_lv_of(pet),
                                       "min_to": max(0.0, (xp_at(mega_lv_of(pet), g) - xp) / rate)}
        if pet["line"] == ARCEUS:
            forms, done = arceus_forms(pet), pet.get("work_sec", 0) / 3600
            out["pet"]["arceus"] = {"forms": [{"type": t, "id": f"493-{t}" if t != "normal" else 493, "open": t in forms}
                                              for t in ["normal"] + ARC_TYPES],
                                    "cur": arceus_cur(pet), "auto": pet.get("arc_form") is None, "step_h": ARC_STEP_H,
                                    "next_min": None if len(forms) > len(ARC_TYPES) else (ARC_STEP_H * len(forms) - done) * 60}
        fl = LINES[pet["line"]]
        if fl.get("fork"):   # 갈래 진화: 진화 예측
            sc = habit_scores(pet)
            out["pet"]["fork"] = {"lv": fl["fork_lv"], "choice": pet.get("fork_choice"), "lean": fork_pick(pet),
                                  "options": [{"id": o["final"], "name": o["stages"][-1][1], "first": o["stages"][0][1],
                                               "type": o["type"], "cond": o["cond"], "label": o["label"],
                                               "score": sc[o["cond"]]} for o in fl["fork"]["options"]]}
            if pet.get("fork_choice") is None and lv < fl["fork_lv"]:
                out["pet"]["next_evo_lv"] = fl["fork_lv"]
                out["pet"]["min_to_evo"] = max(0.0, xp_at(fl["fork_lv"], g) - xp) / rate
        if LINES[pet["line"]].get("branches"):   # 이브이
            out["pet"]["eevee"] = eevee_options(pet)
            out["pet"]["branch_lv"] = EEVEE_LV
            out["pet"]["branch_pending"] = not pet.get("branch") and lv >= EEVEE_LV
            if not pet.get("branch"):
                out["pet"]["next_evo_lv"] = EEVEE_LV if lv < EEVEE_LV else None
                out["pet"]["min_to_evo"] = max(0.0, xp_at(EEVEE_LV, g) - xp) / rate if lv < EEVEE_LV else None
                out["pet"]["mastered"] = False   # 진화형을 골라야 완전 성장
    return out


# 바꿀 수 있는 숫자 설정의 범위 (꼼수 방지: 경험치 배율은 아예 못 바꾸고, 자리비움 판정도 너무 길게 못 함)
LIMITS = {"break_earn_ratio": (3, 30), "bank_max_min": (10, 180), "penalty_xp_per_min": (1, 10),
          "grace_sec": (5, 120), "idle_limit_sec": (30, 600), "pet_size": (0.6, 1.6), "ai_daily_free_min": (0, 240)}


def apply_config(body):
    cfg = STATE["config"]
    for k, v in body.items():
        if k not in DEFAULT_CONFIG or k == "xp_per_work_min":
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            try:
                cfg[k] = min(hi, max(lo, float(v)))
            except (TypeError, ValueError):
                pass
            continue
        if isinstance(DEFAULT_CONFIG[k], bool):
            cfg[k] = bool(v)
        elif isinstance(DEFAULT_CONFIG[k], list):
            cfg[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            cfg[k] = max(0.0, float(v))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/keys":   # 펫이 자주 물어봄 (0.25초마다) → 잠금 없이 숫자 하나만
            self._send(200, {"n": key_count(), "k": key_idle()})
        elif self.path.startswith("/api/state"):
            with LOCK:
                v = view()
            if "lite=1" in self.path:   # 펫은 이것만 있으면 됨 (자주 물어보니 작게)
                v = {"pet": v["pet"], "live": v["live"], "version": v["version"],
                     "config": {"pet_size": v["config"].get("pet_size", 1)}}
            self._send(200, v)
        elif self.path == "/api/report":   # 기록 탭: 오늘 + 지난 날들
            with LOCK:
                self._send(200, {"today": STATE["today"], "history": STATE.get("history", [])[-HISTORY_DAYS:],
                                 "now": time.time()})
        elif self.path == "/api/meta":
            self._send(200, {"lines": LINES, "master_lv": MASTER_LV, "rarity": RARITY,
                             "xp_table": XP_TABLE, "presets": PRESETS, "limits": LIMITS, "growth": {k: n for k, (_, n) in GROWTH.items()},
                             "platform": "windows" if IS_WIN else "mac"})
        elif self.path == "/pet":
            self._send(200, (HERE / "pet.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        with LOCK:
            s = STATE
            if self.path == "/api/choose":
                if str(body.get("line")) not in LINES:
                    return self._send(400, {"error": "잘못된 선택"})
                if s["pet"] is not None and not body.get("replace"):
                    return self._send(400, {"error": "이미 키우는 포켓몬이 있어요"})
                if not is_unlocked(str(body["line"])):
                    l = LINES[str(body["line"])]
                    u = unlock_status(str(body["line"]))
                    if u.get("conds"):
                        return self._send(400, {"error": f"{l['stages'][0][1]}은(는) 조건을 모두 채우면 열려요: "
                                                         + ", ".join(f"{c['label']} {c['have']}/{c['need']}" for c in u["conds"])})
                    return self._send(400, {"error": f"{l['stages'][0][1]}은(는) {u['gen']}세대 포켓몬 {u['need']}마리를 졸업시키면 열려요"
                                                     + (f" (그중 {TYPE_KO.get(u['type'], u['type'])} 타입 1마리)" if u.get("type") else "")})
                old = s["pet"]
                s["pet"] = {"line": str(body["line"]), "xp": 0.0, "work_sec": 0.0,
                            "nickname": "", "born": time.time()}
                new = LINES[s["pet"]["line"]]["stages"][0][1]
                if old:
                    log("hatch", f"{species_name(old, stage_of(level_of(old['xp'], growth_of(old)), pet_line(old)))}와(과) 작별… {new}와(과) Lv.0부터 새 출발!")
                else:
                    log("hatch", f"{new}와(과) 함께 시작! 작업할수록 같이 자라요")
            elif self.path == "/api/mega":   # 메가진화 모습 ↔ 원래 모습
                pet = s["pet"]
                if not pet or pet.get("mega") is None:
                    return self._send(400, {"error": f"Lv.{MEGA_LV}에 메가진화한 뒤에 바꿀 수 있어요"})
                pet["mega_on"] = bool(body.get("on"))
            elif self.path == "/api/arceus":   # 아르세우스: 열린 타입 중 하나 고르기 (null이면 가장 최근 타입)
                pet = s["pet"]
                t = body.get("type")
                if not pet or pet["line"] != ARCEUS or (t is not None and t not in arceus_forms(pet)):
                    return self._send(400, {"error": "아직 열리지 않은 타입이에요"})
                pet["arc_form"] = t
            elif self.path == "/api/nickname" and s["pet"]:
                s["pet"]["nickname"] = str(body.get("nickname", ""))[:12]
            elif self.path == "/api/graduate":
                pet = s["pet"]
                if not pet or level_of(pet["xp"], growth_of(pet)) < master_of(pet):
                    return self._send(400, {"error": f"Lv.{master_of(pet) if pet else MASTER_LV} 완전 성장 후에 졸업할 수 있어요"})
                lv = level_of(pet["xp"], growth_of(pet))
                st = stage_of(lv, pet_line(pet))
                aid, aname, _, _ = appearance(pet, lv, pet_line(pet), st)
                s["dex"].append({**pet, "level": lv, "species": [aid, aname],
                                 "finished": time.time()})
                log("grad", f"{species_name(pet, st)} Lv.{lv} 도감에 등록! 새 파트너를 골라주세요")
                s["pet"] = None
            elif self.path == "/api/evolve":   # 이브이 진화형 고르기
                pet = s["pet"]
                if not pet or not LINES[pet["line"]].get("branches") or pet.get("branch") or level_of(pet["xp"], growth_of(pet)) < EEVEE_LV:
                    return self._send(400, {"error": "지금은 진화할 수 없어요"})
                opt = next((o for o in eevee_options(pet)["options"] if o["id"] == body.get("id")), None)
                if not opt or not opt["open"]:
                    return self._send(400, {"error": "그 진화형은 아직 열리지 않았어요"})
                pet["branch"] = opt["id"]
                log("evo", f"이브이 → {opt['name']} 진화! ({opt['daypart_ko']}에 많이 함께했어요)")
                notify("✨ 진화!", f"이브이 → {opt['name']} 진화!")
            elif self.path == "/api/update_check":   # 대시보드의 '지금 업데이트 확인' (Windows)
                threading.Thread(target=win_update, args=(True,), daemon=True).start()
            elif self.path == "/api/summon":   # 펫 부르기 (Windows에서 Figmon을 한 번 더 실행했을 때 등)
                LIVE["summon"] += 1
                LIVE["summon_label"] = ""
            elif self.path == "/api/pause":
                minutes = float(body.get("minutes", 0))
                s["paused_until"] = time.time() + minutes * 60 if minutes > 0 else 0.0
                log("pause", f"추적 일시정지 ({int(minutes)}분)" if minutes > 0 else "추적 다시 시작")
            elif self.path == "/api/config":
                apply_config(body)
            else:
                return self._send(404, {"error": "not found"})
            save(force=True)
            self._send(200, view())


def newer(a, b):
    """버전 a가 b보다 새 버전인지 ("1.10.0" > "1.9.2")"""
    num = lambda v: [int(x) for x in re.findall(r"\d+", v)[:3]]
    return num(a) > num(b)


def latest_tag():
    """최신 릴리스 태그. API 대신 releases/latest 리디렉트를 따라가서 알아냄 (요청 횟수 제한 없음)"""
    req = urllib.request.Request(f"https://github.com/{REPO}/releases/latest", headers={"User-Agent": "Figmon"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl().rstrip("/").rsplit("/", 1)[-1]   # …/releases/tag/v1.7.0 → v1.7.0


RELEASES_URL = f"https://github.com/{REPO}/releases/latest"


def set_update(state, msg="", latest=""):
    """대시보드에 보여 줄 업데이트 상태 (checking·latest·downloading·failed·dev)"""
    LIVE["update"] = {"state": state, "msg": msg, "latest": latest, "at": time.time()}


def folder_writable(folder):
    try:
        probe = Path(folder) / f".figmon-write-test-{os.getpid()}"
        probe.write_bytes(b"1")
        probe.unlink()
        return True
    except OSError:
        return False


def win_update(manual=False):
    """Windows exe 자동 업데이트: 새 exe를 받아 실행하면, 새 exe가 이 프로세스가 끝나길 기다렸다가
    원래 자리에 자기를 복사하고 다시 켠다 (--apply-update). 업데이트를 시작하면 이 프로세스는 끝난다."""
    if not (IS_WIN and getattr(sys, "frozen", False)):
        set_update("dev", "소스로 실행 중이면 git pull로 업데이트해 주세요")
        return False
    set_update("checking", "새 버전을 확인하는 중…")
    try:
        test = "--updated" not in sys.argv and os.environ.get("FIGMON_UPDATE_TAG")   # CI 점검용
        tag = test or latest_tag()
        print(f"update check: latest {tag}, current {VERSION}", flush=True)
        if not newer(tag, VERSION):
            set_update("latest", f"최신 버전이에요 (v{VERSION})", tag)
            if manual:
                notify("최신 버전이에요", f"Figmon v{VERSION}")
            return False
        exe = Path(sys.executable)
        if not folder_writable(exe.parent):   # Program Files 같은 곳: 바꿔 끼울 수 없음
            msg = f"'{exe.parent}' 폴더에 쓸 수 없어서 자동 업데이트를 못 해요. Figmon.exe를 바탕 화면이나 내 문서 폴더로 옮겨 주세요."
            set_update("failed", msg, tag)
            notify("업데이트를 못 했어요", msg)
            return False
        set_update("downloading", f"v{tag.lstrip('v')} 받는 중…", tag)
        url = (test and os.environ.get("FIGMON_UPDATE_URL")) or f"https://github.com/{REPO}/releases/download/{tag}/Figmon-Windows.zip"
        tmp = Path(tempfile.mkdtemp(prefix="figmon-update-"))
        zpath = tmp / "update.zip"
        zpath.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Figmon"}), timeout=180).read())
        with zipfile.ZipFile(zpath) as z:
            z.extract("Figmon.exe", tmp)
        zpath.unlink()   # 압축 파일(12MB)은 더 필요 없음
        log("update", f"v{tag.lstrip('v')} 업데이트를 설치하고 다시 시작해요")
        with LOCK:
            save(force=True)
        subprocess.Popen([str(tmp / "Figmon.exe"), "--apply-update", sys.executable, str(os.getpid())],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED | NEW_PROCESS_GROUP
        os._exit(0)
    except Exception as e:
        print("update check failed:", repr(e), flush=True)
        set_update("failed", "업데이트를 확인하지 못했어요. 인터넷 연결을 확인해 주세요.")
        if manual:
            notify("업데이트 확인 실패", "인터넷 연결을 확인해 주세요")
    return False


def apply_update(target):
    """(새 exe로 실행됨) 옛 exe 잠금이 풀리면 그 자리에 나를 복사하고 다시 켠다.
    끝내 못 바꾸면 옛 버전을 다시 켜고, 다운로드 페이지를 열어 준다"""
    import shutil
    for _ in range(90):
        try:
            shutil.copy2(sys.executable, target)
            subprocess.Popen([target, "--startup", "--updated"], close_fds=True)
            return
        except OSError:
            time.sleep(1)
    notify("업데이트를 마치지 못했어요", "다운로드 페이지에서 새 Figmon.exe를 받아 주세요")
    webbrowser.open(RELEASES_URL)
    subprocess.Popen([target, "--startup"], close_fds=True)


def clean_old_updates():
    """업데이트할 때 임시 폴더에 받은 새 exe(수십 MB)가 남아 쌓이지 않게 지난 것들을 지운다.
    지금 실행 중인 exe가 든 폴더는 건너뛰고, 아직 잠겨 있으면 다음 실행 때 지운다."""
    import shutil
    here = Path(sys.executable).resolve().parent
    for d in Path(tempfile.gettempdir()).glob("figmon-update-*"):
        if d.is_dir() and d.resolve() != here and time.time() - d.stat().st_mtime > 600:   # 받는 중인 건 건드리지 않게
            shutil.rmtree(d, ignore_errors=True)


def update_loop():
    time.sleep(float(os.environ.get("FIGMON_UPDATE_DELAY", 10)))
    clean_old_updates()
    while True:
        if STATE["config"].get("auto_update", True):
            win_update()
        time.sleep(3600)   # 1시간마다


def launch_pet():
    """개발용: build/FigmonPet(Figmon.swift를 컴파일한 것)이 있으면 펫 창으로 함께 띄운다."""
    exe = HERE / "build" / "FigmonPet"
    if not exe.exists():
        return None
    env = {**os.environ, "FIGMON_PORT": str(PORT), "FIGMON_PARENT": str(os.getpid())}
    return subprocess.Popen([str(exe)], env=env)


class FigmonServer(ThreadingHTTPServer):
    """Windows에선 SO_REUSEADDR이 '이미 쓰는 포트에 또 붙기'를 허용해서, 실행할 때마다 서버·펫이 늘어났다.
    포트를 혼자 쓰도록(SO_EXCLUSIVEADDRUSE) 해서 두 번째 실행은 바인드에 실패하게 한다."""
    allow_reuse_address = not IS_WIN

    def server_bind(self):
        if IS_WIN:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def win_single_instance():
    """Windows: 이름 붙은 뮤텍스로 한 번에 하나만 실행 (거의 동시에 여러 번 눌러도). 이미 실행 중이면 False"""
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    win_single_instance.handle = kernel32.CreateMutexW(None, False, f"Local\\FigmonSingleInstance-{PORT}")
    return kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def win_focus_dashboard():
    """Windows: 이미 열린 대시보드(Edge/Chrome 앱 창, 제목 Figmon)가 있으면 앞으로 가져옴"""
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith("Figmon"):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                h = kernel32.OpenProcess(0x1000, False, pid.value)
                if h:
                    size, path = wintypes.DWORD(1024), ctypes.create_unicode_buffer(1024)
                    if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)) and \
                            Path(path.value).stem.lower() in ("msedge", "chrome"):
                        found.append(hwnd)
                    kernel32.CloseHandle(h)
        return True

    user32.EnumWindows(proto(each), 0)
    if found:
        user32.ShowWindow(found[0], 9)   # SW_RESTORE
        user32.SetForegroundWindow(found[0])
    return bool(found)


def open_window(title=True):
    url = f"http://localhost:{PORT}/" + ("?title=1" if title else "")  # 실행할 때는 타이틀 화면부터
    if IS_WIN:  # Edge 앱 창 (Windows 10/11 기본 설치). 이미 열려 있으면 새로 띄우지 않고 앞으로
        if win_focus_dashboard():
            return
        for base_dir in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            edge = Path(base_dir or "") / "Microsoft/Edge/Application/msedge.exe"
            if base_dir and edge.exists():
                subprocess.Popen([str(edge), f"--app={url}", "--window-size=460,860"])
                return
        webbrowser.open(url)
        return
    if Path("/Applications/Google Chrome.app").exists():
        subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=460,860"])
    else:
        webbrowser.open(url)


def _on_term(*_):
    """맥 앱이 끌 때 보내는 종료 신호: 저장하고 끝"""
    with LOCK:
        save(force=True)
    os._exit(0)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, _on_term)
    if "--apply-update" in sys.argv:   # Windows 업데이트 마무리 단계
        apply_update(sys.argv[sys.argv.index("--apply-update") + 1])
        sys.exit(0)
    quiet = "--no-open" in sys.argv or "--startup" in sys.argv or "--updated" in sys.argv  # 로그인 자동 실행·업데이트 후엔 창 안 띄움
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 고해상도 화면에서 흐려지지 않게
        except Exception:
            pass
    try:
        if IS_WIN and not win_single_instance():
            raise OSError("already running")
        srv = FigmonServer(("127.0.0.1", PORT), Handler)
    except OSError:  # 이미 실행 중이면 펫을 불러내고(대시보드도 열고) 끝
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/summon", data=b"{}",
                                                          headers={"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
        if not quiet:
            open_window(False)
        sys.exit(0)
    threading.Thread(target=loop, daemon=True).start()
    print(f"Figmon 실행 중 → http://localhost:{PORT}  (속도 x{SPEED:g}, 종료: Ctrl+C)")
    if not quiet:
        open_window()
    if IS_WIN and "--no-pet" not in sys.argv:  # Windows: 펫(tkinter)이 메인 스레드, 서버는 백그라운드
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=update_loop, daemon=True).start()
        if "--updated" in sys.argv:
            notify("업데이트 완료", f"Figmon v{VERSION}로 업데이트했어요 ✨")
        import pet_win
        try:
            pet_win.run(PORT, open_window, check_update=lambda: threading.Thread(target=win_update, args=(True,), daemon=True).start(),
                        start_hidden="--startup" in sys.argv and "--updated" not in sys.argv, version=VERSION,
                        updated="--updated" in sys.argv)
        finally:
            with LOCK:
                save(force=True)
        sys.exit(0)
    pet_proc = None if "--no-pet" in sys.argv else launch_pet()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        with LOCK:
            save(force=True)
    finally:
        if pet_proc:
            pet_proc.terminate()
