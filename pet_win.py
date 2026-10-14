"""
Figmon Windows 데스크톱 펫 (tkinter, 표준 라이브러리만 사용).

server.py가 Windows에서 메인 스레드로 실행한다(macOS는 Figmon.swift가 같은 역할).
서버의 /api/state를 읽어서 작업 표시줄 위를 걸어 다니고, 작업 중엔 커서 옆에서 지켜보고,
딴짓하면 커서로 달려와 말린다. 투명색(KEY)으로 칠한 부분은 클릭이 뒤 창으로 통과한다.
"""
import json
import math
import os
import random
import struct
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import ctypes
    from ctypes import wintypes

KEY = "#ff00fe"  # 이 색은 투명 + 클릭 통과
RAW = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
CACHE = Path(os.environ.get("FIGMON_HOME", Path.home() / ".figmon")) / "sprites"
FONT = ("Malgun Gothic", 10, "bold")

LINES = {
    "work": ["잘 되고 있어! ✨", "집중력 최고야", "옆에서 지켜보고 있을게 👀", "같이 크는 중!", "이 속도면 곧 진화해!", "물 한 잔 마셔가며 해!"],
    "neutral": ["뭐 하는 중이야?", "작업 시작하면 같이 크자!", "심심해~"],
    "ai": ["AI 찬스 쓰는 중!", "너무 의존하진 말자~", "네 머리도 믿어봐 🧠"],
    "break": ["잠깐 쉬는 것도 좋아 🍿", "휴식 잔고 쓰는 중~", "잔고 다 쓰면 돌아가자!"],
    "penalty": ["경험치가 줄고 있어!! 😭", "작업으로 돌아가줘…", "레벨 떨어진단 말이야!"],
    "idle": ["Zzz…", "쿨쿨… 돌아오면 깨워줘"],
    "paused": ["일시정지 중 ⏸ 편하게 다녀와!", "기다리고 있을게~"],
}
PARTICLE = {"work": ("✦", "#f5c518"), "break": ("♪", "#ff8a3d"), "penalty": ("!", "#e5484d"),
            "warning": ("!", "#ff9f1a"), "idle": ("z", "#8a90a8"), "ai": ("?", "#7d8cff")}


# ---------- 시스템 ----------
def work_area(root):
    """작업 표시줄을 뺀 화면 영역 (left, top, right, bottom)"""
    if IS_WIN:
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 80


def animations_on():
    """Windows 설정 › 접근성 › 시각 효과 › 애니메이션 효과"""
    if not IS_WIN:
        return True
    v = wintypes.BOOL(True)
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(v.value)


def startup_file():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Figmon.vbs"


def launch_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else sys.executable}" "{Path(sys.argv[0]).resolve()}" --startup'


def gif_disposals(path):
    """GIF 각 프레임의 disposal 값 (1=그대로 둠, 2=배경으로 지움). 스프라이트가 변경분만 담고 있어서 직접 합성해야 한다."""
    d = path.read_bytes()
    i, flags, out, disp = 13, d[10], [], 0
    if flags & 0x80:
        i += 3 * (2 ** ((flags & 7) + 1))
    while i < len(d):
        b = d[i]
        if b == 0x21:
            label = d[i + 1]
            i += 2
            if label == 0xF9:
                disp = (d[i + 1] >> 2) & 7
            while d[i]:
                i += d[i] + 1
            i += 1
        elif b == 0x2C:
            l, t, w, h = struct.unpack("<HHHH", d[i + 1:i + 9])
            pf = d[i + 9]
            i += 10
            if pf & 0x80:
                i += 3 * (2 ** ((pf & 7) + 1))
            i += 1
            while d[i]:
                i += d[i] + 1
            i += 1
            out.append((disp, l, t, w, h))
            disp = 0
        else:
            break
    return out


def fetch_sprite(pid):
    """애니메이션 GIF(없으면 정지 PNG)를 받아 캐시 경로를 돌려준다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in ((f"{pid}.gif", f"{RAW}/other/showdown/{pid}.gif"), (f"{pid}.png", f"{RAW}/{pid}.png")):
        p = CACHE / name
        if p.exists() and p.stat().st_size > 0:
            return p
        try:
            p.write_bytes(urllib.request.urlopen(url, timeout=15).read())
            return p
        except Exception:
            continue
    return None


# ---------- 펫 ----------
class Pet:
    def __init__(self, port, open_dashboard):
        self.base = f"http://127.0.0.1:{port}"
        self.open_dashboard = open_dashboard
        self.root = root = tk.Tk()
        root.title("Figmon")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass  # Windows 외에서는 투명 처리 없이(테스트용)
        root.configure(bg=KEY)
        if IS_WIN:  # 눌러도 포커스를 뺏지 않는 창 + 작업 표시줄/Alt+Tab에 안 보이게 (작업 감지가 끊기지 않도록)
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        self.dpi = max(1.0, root.winfo_fpixels("1i") / 96)
        self.W, self.H = int(300 * self.dpi), int(300 * self.dpi)
        self.cv = tk.Canvas(root, width=self.W, height=self.H, bg=KEY, highlightthickness=0, bd=0)
        self.cv.pack()
        self.area = work_area(root)
        self.area_at = time.time()
        self.calm = not animations_on()
        self.x = self.area[2] - self.W - 60
        self.y = self.area[3] - self.H

        self.new_state = self.state = None
        self.online = True
        self.mode = "neutral"
        self.prev = None
        self.frames, self.cur, self.cur_key, self.frame_i = {}, None, None, 0
        self.pending = None
        self.loading = None
        self.facing, self.walking = -1, False
        self.target, self.speed, self.rest_until = None, 60.0, 0.0
        self.drag, self.falling, self.vy = None, False, 0.0
        self.evolving_until = 0.0
        self.bubble_until = None
        self.floats = []
        self.t0 = self.last = time.time()
        self.next_particle = 0.0
        self.next_chatter = time.time() + 20

        self.img_item = self.cv.create_image(self.W // 2, self.H - 6, anchor="s")
        self.cv.bind("<ButtonPress-1>", self.on_down)
        self.cv.bind("<B1-Motion>", self.on_move)
        self.cv.bind("<ButtonRelease-1>", self.on_up)
        self.cv.bind("<Button-3>", self.on_menu)
        self.place()

        threading.Thread(target=self.poll_loop, daemon=True).start()
        root.after(16, self.tick)
        root.after(90, self.animate)
        root.after(300, self.apply_state)

    # ----- 서버 -----
    def poll_loop(self):
        while True:
            try:
                with urllib.request.urlopen(self.base + "/api/state", timeout=3) as r:
                    self.new_state = json.loads(r.read())
                    self.online = True
            except Exception:
                self.online = False
            time.sleep(1.5)

    def post(self, path, body):
        def run():
            req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=3).read()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    # ----- 상태 반영 (메인 스레드) -----
    def apply_state(self):
        s = self.new_state
        if not self.online:
            if self.online is not None and self.bubble_until != 0:
                self.say("Figmon 서버가 꺼져 있어… 💤", 0)
        elif s:
            self.state = s
            p, m = s["pet"], s["live"]["mode"]
            if m != self.mode:
                alert = m in ("warning", "penalty")
                self.say(self.mode_line(m), 0 if alert else 4.5, alert)
                if alert:
                    self.target, self.rest_until = None, 0
                self.mode = m
            elif m == "warning":
                self.say(self.mode_line(m), 0, True)

            if p and self.prev and self.prev["line"] == p["line"] and time.time() > self.evolving_until:
                if p["stage"] > self.prev["stage"]:
                    self.evolving_until = time.time() + 2.6
                    self.say("…어라? 몸이 빛나고 있어!", 2.6)
                    name = p["species"][1]
                    self.root.after(2700, lambda: (self.say(f"✨ {name}(으)로 진화했다!", 6), self.burst()))
                elif p["level"] > self.prev["level"]:
                    self.say(f"레벨 업! Lv.{p['level']} 🎉", 3)
                    self.float_text(f"Lv.{p['level']}!", "#e0a800")
                elif p["level"] < self.prev["level"]:
                    self.say(f"Lv.{p['level']}로 떨어졌어… 😢", 4, True)
                if self.prev["xp"] - p["xp"] > 0.05:
                    self.float_text(f"-{self.prev['xp'] - p['xp']:.1f} XP", "#e5484d")
            self.prev = {"line": p["line"], "stage": p["stage"], "level": p["level"], "xp": p["xp"]} if p else None

            key = (p["species"][0], self.zoom_for(p, s)) if p else None
            if key != self.cur_key and key != self.loading and time.time() > self.evolving_until:
                self.load_sprite(key)
            if not p and self.bubble_until is None:
                self.say("대시보드에서 파트너를 골라줘! (나를 클릭)", 0)
            now = time.time()
            if p and now > self.next_chatter and m not in ("warning", "penalty"):
                self.next_chatter = now + random.uniform(20, 35)
                self.say(self.status_line() if random.random() < .3 else self.mode_line(m), 4.5)
        if self.pending:
            key, path = self.pending
            self.pending = None
            self.build_frames(key, path)
        self.root.after(300, self.apply_state)

    def zoom_for(self, p, s):
        lv0, lv1 = [(0, 16), (16, 36), (36, 100)][p["stage"] - 1]
        t = min(1, (p["level"] - lv0) / (lv1 - lv0))
        scale = [1.25, 1.55, 1.85][p["stage"] - 1] * (1 + 0.18 * t) * float(s["config"].get("pet_size", 1)) * self.dpi
        return max(1, round(scale * 1.4))

    def mode_line(self, m):
        if m == "warning":
            return f"{math.ceil(self.state['live'].get('grace_left') or 0)}초 안에 돌아가! ⚠️"
        return random.choice(LINES.get(m, LINES["neutral"]))

    def status_line(self):
        p = self.state["pet"]
        mins = p.get("min_to_evo")
        left = "최종 진화 완료!" if mins is None else f"진화까지 {int(mins // 60)}시간 {int(mins % 60)}분"
        return f"{p['name']} Lv.{p['level']} · {left}"

    # ----- 스프라이트 -----
    def load_sprite(self, key):
        if key is None:
            self.cur, self.cur_key = None, None
            self.cv.itemconfigure(self.img_item, image="")
            self.draw_ball()
            return
        if key in self.frames:
            self.cur, self.cur_key = self.frames[key], key
            return
        self.loading = key
        threading.Thread(target=lambda: setattr(self, "pending", (key, fetch_sprite(key[0]))), daemon=True).start()

    def build_frames(self, key, path):
        self.loading = None
        if path is None:
            return
        pid, z = key
        frames = []
        try:
            if path.suffix == ".gif":
                info = gif_disposals(path)
                first = tk.PhotoImage(file=str(path), format="gif -index 0")
                w, h = first.width(), first.height()
                canvas, blank = tk.PhotoImage(width=w, height=h), tk.PhotoImage(width=w, height=h)
                for i, (disp, l, t, fw, fh) in enumerate(info[:150]):
                    f = first if i == 0 else tk.PhotoImage(file=str(path), format=f"gif -index {i}")
                    canvas.tk.call(str(canvas), "copy", str(f), "-compositingrule", "overlay")
                    snap = tk.PhotoImage(width=w, height=h)
                    snap.tk.call(str(snap), "copy", str(canvas), "-compositingrule", "set")
                    frames.append(snap)
                    if disp == 2:  # 이 프레임 영역을 투명하게 되돌림
                        canvas.tk.call(str(canvas), "copy", str(blank), "-from", l, t, l + fw, t + fh,
                                       "-to", l, t, "-compositingrule", "set")
            else:
                frames = [tk.PhotoImage(file=str(path))]
        except tk.TclError:
            frames = frames or []
        if not frames:
            return
        left = [f.zoom(z) if z > 1 else f for f in frames]
        right = []
        for f in left:
            m = tk.PhotoImage()
            m.tk.call(str(m), "copy", str(f), "-subsample", -1, 1)  # 좌우 반전
            right.append(m)
        self.frames[key] = {"L": left, "R": right}
        self.cur, self.cur_key = self.frames[key], key
        self.cv.delete("ball")
        self.frame_i = 0

    def draw_ball(self):
        self.cv.delete("ball")
        cx, by = self.W // 2, self.H - 8
        r = 26 * self.dpi
        self.cv.create_oval(cx - r, by - 2 * r, cx + r, by, fill="#fdf8ec", outline="#c9c2b0", width=2, tags="ball")
        self.cv.create_text(cx, by - r, text="?", font=("Malgun Gothic", int(16 * self.dpi), "bold"), fill="#a9a39a", tags="ball")

    def animate(self):
        if self.cur:
            seq = self.cur["R" if self.facing > 0 else "L"]
            self.frame_i = (self.frame_i + 1) % len(seq)
            self.cv.itemconfigure(self.img_item, image=seq[self.frame_i])
        self.root.after(70, self.animate)

    def sprite_top(self):
        if self.cur:
            return self.H - 6 - self.cur["L"][0].height()
        return self.H - 8 - 52 * self.dpi

    # ----- 말풍선 / 효과 -----
    def say(self, text, secs=4.0, alert=False):
        self.cv.delete("bubble")
        bg, fg = ("#ff5d6c", "#ffffff") if alert else ("#ffffff", "#222222")
        y = max(40, self.sprite_top() - 14)
        t = self.cv.create_text(self.W // 2, y, text=text, font=FONT, fill=fg, width=self.W - 40,
                                justify="center", anchor="s", tags="bubble")
        x1, y1, x2, y2 = self.cv.bbox(t)
        x1, y1, x2, y2 = x1 - 10, y1 - 6, x2 + 10, y2 + 6
        r = 10
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        box = self.cv.create_polygon(pts, smooth=True, fill=bg, outline="#d0d0d0" if not alert else bg, tags="bubble")
        cx = self.W // 2
        tail = self.cv.create_polygon(cx - 7, y2 - 1, cx + 7, y2 - 1, cx, y2 + 8, fill=bg, outline=bg, tags="bubble")
        self.cv.tag_raise(t)
        self.cv.tag_lower(box, t)
        self.cv.tag_lower(tail, t)
        self.bubble_until = time.time() + secs if secs else 0

    def float_text(self, text, color):
        item = self.cv.create_text(self.W // 2, self.sprite_top() + 10, text=text, fill=color,
                                   font=("Malgun Gothic", int(12 * self.dpi), "bold"))
        self.floats.append([item, time.time(), 1.5])

    def burst(self):
        for i in range(6):
            self.root.after(i * 120, lambda: self.float_text("✦", "#f5c518"))

    def hop(self):
        self.hop_until = time.time() + 0.5

    # ----- 마우스 -----
    def on_down(self, e):
        self.drag = {"ox": e.x_root - self.x, "oy": e.y_root - self.y, "dist": 0.0, "last": (e.x_root, e.y_root), "grab": False}
        self.falling = False

    def on_move(self, e):
        if not self.drag:
            return
        lx, ly = self.drag["last"]
        self.drag["dist"] += math.hypot(e.x_root - lx, e.y_root - ly)
        self.drag["last"] = (e.x_root, e.y_root)
        self.x, self.y = e.x_root - self.drag["ox"], e.y_root - self.drag["oy"]
        if self.drag["dist"] > 6 and not self.drag["grab"]:
            self.drag["grab"] = True
            self.say(random.choice(["으아아 내려줘~", "높다…!", "어디 가는 거야?"]), 2)

    def on_up(self, e):
        d, self.drag = self.drag, None
        if d and d["dist"] <= 6:   # 그냥 클릭 → 대시보드
            self.hop()
            self.float_text("♥", "#ff8fa8")
            if self.state and self.state.get("pet"):
                self.say(self.status_line(), 3.5)
            self.open_dashboard(False)
        else:
            self.falling, self.vy = True, 0.0
            self.target, self.rest_until = None, time.time() + 2

    def on_menu(self, e):
        m = tk.Menu(self.root, tearoff=0, font=("Malgun Gothic", 10))
        m.add_command(label="대시보드 열기", command=lambda: self.open_dashboard(False))
        m.add_command(label="펫을 내 쪽으로 부르기", command=self.call)
        pm = tk.Menu(m, tearoff=0, font=("Malgun Gothic", 10))
        for label, mins in (("30분", 30), ("1시간", 60), ("2시간", 120), ("오늘 하루", 720)):
            pm.add_command(label=label, command=lambda v=mins: self.post("/api/pause", {"minutes": v}))
        pm.add_separator()
        pm.add_command(label="다시 시작", command=lambda: self.post("/api/pause", {"minutes": 0}))
        m.add_cascade(label="추적 일시정지 (회의·외출)", menu=pm)
        if IS_WIN:
            self.auto_var = tk.BooleanVar(value=startup_file().exists())
            m.add_checkbutton(label="Windows 시작 시 자동 실행", variable=self.auto_var, command=self.toggle_startup)
        m.add_separator()
        m.add_command(label="Figmon 종료", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def toggle_startup(self):
        f = startup_file()
        if f.exists():
            f.unlink()
        else:
            cmd = launch_cmd().replace('"', '""')
            f.write_text(f'CreateObject("WScript.Shell").Run "{cmd}", 0, False\r\n', encoding="utf-16")

    def call(self):
        self.target = self.root.winfo_pointerx() - self.W / 2
        self.speed = 320
        self.say("불렀어? 갈게!", 2)

    # ----- 이동 (60fps) -----
    def place(self):
        self.root.geometry(f"{self.W}x{self.H}+{int(self.x)}+{int(self.y)}")

    def tick(self):
        now = time.time()
        dt = min(now - self.last, 0.1)
        self.last = now
        if now - self.area_at > 3:
            self.area, self.area_at, self.calm = work_area(self.root), now, not animations_on()
        l, _, r, b = self.area
        ground = b - self.H
        mx = self.root.winfo_pointerx()
        if self.drag:
            pass
        elif self.falling:
            self.vy += 2600 * dt
            self.y += self.vy * dt
            if self.y >= ground:
                self.y, self.vy, self.falling = ground, 0.0, False
                self.hop()
                self.say("착지!", 1.2)
        else:
            self.y = ground
            self.think(now, mx, l, r, dt)
            self.x = min(max(self.x, l - self.W * 0.3), r - self.W * 0.7)
        self.place()
        self.render(now)
        self.root.after(16, self.tick)

    def think(self, now, mx, left, right, dt):
        cursor = mx - self.W / 2
        m = self.mode
        if m in ("idle", "paused"):
            if self.speed < 300:
                self.target = None
        elif m in ("warning", "penalty"):
            if not self.calm and abs(cursor - self.x) > 110:
                self.target, self.speed = cursor + (-70 if self.x < cursor else 70), 300.0
        elif not self.calm and self.target is None and now >= self.rest_until:
            watch = random.random() < (0.55 if m == "work" else 0.2)
            self.target = cursor + random.uniform(-180, 180) if watch else random.uniform(left, right - self.W)
            self.speed = 35.0 if m == "break" else 80.0 if m == "work" else 60.0
        if self.target is not None:
            d = self.target - self.x
            if abs(d) < 3:
                self.target = None
                lo, hi = (8, 20) if m == "break" else (5, 14) if m == "work" else (3, 9)
                self.rest_until = now + random.uniform(lo, hi)
                self.speed, self.walking = 60.0, False
            else:
                self.x += math.copysign(min(abs(d), self.speed * dt), d)
                self.facing, self.walking = (1 if d > 0 else -1), True
                return
        self.walking = False
        self.facing = 1 if mx > self.x + self.W / 2 else -1   # 멈춰 있을 땐 커서 쪽을 본다

    def render(self, now):
        t = now - self.t0
        dx = dy = 0.0
        if not self.calm:
            if getattr(self, "hop_until", 0) > now:
                dy = -math.sin((self.hop_until - now) / 0.5 * math.pi) * 26
            elif self.drag and self.drag["grab"]:
                dx = math.sin(t * 8) * 5
            elif self.walking:
                dy = -abs(math.sin(t * 10)) * 5
            elif self.mode == "work":
                dy = -abs(math.sin(t * 5)) * 7
            elif self.mode == "penalty":
                dx = math.sin(t * 60) * 4
            elif self.mode == "warning":
                dx = math.sin(t * 25) * 3
        self.cv.coords(self.img_item, self.W / 2 + dx, self.H - 6 + dy)
        # 진화 중 깜빡임 (동작 줄이기면 생략)
        if now < self.evolving_until and not self.calm:
            self.cv.itemconfigure(self.img_item, state="hidden" if int(t * 8) % 2 else "normal")
        else:
            self.cv.itemconfigure(self.img_item, state="normal")
        if self.bubble_until and now > self.bubble_until:
            self.cv.delete("bubble")
            self.bubble_until = None
        for f in self.floats[:]:
            item, born, life = f
            age = now - born
            if age > life:
                self.cv.delete(item)
                self.floats.remove(f)
            else:
                self.cv.move(item, 0, -40 * 0.016)
        if not self.calm and now > self.next_particle and self.mode in PARTICLE and self.cur:
            self.next_particle = now + random.uniform(0.7, 1.4)
            ch, col = PARTICLE[self.mode]
            item = self.cv.create_text(self.W / 2 + random.uniform(-40, 40), self.sprite_top() + 20, text=ch, fill=col,
                                       font=("Malgun Gothic", int(12 * self.dpi), "bold"))
            self.floats.append([item, now, 1.6])


def run(port, open_dashboard):
    Pet(port, open_dashboard).root.mainloop()


if __name__ == "__main__":  # 단독 테스트: 서버가 떠 있어야 함
    run(int(os.environ.get("FIGMON_PORT", 7777)), lambda title=False: print("open dashboard"))
