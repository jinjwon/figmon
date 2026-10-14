#!/bin/zsh
# Figmon.app 빌드 → dist/Figmon.app, dist/Figmon-macOS.zip
set -e
cd "$(dirname "$0")"
VERSION=1.2.0
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
swiftc -O -swift-version 5 Figmon.swift -o "$APP/Contents/MacOS/Figmon"
cp server.py index.html pet.html build/AppIcon.icns "$APP/Contents/Resources/"

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
  <key>LSMinimumSystemVersion</key><string>13.0</string>
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
