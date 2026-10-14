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
import json
import tempfile
import urllib.request
import zipfile
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.7.0"   # build_app.sh·Windows 빌드가 이 값을 버전으로 씀
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
    _log = open(DATA_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout, sys.stderr = sys.stdout or _log, sys.stderr or _log

XP_PER_LEVEL = 50
MASTER_LV, MAX_LV = 36, 100   # Lv.36 = 완전 성장 (자랑 카드·졸업)
EVO_LEVELS = {1: [], 2: [20], 3: [16, 36]}   # 진화 단계 수별 진화 레벨

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
                      "MapleStory", "LOSTARK", "Client-Win64-Shipping"],
    "distract_domains": ["youtube.com", "youtu.be", "instagram.com", "tiktok.com",
                         "netflix.com", "twitch.tv", "chzzk.naver.com", "x.com",
                         "twitter.com", "facebook.com", "afreecatv.com", "sooplive.co.kr"],
    "xp_per_work_min": 1.0,      # 작업 1분당 경험치
    "break_earn_ratio": 5.0,     # 작업 N분 = 휴식 1분
    "bank_max_min": 60.0,        # 휴식 잔고 최대치
    "penalty_xp_per_min": 3.0,   # 잔고 없이 딴짓할 때 분당 감소 경험치
    "grace_sec": 20,             # 경고 후 페널티까지 유예
    "idle_limit_sec": 120,       # 이만큼 입력이 없으면 작업 시간에서 제외
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
    ("41", "poison", [(41, "주뱃"), (42, "골뱃")]),
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
    ("81", "electric", [(81, "코일"), (82, "레어코일")]),
    ("83", "normal", [(83, "파오리")]),
    ("84", "normal", [(84, "두두"), (85, "두트리오")]),
    ("86", "water", [(86, "쥬쥬"), (87, "쥬레곤")]),
    ("88", "poison", [(88, "질퍽이"), (89, "질뻐기")]),
    ("90", "water", [(90, "셀러"), (91, "파르셀")]),
    ("92", "ghost", [(92, "고오스"), (93, "고우스트"), (94, "팬텀")]),
    ("95", "rock", [(95, "롱스톤")]),
    ("96", "psychic", [(96, "슬리프"), (97, "슬리퍼")]),
    ("98", "water", [(98, "크랩"), (99, "킹크랩")]),
    ("100", "electric", [(100, "찌리리공"), (101, "붐볼")]),
    ("102", "grass", [(102, "아라리"), (103, "나시")]),
    ("104", "ground", [(104, "탕구리"), (105, "텅구리")]),
    ("106", "fighting", [(106, "시라소몬")]),
    ("107", "fighting", [(107, "홍수몬")]),
    ("108", "normal", [(108, "내루미")]),
    ("109", "poison", [(109, "또가스"), (110, "또도가스")]),
    ("111", "ground", [(111, "뿔카노"), (112, "코뿌리")]),
    ("113", "normal", [(113, "럭키")]),
    ("114", "grass", [(114, "덩쿠리")]),
    ("115", "normal", [(115, "캥카")]),
    ("116", "water", [(116, "쏘드라"), (117, "시드라")]),
    ("118", "water", [(118, "콘치"), (119, "왕콘치")]),
    ("120", "water", [(120, "별가사리"), (121, "아쿠스타")]),
    ("122", "psychic", [(122, "마임맨")]),
    ("123", "bug", [(123, "스라크")]),
    ("124", "ice", [(124, "루주라")]),
    ("125", "electric", [(125, "에레브")]),
    ("126", "fire", [(126, "마그마")]),
    ("127", "bug", [(127, "쁘사이저")]),
    ("128", "normal", [(128, "켄타로스")]),
    ("129", "water", [(129, "잉어킹"), (130, "갸라도스")]),
    ("131", "water", [(131, "라프라스")]),
    ("132", "normal", [(132, "메타몽")]),
    ("133-water", "water", [(133, "이브이"), (134, "샤미드")]),
    ("133-electric", "electric", [(133, "이브이"), (135, "쥬피썬더")]),
    ("133-fire", "fire", [(133, "이브이"), (136, "부스터")]),
    ("137", "normal", [(137, "폴리곤")]),
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
LINES = {str(st[0][0]): {"gen": g, "type": t, "stages": [list(x) for x in st], "starter": True} for g, t, st in _RAW}
LINES.update({k: {"gen": 1, "type": t, "stages": [list(x) for x in st], "starter": False} for k, t, st in _GEN1})
for _l in LINES.values():
    _l["evo_levels"] = [0] + EVO_LEVELS[len(_l["stages"])]   # 각 단계가 시작되는 레벨

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
        "summon": 0, "summon_label": "", "last_work_at": 0.0,
        "distract_since": None, "penalty_since": None, "last_dialog": 0.0}


# ---------- 레벨 계산 ----------
def level_of(xp):
    return min(MAX_LV, int(xp // XP_PER_LEVEL))


def stage_of(lv, line="1"):
    """지금 몇 번째 모습인지 (1부터)"""
    return sum(1 for need in LINES[line]["evo_levels"] if lv >= need)


def stage_floor_xp(stage, line="1"):
    return LINES[line]["evo_levels"][stage - 1] * XP_PER_LEVEL


def species_name(pet, stage):
    return LINES[pet["line"]]["stages"][stage - 1][1]


# ---------- 상태 저장 ----------
def fresh_state():
    return {"config": dict(DEFAULT_CONFIG), "pet": None, "bank_sec": 0.0, "paused_until": 0.0,
            "total_work_sec": 0.0, "dex": [], "events": [], "today": new_today()}


def new_today():
    return {"date": date.today().isoformat(), "work_sec": 0.0, "distract_sec": 0.0, "xp_lost": 0.0, "ai_sec": 0.0}


def load_state():
    s = fresh_state()
    if STATE_FILE.exists():
        try:
            s.update(json.loads(STATE_FILE.read_text()))
            s["config"] = {**DEFAULT_CONFIG, **s.get("config", {})}
            s["today"] = {**new_today(), **s["today"]}
        except Exception as e:
            print("상태 파일을 읽지 못해 새로 시작합니다:", e)
    return s


def save():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, ensure_ascii=False))
    tmp.replace(STATE_FILE)


STATE = load_state()


def log(kind, msg):
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
    asn = sh(["lsappinfo", "front"])
    if not asn:
        return ""
    m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', sh(["lsappinfo", "info", "-only", "name", asn]))
    return m.group(1) if m else ""


def browser_url(app):
    kind = BROWSERS.get(app)
    if not kind:
        return ""
    prop = "URL of current tab of front window" if kind == "safari" else "URL of active tab of front window"
    return sh(["osascript", "-e", f'tell application "{app}" to get {prop}'])


def idle_seconds():
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
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            name = Path(path.value).stem
        kernel32.CloseHandle(h)
    return name, buf.value, hwnd


def win_idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    user32.GetLastInputInfo(ctypes.byref(li))
    return ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def sample():
    """지금 앞에 있는 (앱 이름, 탭 주소, 창 제목, 입력 없는 초)"""
    if IS_WIN:
        app, title, hwnd = win_front()
        LIVE["front_hwnd"] = hwnd
        return app, "", title, win_idle_seconds()
    app = front_app()
    return app, browser_url(app), "", idle_seconds()


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


def classify(app, url, cfg, title=""):
    if title and app.lower() in WIN_BROWSERS:
        return classify_title(title, cfg)
    host = re.sub(r"^[a-z]+://", "", url.lower()).split("/")[0].split(":")[0] if url else ""
    dom = lambda lst: bool(host) and any(host == d or host.endswith("." + d) for d in lst)
    # 앱 이름은 정확히 같거나 앞부분이 같으면 인정 ("IntelliJ IDEA" ↔ "IntelliJ IDEA CE")
    appm = lambda lst: bool(app) and any(a and app.lower().startswith(a.lower()) for a in lst)
    label = host or app
    # AI를 먼저 본다 ("Codex"가 작업 앱 "Code"로 잡히지 않게). AI 절제 모드가 꺼져 있으면 step()에서 neutral 처리
    if url:  # 브라우저는 앱 이름이 아니라 열린 사이트로 판단
        for kind in ("ai", "work", "distract"):
            if dom(cfg[kind + "_domains"]):
                return kind, label
        return "neutral", label
    for kind in ("ai", "work", "distract"):
        if appm(cfg[kind + "_apps"]):
            return kind, label
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
    if s["today"]["date"] != date.today().isoformat():
        s["today"] = new_today()
    kind, label = classify(app, url, cfg, title)
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
        if not cfg["ai_limit_enabled"]:
            kind = "neutral"
        else:
            s["today"]["ai_sec"] += dt
            ai_over = s["today"]["ai_sec"] > cfg["ai_daily_free_min"] * 60
            if ai_over:
                kind = "distract"
    LIVE.update(app=app, label=label, idle=int(idle))
    pet = s["pet"]
    before = level_of(pet["xp"]) if pet else None
    name = species_name(pet, stage_of(before, pet["line"])) if pet else "포켓몬"

    if kind != "distract":
        LIVE["distract_since"] = LIVE["penalty_since"] = None

    if kind == "work":
        mode = "work"
        s["today"]["work_sec"] += dt
        s["total_work_sec"] += dt
        s["bank_sec"] = min(cfg["bank_max_min"] * 60, s["bank_sec"] + dt / max(cfg["break_earn_ratio"], 0.1))
        if pet:
            pet["xp"] += dt / 60 * cfg["xp_per_work_min"]
            pet["work_sec"] += dt
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
                if pet:
                    loss = dt / 60 * cfg["penalty_xp_per_min"]
                    floor = stage_floor_xp(stage_of(level_of(pet["xp"]), pet["line"]), pet["line"])
                    loss = min(loss, max(0.0, pet["xp"] - floor))
                    pet["xp"] -= loss
                    s["today"]["xp_lost"] += loss
            else:
                mode = "warning"
    elif kind == "ai":
        mode = "ai"
    elif kind == "idle":
        mode = "idle"
    else:
        mode = "neutral"

    if mode == "penalty" and LIVE["mode"] != "penalty":
        log("penalty", f"페널티 시작! {label} 때문에 경험치가 줄고 있어요")
    LIVE["mode"] = mode

    if pet:
        after = level_of(pet["xp"])
        if after != before:
            sb, sa = stage_of(before, pet["line"]), stage_of(after, pet["line"])
            if sa > sb:
                msg = f"{species_name(pet, sb)} → {species_name(pet, sa)} 진화!"
                log("evo", msg)
                notify("✨ 진화!", msg)
            if before < MASTER_LV <= after:
                log("grad", f"Lv.{MASTER_LV} 완전 성장! 자랑 카드를 만들 수 있어요")
                notify("🏆 완전 성장", "Figmon 창에서 자랑 카드를 만들어 자랑해보세요!")
            elif sa > sb:
                pass
            elif after > before:
                log("lvup", f"Lv.{after} 달성!")
            else:
                log("lvdown", f"Lv.{after}로 떨어졌어요…")
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
           "events": s["events"][-25:][::-1], "live": live, "speed": SPEED, "pet": None, "version": VERSION}
    pet = s["pet"]
    if pet:
        xp = pet["xp"]
        lv = level_of(xp)
        line = LINES[pet["line"]]
        st = stage_of(lv, pet["line"])
        lv_list = line["evo_levels"]
        nxt = lv_list[st] if st < len(lv_list) else None
        rate = max(cfg["xp_per_work_min"], 0.01)
        out["pet"] = {**pet, "level": lv, "stage": st, "type": line["type"], "gen": line["gen"],
                      "stages": line["stages"], "species": line["stages"][st - 1],
                      "name": pet.get("nickname") or species_name(pet, st),
                      "lv_progress": 1 if lv >= MAX_LV else (xp % XP_PER_LEVEL) / XP_PER_LEVEL,
                      "evo_levels": lv_list, "final": st == len(line["stages"]),
                      "mastered": lv >= MASTER_LV, "master_lv": MASTER_LV,
                      "next_evo_lv": nxt,
                      "min_to_evo": (nxt * XP_PER_LEVEL - xp) / rate if nxt else None,
                      "min_to_master": max(0.0, (MASTER_LV * XP_PER_LEVEL - xp) / rate)}
    return out


def apply_config(body):
    cfg = STATE["config"]
    for k, v in body.items():
        if k not in DEFAULT_CONFIG:
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
        elif self.path == "/api/state":
            with LOCK:
                self._send(200, view())
        elif self.path == "/api/meta":
            self._send(200, {"lines": LINES, "master_lv": MASTER_LV,
                             "xp_per_level": XP_PER_LEVEL, "presets": PRESETS,
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
                old = s["pet"]
                s["pet"] = {"line": str(body["line"]), "xp": 0.0, "work_sec": 0.0,
                            "nickname": "", "born": time.time()}
                new = LINES[s["pet"]["line"]]["stages"][0][1]
                if old:
                    log("hatch", f"{species_name(old, stage_of(level_of(old['xp']), old['line']))}와(과) 작별… {new}와(과) Lv.0부터 새 출발!")
                else:
                    log("hatch", f"{new}와(과) 함께 시작! 작업할수록 같이 자라요")
            elif self.path == "/api/nickname" and s["pet"]:
                s["pet"]["nickname"] = str(body.get("nickname", ""))[:12]
            elif self.path == "/api/graduate":
                pet = s["pet"]
                if not pet or level_of(pet["xp"]) < MASTER_LV:
                    return self._send(400, {"error": f"Lv.{MASTER_LV} 완전 성장 후에 졸업할 수 있어요"})
                lv = level_of(pet["xp"])
                st = stage_of(lv, pet["line"])
                s["dex"].append({**pet, "level": lv, "species": LINES[pet["line"]]["stages"][st - 1],
                                 "finished": time.time()})
                log("grad", f"{species_name(pet, st)} Lv.{lv} 도감에 등록! 새 파트너를 골라주세요")
                s["pet"] = None
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
            save()
            self._send(200, view())


def newer(a, b):
    """버전 a가 b보다 새 버전인지 ("1.10.0" > "1.9.2")"""
    num = lambda v: [int(x) for x in re.findall(r"\d+", v)[:3]]
    return num(a) > num(b)


def latest_release():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "Figmon"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def win_update(manual=False):
    """Windows exe 자동 업데이트: 새 Figmon.exe를 받아 두고, 종료 뒤 바꿔 끼운 다음 다시 실행. 업데이트를 시작하면 True."""
    if not (IS_WIN and getattr(sys, "frozen", False)):
        if manual:
            notify("업데이트", "소스로 실행 중이면 git pull로 업데이트해 주세요")
        return False
    try:
        rel = latest_release()
        tag = rel.get("tag_name", "")
        if not newer(tag, VERSION):
            if manual:
                notify("최신 버전이에요", f"Figmon v{VERSION}")
            return False
        asset = next(a for a in rel.get("assets", []) if a["name"] == "Figmon-Windows.zip")
        tmp = Path(tempfile.mkdtemp(prefix="figmon-update-"))
        zpath = tmp / "update.zip"
        zpath.write_bytes(urllib.request.urlopen(asset["browser_download_url"], timeout=120).read())
        with zipfile.ZipFile(zpath) as z:
            z.extract("Figmon.exe", tmp)
        exe = Path(sys.executable)
        script = tmp / "update.cmd"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n"
            f"move /Y \"{tmp / 'Figmon.exe'}\" \"{exe}\" >nul\r\n"
            f"start \"\" \"{exe}\" --startup --updated\r\n", encoding="mbcs")  # cmd는 시스템 코드 페이지로 읽음 (한글 사용자 이름 대비)
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x08000000 | 0x00000008)  # 창 없이, 분리 실행
        log("update", f"v{tag.lstrip('v')} 업데이트를 설치하고 다시 시작해요")
        with LOCK:
            save()
        os._exit(0)
    except Exception as e:
        print("update check failed:", e)
        if manual:
            notify("업데이트 확인 실패", "인터넷 연결을 확인해 주세요")
    return False


def update_loop():
    time.sleep(float(os.environ.get("FIGMON_UPDATE_DELAY", 20)))
    while True:
        if STATE["config"].get("auto_update", True):
            win_update()
        time.sleep(6 * 3600)


def launch_pet():
    """개발용: build/FigmonPet(Figmon.swift를 컴파일한 것)이 있으면 펫 창으로 함께 띄운다."""
    exe = HERE / "build" / "FigmonPet"
    if not exe.exists():
        return None
    env = {**os.environ, "FIGMON_PORT": str(PORT), "FIGMON_PARENT": str(os.getpid())}
    return subprocess.Popen([str(exe)], env=env)


def open_window(title=True):
    url = f"http://localhost:{PORT}/" + ("?title=1" if title else "")  # 실행할 때는 타이틀 화면부터
    if IS_WIN:  # Edge 앱 창 (Windows 10/11 기본 설치)
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


if __name__ == "__main__":
    quiet = "--no-open" in sys.argv or "--startup" in sys.argv or "--updated" in sys.argv  # 로그인 자동 실행·업데이트 후엔 창 안 띄움
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 고해상도 화면에서 흐려지지 않게
        except Exception:
            pass
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
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
                save()
        sys.exit(0)
    pet_proc = None if "--no-pet" in sys.argv else launch_pet()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        with LOCK:
            save()
    finally:
        if pet_proc:
            pet_proc.terminate()
