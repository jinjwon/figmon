# AGENTS.md — AI 코딩 도구(Codex, Claude Code 등)를 위한 작업 안내

Figmon은 작업 시간으로 포켓몬을 키우는 데스크톱 펫이에요 (macOS · Windows). 사용자와는 **한국어**로 이야기해 주세요.

## 구조 한눈에
- `server.py` — 핵심. Python 표준 라이브러리만 사용(외부 패키지 금지). 앞에 떠 있는 앱·탭 감지, 경험치·휴식 잔고·페널티 계산, 기록, HTTP API(`127.0.0.1:7777`), Windows 자동 업데이트. **버전은 맨 위 `VERSION` 한 곳**에서 관리.
- `index.html` — 대시보드(홈·기록·도감 탭, 선택 창, 설정). 빌드 도구 없이 한 파일.
- `pet.html` — macOS 데스크톱 펫 화면 (WKWebView 안에서 실행).
- `Figmon.swift` — macOS 앱 본체: server.py 실행, 펫 창, 메뉴 막대, 자동 업데이트, 응용 프로그램 폴더로 옮기기.
- `pet_win.py` — Windows 펫(tkinter). Windows에서는 server.py가 이걸 메인 스레드로 실행.
- `pokedex.json` — 2~9세대 데이터. `python3 tools/build_pokedex.py`로 다시 만듦. 1세대는 server.py에 직접 있음(기존 저장 호환).
- 사용자 데이터: `~/.figmon/state.json` (UTF-8로 읽고 쓸 것 — Windows 기본 인코딩은 한글을 못 담음).

## 빌드 · 확인
```bash
python3 -m py_compile server.py pet_win.py                  # 문법 확인
FIGMON_HOME=/tmp/figmon-test FIGMON_PORT=7799 python3 server.py --no-open --no-pet   # 테스트 서버
./build_app.sh                                              # macOS: dist/Figmon.app, dist/Figmon-macOS.zip
```
- index.html·pet.html의 스크립트를 고치면 `node --check`로 문법을 확인하세요 (스크립트가 깨지면 화면 전체가 멈춤).
- Windows는 GitHub Actions(`.github/workflows/windows.yml`)가 빌드·점검합니다: 감지, 하나만 실행, 자동 업데이트.

## 배포 (사용자 앱이 자동 업데이트됨)
1. `server.py`의 `VERSION`을 올림 → `./build_app.sh`
2. 커밋·푸시 → Actions 통과 확인
3. `gh release create vX.Y.Z dist/Figmon-macOS.zip --title ... --notes ...`
   → release 이벤트에서 Actions가 `Figmon-Windows.zip`을 자동으로 올림
4. 앱들이 1시간 안에 `releases/latest`를 보고 스스로 업데이트

## 꼭 지킬 것
- **비공식·비영리 팬 프로젝트**: 수익 활동(유료 기능, 광고, 후원) 금지. 포켓몬 이미지는 저장소에 넣지 말고 실행 중에 PokeAPI/sprites에서 불러올 것.
- 기존 저장 데이터와 호환: 계열 키·진화 단계 수를 바꾸면 키우던 포켓몬의 레벨/단계가 어긋날 수 있음.
- 쓰다듬기·던지기·클릭은 반응만, 경험치는 오직 작업 시간으로.
- 접근성: 버튼엔 글자나 aria-label, 키보드로 조작 가능, `prefers-reduced-motion` 존중.
