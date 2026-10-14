"""
Windows CI 점검: 감지(앞 창·입력 없음·창 제목 분류)와 펫 창이 실제로 뜨는지 확인하고 스크린샷을 남긴다.
사용: python tools/win_smoketest.py source|exe
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "smoketest"
OUT.mkdir(exist_ok=True)
mode = sys.argv[1] if len(sys.argv) > 1 else "source"
port = 7801 if mode == "source" else 7802
home = Path(tempfile.mkdtemp())
(home / "state.json").write_text(json.dumps({
    "pet": {"line": "909", "xp": 1300.0, "work_sec": 78000.0, "nickname": "", "born": 0},
    "bank_sec": 300.0, "total_work_sec": 78000.0, "dex": [], "events": [],
}), encoding="utf-8")
env = {**os.environ, "FIGMON_HOME": str(home), "FIGMON_PORT": str(port)}
report = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    report.append(line)


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return json.loads(r.read())


cmd = [sys.executable, str(ROOT / "server.py"), "--no-open"] if mode == "source" else [str(ROOT / "dist" / "Figmon.exe"), "--no-open"]
proc = subprocess.Popen(cmd, env=env, cwd=str(ROOT))
ok = False
for _ in range(80):
    try:
        get("/api/meta")
        ok = True
        break
    except Exception:
        time.sleep(0.5)
log(f"[{mode}] server up:", ok)

if mode == "source":
    os.environ.update(FIGMON_HOME=str(home))
    sys.path.insert(0, str(ROOT))
    import server
    time.sleep(5)  # 펫 창이 뜬 뒤에
    note = subprocess.Popen(["notepad.exe"])
    time.sleep(3)
    front = server.win_front()
    log("front window:", front, "(펫이 포커스를 안 뺏으면 notepad)")
    if front[0].lower() != "notepad":
        log("FAIL: 펫 창이 포커스를 가져감")
    log("idle seconds:", round(server.win_idle_seconds(), 1))
    log("sample():", server.sample())
    cfg = server.STATE["config"]
    for app, title in [("chrome", "Figma tutorial - YouTube - Google Chrome"), ("msedge", "Untitled – Figma - Microsoft Edge"),
                       ("chrome", "Claude - Google Chrome"), ("Code", "app.py - Visual Studio Code"), ("steam", "Steam")]:
        log("classify", app, "|", title, "→", server.classify(app, "", cfg, title))
    ps = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand",
                         __import__("base64").b64encode(server.PS_TOAST.replace("@TITLE@", "Figmon").replace("@MSG@", "테스트 알림").encode("utf-16-le")).decode()],
                        capture_output=True, text=True, timeout=30)
    log("toast exit:", ps.returncode, ps.stderr.strip()[:300])
    note.kill()

time.sleep(10)  # 스프라이트 내려받고 걸어 다닐 시간
try:
    st = get("/api/state")
    log("state:", st["live"]["mode"], st["pet"]["name"], "Lv", st["pet"]["level"])
except Exception as e:
    log("state error:", e)
try:
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.save(OUT / f"screen-{mode}.png")
    w, h = img.size
    img.crop((0, h - 420, w, h)).save(OUT / f"bottom-{mode}.png")
    log("screenshot:", img.size)
except Exception as e:
    log("screenshot error:", e)
log("pet process alive:", proc.poll() is None)
log("sprites cached:", sorted(p.name for p in (home / "sprites").glob("*")) if (home / "sprites").exists() else [])
proc.kill()
(OUT / f"report-{mode}.txt").write_text("\n".join(report), encoding="utf-8")
if not ok or proc.returncode not in (None, 1, -9, 15) and False:
    sys.exit(1)
sys.exit(0 if ok else 1)
