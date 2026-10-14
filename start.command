#!/bin/zsh
# 더블클릭으로 Figmon 실행 (처음 한 번은 데스크톱 펫을 빌드해요)
cd "$(dirname "$0")"
if [ ! -x build/FigmonPet ] || [ FigmonPet.swift -nt build/FigmonPet ]; then
  echo "데스크톱 펫 빌드 중…"
  mkdir -p build
  swiftc -O -swift-version 5 FigmonPet.swift -o build/FigmonPet \
    || echo "⚠️ 펫 빌드 실패 (Xcode Command Line Tools 필요: xcode-select --install). 대시보드만 실행합니다."
fi
python3 server.py
