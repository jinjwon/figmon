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
    for name, url in ((f"{pid}.gif", f"{RAW}/other/showdown/{pid}.gif"), (f"{pid}.png", f"{RAW}/{pid}.png"),
                      (f"{pid}-art.png", f"{RAW}/other/official-artwork/{pid}.png")):   # 움직이는 이미지가 없으면 일러스트
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
    def __init__(self, port, open_dashboard, check_update=None, start_hidden=False, version="", updated=False):
        self.base = f"http://127.0.0.1:{port}"
        self.open_dashboard = open_dashboard
        self.check_update, self.version = check_update, version
        self.hidden, self.last_summon = False, 0
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
        self.W, self.H = int(300 * self.dpi), int(320 * self.dpi)
        self.size = 1.0
        self.live_size, self.live_until, self.size_win = None, 0.0, None
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
        self.facing, self.walking, self.running = -1, False, False
        self.target, self.speed, self.rest_until = None, 55.0, 0.0
        self.vx, self.look_until, self.next_look = 0.0, 0.0, 0.0
        self.last_click, self.clicks, self.last_hover, self.next_dust = 0.0, [], 0.0, 0.0
        self.squash_until = 0.0
        self.rub = {"x": None, "sign": 0, "travel": 0.0, "turns": 0, "last": 0.0, "pet": 0.0}   # 쓰다듬기 감지
        self.vx_air, self.thrown, self.bounced, self.spin_until, self.samples = 0.0, False, False, 0.0, []
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
        self.cv.bind("<Enter>", self.on_hover)
        self.cv.bind("<Motion>", self.on_rub)
        self.place()

        threading.Thread(target=self.poll_loop, daemon=True).start()
        if start_hidden:   # 로그인 자동 실행: 작업을 시작하면 걸어 나옴
            self.hide()
        if updated:
            root.after(3500, lambda: self.say(f"v{version}로 업데이트했어요 ✨", 6))
        if IS_WIN:
            root.after(6000, self.ask_startup_once)
        root.after(16, self.tick)
        root.after(90, self.animate)
        root.after(300, self.apply_state)

    # ----- 서버 -----
    def poll_loop(self):
        while True:
            try:
                with urllib.request.urlopen(self.base + "/api/state?lite=1", timeout=3) as r:   # 가벼운 응답
                    self.new_state = json.loads(r.read())
                    self.online = True
            except Exception:
                self.online = False
            time.sleep(0.5)   # 대시보드에서 크기·설정을 바꾸면 바로 따라오게

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
            ps = self.live_size if time.time() < self.live_until else float(s["config"].get("pet_size", 1) or 1)
            if ps != self.size:
                self.resize(ps)
            if s["live"].get("summon", 0) > self.last_summon:   # 작업 앱을 켜서 불려 나옴
                self.last_summon = s["live"]["summon"]
                self.summon(s["live"].get("summon_label", ""))
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
                elif p["species"][0] != self.prev["species"]:   # 메가진화·아르세우스 타입처럼 모습만 바뀜
                    mega = (p.get("mega_info") or {}).get("active") and not self.prev.get("mega")
                    self.say(f"💎 {p['species'][1]}(으)로 메가진화!" if mega else f"✨ {p['species'][1]}!", 5)
                    self.burst()
                elif p["level"] > self.prev["level"]:
                    self.say(f"레벨 업! Lv.{p['level']} 🎉", 3)
                    self.float_text(f"Lv.{p['level']}!", "#e0a800")
                elif p["level"] < self.prev["level"]:
                    self.say(f"Lv.{p['level']}로 떨어졌어… 😢", 4, True)
                if self.prev["xp"] - p["xp"] > 0.05:
                    self.float_text(f"-{self.prev['xp'] - p['xp']:.1f} XP", "#e5484d")
            self.prev = {"line": p["line"], "stage": p["stage"], "level": p["level"], "xp": p["xp"], "species": p["species"][0],
                         "mega": (p.get("mega_info") or {}).get("active")} if p else None

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
        # 레벨에 따라 조금씩 커짐 (진화 단계 수와 상관없이 같은 규칙)
        scale = (1.25 + 0.85 * min(p["level"], 40) / 40) * self.size * self.dpi   # 슬라이더 값이 바로 반영되게
        return max(1, round(scale * 1.4))

    def mode_line(self, m):
        if self.state and (self.state.get("pet") or {}).get("branch_pending") and random.random() < .5:
            return "진화할 수 있을 것 같아! 대시보드에서 골라줘 ✨"
        h = time.localtime().tm_hour
        part = "morning" if 6 <= h < 11 else "noon" if 11 <= h < 17 else "evening" if 17 <= h < 21 else "night"
        if m in ("work", "neutral") and random.random() < .25:
            return {"morning": "좋은 아침! ☀️ 오늘도 같이 크자", "noon": "점심은 먹었어? 🍚",
                    "evening": "저녁 시간이야, 조금만 더!", "night": "새벽이야… 무리하지 마 🌙"}[part]
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
            self.set_cur(key)
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
                img = tk.PhotoImage(file=str(path))
                if path.name.endswith("-art.png") and img.width() > 120:   # 큰 일러스트는 도트 크기로 줄임
                    img = img.subsample(max(1, img.width() // 64))
                frames = [img]
        except tk.TclError:
            frames = frames or []
        if not frames:
            return
        fw, fh = frames[0].width(), frames[0].height()
        z = max(1, min(z, int((self.H - 110) / max(fh, 1)), int((self.W - 40) / max(fw, 1))))   # 말풍선·점프 자리 남기기
        left = [f.zoom(z) if z > 1 else f for f in frames]
        right = []
        for f in left:
            m = tk.PhotoImage()
            m.tk.call(str(m), "copy", str(f), "-subsample", -1, 1)  # 좌우 반전
            right.append(m)
        self.frames[key] = {"L": left, "R": right}
        self.set_cur(key)
        self.cv.delete("ball")

    def set_cur(self, key):
        # 캔버스를 새 이미지로 바로 바꿔야 함: 크기를 바꾸면 예전 이미지가 지워지는데,
        # 캔버스가 지워진 이미지를 가리킨 채로 남으면 tick에서 TclError가 나서 펫이 멈춤
        self.cur, self.cur_key, self.frame_i = self.frames[key], key, 0
        self.cv.itemconfigure(self.img_item, image=self.cur["R" if self.facing > 0 else "L"][0])

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

    def float_text(self, text, color, dx=0.0, speed=40.0):
        item = self.cv.create_text(self.W // 2 + dx, self.sprite_top() + 10, text=text, fill=color,
                                   font=("Malgun Gothic", int(12 * self.dpi), "bold"))
        self.floats.append([item, time.time(), 1.3, dx / 1.3 * 0.4, speed])

    def hearts(self, n):
        for i in range(n):
            self.root.after(i * 90, lambda: self.float_text("♥", "#ff7aa2", random.uniform(-35, 35), 50))

    def dust(self, n=1):
        foot_y = self.H - 10
        back = -1 if self.facing > 0 else 1   # 진행 반대쪽 발 뒤로
        for _ in range(n):
            x = self.W / 2 + back * 22 * self.dpi + random.uniform(-5, 5)
            r = 3 * self.dpi
            item = self.cv.create_oval(x - r, foot_y - r, x + r, foot_y + r, fill="#c9c1b3", outline="")
            self.floats.append([item, time.time(), 0.6, back * 18, 10])

    def burst(self):
        for i in range(6):
            self.root.after(i * 120, lambda: self.float_text("✦", "#f5c518", random.uniform(-40, 40)))

    def hop(self, secs=0.5):
        self.hop_until = time.time() + secs
        self.hop_len = secs

    def on_hover(self, e):
        now = time.time()
        if self.drag or self.walking or now - self.last_hover < 6:
            return
        self.last_hover = now
        self.hop(0.3)
        self.float_text("!", "#f5b400", 18, 25)
        if self.target is not None:
            self.target, self.vx, self.rest_until = None, 0.0, now + 3

    def on_rub(self, e):
        """커서를 펫 위에서 좌우로 비비면 쓰다듬기 (반응만, 경험치와 무관)"""
        if self.drag or self.falling:
            return
        r, now = self.rub, time.time()
        if r["x"] is not None:
            dxm = e.x_root - r["x"]
            if abs(dxm) > 1.5:
                sign = 1 if dxm > 0 else -1
                if sign != r["sign"] and r["travel"] > 6:
                    r["turns"] += 1
                    r["travel"] = 0.0
                r["sign"], r["last"] = sign, now
                r["travel"] += abs(dxm)
        r["x"] = e.x_root
        if now - r["last"] > 0.6:
            r["turns"] = 0
        if r["turns"] >= 3 and now - r["pet"] > 1.2:
            r["pet"], r["turns"] = now, 0
            self.target, self.vx, self.rest_until = None, 0.0, now + 4
            self.hop(0.3)
            self.hearts(2)
            if self.bubble_until is None or now - getattr(self, "pet_said", 0) > 2.6:
                self.pet_said = now
                self.say(random.choice(["쓰담쓰담~ 좋아 💕", "기분 좋아~ 😊", "헤헤 더 해줘!", "스르르… 행복해"]), 2.6)

    def react_line(self):
        if not self.state or not self.state.get("pet"):
            return "대시보드에서 파트너를 골라줘! (두 번 누르기)"
        lines = {
            "work": ["응? 나 불렀어? 😊", "쓰담쓰담 좋아!", "같이 힘내자!", self.status_line()],
            "neutral": ["헤헤, 반가워!", "놀아줄 거야?", self.status_line()],
            "break": ["같이 쉬자~", "간식 있어? 🍪"],
            "warning": ["지금 그럴 때가 아니야!", "빨리 돌아가자!"],
            "penalty": ["흥! 작업부터 해!", "쓰다듬어도 안 풀려 💢"],
            "idle": ["으음… 깼어…", "돌아왔구나!"],
            "paused": ["쉬는 중이지? 기다릴게", "다녀와~"],
            "ai": ["AI 말고 나랑 놀자~", "스스로도 해보자!"],
        }
        return random.choice(lines.get(self.mode, lines["neutral"]))

    # ----- 마우스 -----
    def on_down(self, e):
        self.drag = {"ox": e.x_root - self.x, "oy": e.y_root - self.y, "dist": 0.0, "last": (e.x_root, e.y_root), "grab": False}
        self.falling = False
        self.samples = [(time.time(), e.x_root, e.y_root)]

    def on_move(self, e):
        if not self.drag:
            return
        lx, ly = self.drag["last"]
        self.drag["dist"] += math.hypot(e.x_root - lx, e.y_root - ly)
        self.drag["last"] = (e.x_root, e.y_root)
        self.x, self.y = e.x_root - self.drag["ox"], e.y_root - self.drag["oy"]
        now = time.time()
        self.samples = [p for p in self.samples if now - p[0] <= 0.12] + [(now, e.x_root, e.y_root)]
        if self.drag["dist"] > 6 and not self.drag["grab"]:
            self.drag["grab"] = True
            self.say(random.choice(["으아아 내려줘~", "높다…!", "어디 가는 거야?"]), 2)

    def on_up(self, e):
        d, self.drag = self.drag, None
        if d and d["dist"] <= 6:
            now = time.time()
            if now - self.last_click < 0.4:      # 두 번 누르기 → 대시보드
                self.last_click = 0
                self.say("대시보드 열게!", 1.5)
                self.open_dashboard(False)
                return
            self.last_click = now
            self.clicks = [t for t in self.clicks if now - t < 1.5] + [now]
            self.target, self.vx, self.rest_until = None, 0.0, now + 4   # 멈춰서 나를 봄
            self.facing = 1 if e.x_root > self.x + self.W / 2 else -1
            if len(self.clicks) >= 3:            # 연달아 누르면 간지러워함
                self.hop(0.7)
                self.hearts(5)
                self.say(random.choice(["간지러워~ ㅋㅋㅋ", "그만그만~ 😆", "히히 간지럽다니까!"]), 2.2)
            else:
                self.hop(0.55)
                if self.mode == "penalty":
                    self.float_text("💢", "#e5484d")
                else:
                    self.hearts(3)
                self.say(self.react_line(), 3)
        else:
            self.falling, self.vy, self.vx_air, self.thrown, self.bounced = True, 0.0, 0.0, False, False
            self.target, self.vx, self.rest_until = None, 0.0, time.time() + 2
            (t0, x0, y0), (t1, x1, y1) = self.samples[0], self.samples[-1]
            if t1 - t0 > 0.01:
                vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
                if math.hypot(vx, vy) > 900 * self.dpi:   # 세게 던짐 → 날아감
                    self.thrown = True
                    self.vx_air = max(-2600.0, min(2600.0, vx))
                    self.vy = max(-2600.0, min(2600.0, vy))
                    self.spin_until = time.time() + 5
                    self.say(random.choice(["으아아아~!", "날 수 있다…?!", "살려줘어~"]), 0, True)

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
        m.add_command(label=f"펫 크기 조절… (지금 {round(self.size * 100)}%)", command=self.open_size_window)
        m.add_command(label="펫 숨기기 (작업 앱을 켜면 다시 나와요)", command=self.hide)
        if IS_WIN:
            self.auto_var = tk.BooleanVar(value=startup_file().exists())
            m.add_checkbutton(label="Windows 시작 시 자동 실행", variable=self.auto_var, command=self.toggle_startup)
        if self.check_update:
            m.add_command(label="업데이트 확인", command=self.check_update)
        if self.version:
            m.add_command(label=f"Figmon v{self.version}", state="disabled")
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

    def ask_startup_once(self):
        """처음 한 번: 작업 앱을 켜면 펫이 바로 나오도록 Windows 시작 시 자동 실행할지 묻기"""
        flag = CACHE.parent / "asked_startup"
        if flag.exists() or startup_file().exists():
            return
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1")
        r = ctypes.windll.user32.MessageBoxW(
            None, "작업 앱(Figma·VS Code 등)을 켜면 포켓몬이 바로 걸어 나오게 할까요?\n\n"
                  "Windows를 켤 때 Figmon을 조용히 실행해 두고, 작업을 시작하면 펫이 나타나요.\n"
                  "(나중에 펫 우클릭 › Windows 시작 시 자동 실행에서 바꿀 수 있어요)",
            "Figmon", 0x4 | 0x40 | 0x40000)  # 예/아니요 | 정보 아이콘 | 맨 위
        if r == 6:
            self.toggle_startup()
            self.say("좋아! 작업 앱을 켜면 바로 나올게 🎨", 4)

    def resize(self, size):
        """펫 크기에 맞춰 창도 키움 (크게 해도 잘리지 않게). 가운데·바닥 위치는 그대로"""
        self.size = size
        f = max(1.0, size * 1.15)
        nw, nh = int(300 * self.dpi * f), int(320 * self.dpi * f)
        if (nw, nh) == (self.W, self.H):
            return
        self.x -= (nw - self.W) / 2
        self.y -= nh - self.H
        self.W, self.H = nw, nh
        self.cv.config(width=nw, height=nh)
        self.frames.clear()          # 새 크기에 맞춰 다시 만듦
        self.cur_key = None

    def open_size_window(self):
        """작은 슬라이더 창: 끄는 동안 펫이 바로 커지고 작아짐"""
        if self.size_win and self.size_win.winfo_exists():
            self.size_win.lift()
            return
        w = self.size_win = tk.Toplevel(self.root)
        w.title("펫 크기")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.geometry(f"+{max(0, int(self.x))}+{max(0, int(self.y) - 120)}")
        label = tk.Label(w, text=f"🐾 펫 크기  {round(self.size * 100)}%", font=("Malgun Gothic", 11, "bold"))
        label.pack(padx=16, pady=(12, 0))
        save_job = [None]

        def changed(v):
            v = round(float(v) * 20) / 20   # 5% 단위
            label.config(text=f"🐾 펫 크기  {round(v * 100)}%")
            self.live_size, self.live_until = v, time.time() + 2.5
            self.resize(v)
            if save_job[0]:
                self.root.after_cancel(save_job[0])
            save_job[0] = self.root.after(400, lambda: self.post("/api/config", {"pet_size": v}))

        scale = tk.Scale(w, from_=0.6, to=1.6, resolution=0.05, orient="horizontal", length=260, showvalue=False,
                         command=changed)
        scale.set(self.size)
        scale.pack(padx=16, pady=6)
        scale.focus_set()   # ← → 키로도 조절
        tk.Button(w, text="닫기", command=w.destroy, font=("Malgun Gothic", 10)).pack(pady=(0, 12))

    def hide(self):
        self.hidden = True
        self.root.withdraw()

    def summon(self, label=""):
        """화면 가장자리(커서와 가까운 쪽)에서 걸어 나와 인사"""
        mx = self.root.winfo_pointerx()
        l, _, r, _ = self.area
        if self.hidden:
            self.hidden = False
            self.x = (r - self.W * 0.7) if mx > (l + r) / 2 else (l - self.W * 0.3)
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        self.target, self.speed, self.rest_until = mx - self.W / 2 + random.choice((-120, 120)), 170.0, 0
        self.look_until = time.time() + 0.4
        name = self.state["pet"]["name"] if self.state and self.state.get("pet") else "나"
        app = label.split(".")[0].capitalize() if "." in label else label
        self.say(f"{app} 켰구나! {name}도 같이 할게 🎨" if app else "불렀어? 왔어!", 4.5)

    def call(self):
        self.target = self.root.winfo_pointerx() - self.W / 2
        self.speed = 260
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
            self.x += self.vx_air * dt
            self.vx_air *= 0.6 ** dt   # 공기 저항
            lo, hi = l - self.W * 0.3, r - self.W * 0.7   # 화면 가장자리에 튕김
            if self.x < lo:
                self.x, self.vx_air = lo, abs(self.vx_air) * 0.5
            if self.x > hi:
                self.x, self.vx_air = hi, -abs(self.vx_air) * 0.5
            top = self.area[1] - self.sprite_top()   # 창이 아니라 펫 몸 윗부분 기준 (크게 해도 던지는 범위가 그대로)
            if self.y < top:
                self.y, self.vy = top, abs(self.vy) * 0.3
            if self.y >= ground:
                impact = self.vy
                self.y = ground
                if self.thrown and not self.bounced and impact > 900:   # 한 번 통 튕기고
                    self.bounced, self.vy, self.vx_air = True, -impact * 0.35, self.vx_air * 0.6
                else:
                    self.vy, self.falling, self.vx, self.vx_air = 0.0, False, 0.0, 0.0
                    self.squash_until = time.time() + 0.35
                    self.dust(5 if self.thrown else 4)
                    if self.thrown:   # 깜짝 놀람
                        self.spin_until = 0
                        self.hop(0.7)
                        self.float_text("!?", "#f5b400", 18, 30)
                        self.root.after(300, lambda: self.float_text("💫", "#f5b400", -20, 30))
                        self.say(random.choice(["깜짝이야!! 😵", "어지러워~ 💫", "던지지 마아~ 😣", "휴… 살았다"]), 3.2)
                        self.rest_until = time.time() + 4
                    else:
                        self.say("착지!", 1.2)
                    self.thrown = False
        else:
            self.y = ground
            self.think(now, mx, l, r, dt)
            self.x = min(max(self.x, l - self.W * 0.3), r - self.W * 0.7)
        try:
            self.place()
            self.render(now)
        finally:   # 한 번 오류가 나도 움직임·드래그가 멈추지 않게
            self.root.after(16, self.tick)

    def think(self, now, mx, left, right, dt):
        """작업 중엔 가끔 커서 근처로 와서 지켜보고, 딴짓하면 달려온다. 가속·감속해서 자연스럽게 걷는다."""
        cursor = mx - self.W / 2
        m = self.mode
        if m in ("idle", "paused"):
            if self.speed < 200:
                self.target = None
        elif m in ("warning", "penalty"):
            if not self.calm and abs(cursor - self.x) > 120 and self.target is None:
                self.target, self.speed = cursor + (-80 if self.x < cursor else 80), 220.0
        elif not self.calm and self.target is None and now >= self.rest_until:
            watch = random.random() < (0.6 if m == "work" else 0.25)
            t = cursor + random.uniform(-160, 160) if watch else random.uniform(left, right - self.W)
            if abs(t - self.x) < 40:
                t = self.x + (60 if t > self.x else -60)
            self.target, self.speed = t, (32.0 if m == "break" else 55.0)
            self.look_until = now + 0.6             # 출발 전에 갈 쪽을 먼저 봄
            self.facing = 1 if t > self.x else -1
        if self.target is not None and now >= self.look_until:
            # 크기 변경·커서 추적으로 목적지가 화면 밖에 남으면 도착하지 못하고 멈춰 버림.
            # tick의 위치 제한과 같은 범위 안에서 목적지를 정한다.
            self.target = min(max(self.target, left - self.W * 0.3), right - self.W * 0.7)
            d = self.target - self.x
            want = math.copysign(min(self.speed, abs(d) * 2.2), d)   # 도착할수록 천천히
            accel = 520 if self.speed > 150 else 160
            self.vx += max(-accel * dt, min(accel * dt, want - self.vx))
            self.x += self.vx * dt
            if abs(d) < 2 and abs(self.vx) < 12:
                self.target, self.vx = None, 0.0
                lo, hi = (14, 30) if m == "break" else (10, 24) if m == "work" else (6, 16)
                self.rest_until = now + random.uniform(lo, hi)
                self.speed, self.walking, self.running = 55.0, False, False
            else:
                self.facing = 1 if self.vx > 0 else -1
                self.walking, self.running = abs(self.vx) > 6, abs(self.vx) > 140
                if self.walking and now > self.next_dust and not self.calm:
                    self.next_dust = now + (0.26 if self.running else 0.42)
                    self.dust(2 if self.running else 1)
                return
        elif self.target is not None:
            self.walking = self.running = False
            return
        self.walking = self.running = False
        if abs(mx - (self.x + self.W / 2)) < 500:   # 멈춰 있을 땐 가까운 커서를 바라봄
            self.facing = 1 if mx > self.x + self.W / 2 else -1
        elif now >= self.next_look:                 # 멀리 있으면 가끔 두리번
            self.next_look = now + random.uniform(4, 9)
            self.facing = -self.facing

    def render(self, now):
        t = now - self.t0
        dx = dy = 0.0
        if not self.calm:   # 기본은 가만히, 걸을 때만 살짝 통통
            if getattr(self, "hop_until", 0) > now:
                k = (self.hop_until - now) / self.hop_len
                dy = -math.sin(k * math.pi) * (22 if self.hop_len > 0.4 else 8) * self.dpi
                if self.hop_len >= 0.7:
                    dx = math.sin(k * math.pi * 6) * 5   # 간지러워서 꿈틀
            elif self.drag and self.drag["grab"]:
                dx = math.sin(t * 7) * 4
            elif self.squash_until > now:
                dy = 2
            elif self.walking:
                dy = -abs(math.sin(t * (24 if self.running else 13.6))) * 3 * self.dpi
            elif self.mode == "penalty":
                dx = math.sin(t * 22) * 2
            elif self.mode == "warning":
                dx = math.sin(t * 12) * 1.5
        if self.falling and self.thrown and now < self.spin_until and not self.calm:
            self.facing = 1 if int(t * 10) % 2 else -1
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
            item, born, life, vx, vy = f
            if now - born > life:
                self.cv.delete(item)
                self.floats.remove(f)
            else:
                self.cv.move(item, vx * 0.016, -vy * 0.016)
        if not self.calm and now > self.next_particle and self.mode in PARTICLE and self.cur:
            self.next_particle = now + random.uniform(1.4, 2.6)   # 파티클도 덜 자주
            ch, col = PARTICLE[self.mode]
            item = self.cv.create_text(self.W / 2 + random.uniform(-40, 40), self.sprite_top() + 20, text=ch, fill=col,
                                       font=("Malgun Gothic", int(12 * self.dpi), "bold"))
            self.floats.append([item, now, 1.6, 0, 40])


def run(port, open_dashboard, **kw):
    Pet(port, open_dashboard, **kw).root.mainloop()


if __name__ == "__main__":  # 단독 테스트: 서버가 떠 있어야 함
    run(int(os.environ.get("FIGMON_PORT", 7777)), lambda title=False: print("open dashboard"))
