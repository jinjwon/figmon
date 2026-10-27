# Windows용 Figmon.exe 빌드 설정 (pyinstaller figmon.spec)
# 쓰지 않는 표준 모듈과 Tcl/Tk 부가 데이터를 빼서 내려받는 파일을 가볍게 함.
# 인터넷(SSL: 자동 업데이트·포켓몬 이미지)과 Tcl 문자 인코딩은 꼭 필요해서 남김.
EXCLUDES = [
    "unittest", "doctest", "pdb", "pydoc", "pydoc_data", "test", "tkinter.test", "idlelib", "lib2to3",
    "distutils", "setuptools", "pkg_resources", "pip", "ensurepip", "venv",
    "sqlite3", "_sqlite3", "xmlrpc", "multiprocessing", "asyncio", "concurrent",
    "lzma", "_lzma", "bz2", "_bz2", "decimal", "_decimal", "curses", "_curses",
    "PIL", "numpy", "tkinter.tix", "turtle", "turtledemo",
]
# Tcl/Tk 데이터 중 안 쓰는 것: 시간대(tzdata), 번역(msgs), 예제·그림
DROP = ("_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/demos", "_tk_data/images", "_tk_data/msgs",
        "tcl/tzdata", "tcl/msgs", "tk/demos", "tk/images", "tk/msgs")

a = Analysis(["server.py"], datas=[("index.html", "."), ("pet.html", "."), ("pokedex.json", ".")],
             hiddenimports=["pet_win"], excludes=EXCLUDES)
before = len(a.datas)
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith(DROP)]
print(f"[figmon.spec] dropped {before - len(a.datas)} Tcl/Tk data files")   # Windows 콘솔은 한글 출력 불가
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Figmon", icon="assets/figmon.ico",
          console=False, upx=False, strip=False, optimize=1)
