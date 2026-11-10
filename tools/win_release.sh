#!/bin/bash
# Windows 빌드를 미러 저장소의 Actions에서 돌려 이 저장소 릴리스에 붙임.
# 본 계정 Actions가 막혔을 때 씀 (조직은 결제가 따로라 공개 저장소 Actions가 무료로 돎).
# 사용: FIGMON_MIRROR=<조직>/figmon tools/win_release.sh   (릴리스 vX.Y.Z를 먼저 만든 뒤)
set -euo pipefail
cd "$(dirname "$0")/.."
MIRROR="${FIGMON_MIRROR:?FIGMON_MIRROR=<조직>/figmon 을 지정하세요}"
TAG="v$(sed -n 's/^VERSION = "\([^"]*\)".*/\1/p' server.py)"
gh release view "$TAG" >/dev/null   # 릴리스가 없으면 여기서 멈춤
gh repo view "$MIRROR" >/dev/null 2>&1 || gh repo create "$MIRROR" --public
git push "https://github.com/$MIRROR.git" "$TAG^{commit}:refs/heads/main"   # 릴리스한 커밋 그대로 빌드
gh workflow run windows.yml -R "$MIRROR" --ref main
sleep 15   # 방금 요청한 실행이 목록에 나타날 때까지
ID=$(gh run list -R "$MIRROR" --workflow windows.yml --event workflow_dispatch -L 1 --json databaseId -q '.[0].databaseId')
gh run watch "$ID" -R "$MIRROR" --exit-status   # 스모크 테스트가 실패하면 올리지 않음
rm -rf dist/win && gh run download "$ID" -R "$MIRROR" -n Figmon-Windows -D dist/win
gh release upload "$TAG" dist/win/Figmon-Windows.zip --clobber
echo "$TAG 릴리스에 Figmon-Windows.zip 올림"
