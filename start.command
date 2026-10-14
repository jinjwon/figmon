#!/bin/zsh
# 더블클릭: Figmon.app을 (필요하면 빌드해서) 실행
cd "$(dirname "$0")"
if [ ! -d dist/Figmon.app ] || [ -n "$(find Figmon.swift server.py index.html pet.html -newer dist/Figmon.app/Contents/Info.plist 2>/dev/null)" ]; then
  ./build_app.sh || { echo "⚠️ 빌드 실패: xcode-select --install 로 Command Line Tools를 설치해 주세요."; exit 1; }
fi
open dist/Figmon.app
