#!/bin/zsh
# Figmon.app 빌드 → dist/Figmon.app, dist/Figmon-macOS.zip
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^VERSION = "\(.*\)".*/\1/p' server.py)   # 버전은 server.py 한 곳에서 관리
APP=dist/Figmon.app

echo "▸ 아이콘"
if [ ! -f build/AppIcon.icns ] || [ tools/make_icon.swift -nt build/AppIcon.icns ]; then
  rm -rf build/AppIcon.iconset && mkdir -p build
  swift tools/make_icon.swift build/AppIcon.iconset >/dev/null
  iconutil -c icns build/AppIcon.iconset -o build/AppIcon.icns
fi

echo "▸ 앱 컴파일"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# 대상 OS를 꼭 정해야 함: 안 정하면 빌드한 Mac의 버전(예: macOS 26)이 최소 버전이 되어
# Sequoia(15)·Sonoma(14)·Ventura(13)에서 앱이 안 열림. Intel Mac도 쓸 수 있게 유니버설로 만든다
MIN_OS=13.0
for ARCH in arm64 x86_64; do
  swiftc -O -swift-version 5 -target $ARCH-apple-macos$MIN_OS Figmon.swift -o build/Figmon-$ARCH
done
lipo -create build/Figmon-arm64 build/Figmon-x86_64 -output "$APP/Contents/MacOS/Figmon"
cp server.py index.html pet.html pokedex.json build/AppIcon.icns "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Figmon</string>
  <key>CFBundleDisplayName</key><string>Figmon</string>
  <key>CFBundleIdentifier</key><string>io.github.jinjwon.figmon</string>
  <key>CFBundleExecutable</key><string>Figmon</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleDevelopmentRegion</key><string>ko</string>
  <key>LSMinimumSystemVersion</key><string>${MIN_OS}</string>
  <key>LSUIElement</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>비공식 · 비영리 팬 프로젝트. Pokémon © Nintendo / Creatures Inc. / GAME FREAK inc.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>지금 보고 있는 브라우저 탭이 작업 사이트(예: figma.com)인지 딴짓 사이트(예: youtube.com)인지 구분하려고 주소만 확인해요. 정보는 이 Mac 밖으로 나가지 않아요.</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

echo "▸ 서명 (ad-hoc)"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"

echo "▸ 압축"
rm -f dist/Figmon-macOS.zip
(cd dist && ditto -c -k --keepParent Figmon.app Figmon-macOS.zip)
echo "✅ 완료: $APP"
