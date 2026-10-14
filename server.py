#!/usr/bin/env python3
"""
Figmon — 작업 시간으로 포켓몬을 키우는 macOS 데스크톱 펫.

- Figma, VS Code, Xcode, 터미널 같은 작업 앱(또는 figma.com, github.com)을 쓰면 경험치와 휴식 잔고가 쌓인다.
- 유튜브/인스타/게임 등 딴짓을 하면 휴식 잔고가 줄어든다.
  잔고가 바닥났는데 계속하면 경험치가 깎인다(진화 단계 아래로는 안 떨어짐).
- (선택) AI 절제 모드: ChatGPT/Claude 등은 하루 무료 시간만큼만, 넘기면 딴짓 취급.

실행:  python3 server.py            (http://localhost:7777)
테스트: FIGMON_SPEED=60 python3 server.py   (1초 = 1분으로 빨리 감기)
"""
import json
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

PORT = int(os.environ.get("FIGMON_PORT", 7777))
SPEED = float(os.environ.get("FIGMON_SPEED", 1))
TICK = 3  # 초
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FIGMON_HOME", Path.home() / ".figmon"))
STATE_FILE = DATA_DIR / "state.json"

XP_PER_LEVEL = 50
EVO1_LV, EVO2_LV, MAX_LV = 16, 36, 100  # Lv.0 기본형 → Lv.16 → Lv.36 최종

DESIGN_APPS = ["Figma", "Sketch", "Framer", "Adobe Illustrator", "Adobe Photoshop", "Adobe XD", "Penpot"]
DEV_APPS = ["Code", "Visual Studio Code", "Cursor", "Windsurf", "Zed", "Xcode", "Android Studio",
            "IntelliJ IDEA", "PyCharm", "WebStorm", "GoLand", "Rider", "CLion", "PhpStorm", "RubyMine",
            "DataGrip", "Fleet", "Sublime Text", "Nova", "Terminal", "iTerm2", "Warp", "Ghostty",
            "Alacritty", "kitty", "WezTerm", "Docker Desktop", "Postman", "Insomnia", "TablePlus",
            "Simulator", "GitHub Desktop", "Fork", "Tower", "Sourcetree"]
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
                      "Roblox", "Genshin Impact", "Hearthstone", "Overwatch"],
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
LINES = {str(st[0][0]): {"gen": g, "type": t, "stages": [list(x) for x in st]} for g, t, st in _RAW}

BROWSERS = {
    "Google Chrome": "chromium", "Arc": "chromium", "Brave Browser": "chromium",
    "Microsoft Edge": "chromium", "Whale": "chromium", "Naver Whale": "chromium",
    "Chromium": "chromium", "Vivaldi": "chromium", "Opera": "chromium",
    "Safari": "safari", "Safari Technology Preview": "safari",
}

LOCK = threading.Lock()
LIVE = {"mode": "neutral", "app": "", "label": "", "idle": 0, "last_work": None,
        "distract_since": None, "penalty_since": None, "last_dialog": 0.0}


# ---------- 레벨 계산 ----------
def level_of(xp):
    return min(MAX_LV, int(xp // XP_PER_LEVEL))


def stage_of(lv):
    return 1 if lv < EVO1_LV else 2 if lv < EVO2_LV else 3


def stage_floor_xp(stage):
    return [0, 0, EVO1_LV, EVO2_LV][stage] * XP_PER_LEVEL


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
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
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


def classify(app, url, cfg):
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


def notify(title, msg):
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
def step(app, url, idle, dt):
    s, cfg, now = STATE, STATE["config"], time.time()
    if s["today"]["date"] != date.today().isoformat():
        s["today"] = new_today()
    kind, label = classify(app, url, cfg)
    if now < s.get("paused_until", 0):  # 일시정지 중엔 아무것도 쌓거나 깎지 않음
        LIVE.update(app=app, label=label, idle=int(idle), mode="paused", distract_since=None, penalty_since=None)
        save()
        return
    if kind == "work" and idle > cfg["idle_limit_sec"]:
        kind = "idle"
    if kind == "work":
        LIVE["last_work"] = ("url", url) if url else ("app", app)
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
    name = species_name(pet, stage_of(before)) if pet else "포켓몬"

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
                    floor = stage_floor_xp(stage_of(level_of(pet["xp"])))
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
            sb, sa = stage_of(before), stage_of(after)
            if sa > sb:
                msg = f"{species_name(pet, sb)} → {species_name(pet, sa)} 진화!"
                log("evo", msg)
                notify("✨ 진화!", msg)
                if sa == 3:
                    notify("🏆 최종 진화 완료", "Figmon 창에서 공유 카드를 만들어 자랑해보세요!")
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
        app = front_app()
        url = browser_url(app)
        idle = idle_seconds()
        with LOCK:
            try:
                step(app, url, idle, dt)
            except Exception as e:
                print("tick error:", e, file=sys.stderr)


# ---------- API ----------
def view():
    s, cfg = STATE, STATE["config"]
    live = {k: LIVE[k] for k in ("mode", "app", "label", "idle")}
    if LIVE["penalty_since"]:
        live["grace_left"] = max(0, cfg["grace_sec"] - (time.time() - LIVE["penalty_since"]))
    out = {"config": cfg, "bank_sec": s["bank_sec"], "today": s["today"], "paused_until": s.get("paused_until", 0),
           "total_work_sec": s["total_work_sec"], "dex": s["dex"],
           "events": s["events"][-25:][::-1], "live": live, "speed": SPEED, "pet": None}
    pet = s["pet"]
    if pet:
        xp = pet["xp"]
        lv = level_of(xp)
        st = stage_of(lv)
        line = LINES[pet["line"]]
        nxt = [None, EVO1_LV, EVO2_LV][st] if st < 3 else None
        out["pet"] = {**pet, "level": lv, "stage": st, "type": line["type"], "gen": line["gen"],
                      "stages": line["stages"], "species": line["stages"][st - 1],
                      "name": pet.get("nickname") or species_name(pet, st),
                      "lv_progress": 1 if lv >= MAX_LV else (xp % XP_PER_LEVEL) / XP_PER_LEVEL,
                      "next_evo_lv": nxt,
                      "min_to_evo": (nxt * XP_PER_LEVEL - xp) / max(cfg["xp_per_work_min"], 0.01) if nxt else None}
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
            self._send(200, {"lines": LINES, "levels": [0, EVO1_LV, EVO2_LV],
                             "xp_per_level": XP_PER_LEVEL, "presets": PRESETS})
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
                    log("hatch", f"{species_name(old, stage_of(level_of(old['xp'])))}와(과) 작별… {new}와(과) Lv.0부터 새 출발!")
                else:
                    log("hatch", f"{new}와(과) 함께 시작! 작업할수록 같이 자라요")
            elif self.path == "/api/nickname" and s["pet"]:
                s["pet"]["nickname"] = str(body.get("nickname", ""))[:12]
            elif self.path == "/api/graduate":
                pet = s["pet"]
                if not pet or stage_of(level_of(pet["xp"])) < 3:
                    return self._send(400, {"error": "최종 진화 후에 졸업할 수 있어요"})
                lv = level_of(pet["xp"])
                s["dex"].append({**pet, "level": lv, "species": LINES[pet["line"]]["stages"][2],
                                 "finished": time.time()})
                log("grad", f"{species_name(pet, 3)} Lv.{lv} 도감에 등록! 새 파트너를 골라주세요")
                s["pet"] = None
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


def launch_pet():
    """개발용: build/FigmonPet(Figmon.swift를 컴파일한 것)이 있으면 펫 창으로 함께 띄운다."""
    exe = HERE / "build" / "FigmonPet"
    if not exe.exists():
        return None
    env = {**os.environ, "FIGMON_PORT": str(PORT), "FIGMON_PARENT": str(os.getpid())}
    return subprocess.Popen([str(exe)], env=env)


def open_window():
    url = f"http://localhost:{PORT}/?title=1"  # 실행할 때마다 타이틀 화면부터
    if Path("/Applications/Google Chrome.app").exists():
        subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=460,860"])
    else:
        webbrowser.open(url)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Figmon 실행 중 → http://localhost:{PORT}  (속도 x{SPEED:g}, 종료: Ctrl+C)")
    pet_proc = None if "--no-pet" in sys.argv else launch_pet()
    if "--no-open" not in sys.argv:
        open_window()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        with LOCK:
            save()
    finally:
        if pet_proc:
            pet_proc.terminate()
