// Figmon 앱 아이콘(원본 일러스트: 알 + 반짝임)을 .iconset 폴더로 그린다.
// 사용: swift tools/make_icon.swift build/AppIcon.iconset && iconutil -c icns build/AppIcon.iconset
import Cocoa

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset")
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

func draw(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px) / 1024
    let ctx = NSGraphicsContext.current!.cgContext
    ctx.scaleBy(x: s, y: s)

    // 둥근 사각형 배경 (macOS 아이콘 그리드: 824px, 여백 100px)
    let card = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
    NSGraphicsContext.current!.saveGraphicsState()
    card.addClip()
    NSGradient(colors: [NSColor(red: 0.18, green: 0.21, blue: 0.44, alpha: 1), NSColor(red: 0.07, green: 0.08, blue: 0.12, alpha: 1)])!
        .draw(in: card, angle: -90)
    NSColor(red: 0.42, green: 0.75, blue: 0.35, alpha: 1).setFill()
    NSBezierPath(ovalIn: NSRect(x: -60, y: -40, width: 1144, height: 330)).fill()
    for (x, y, r) in [(250.0, 780.0, 7.0), (760.0, 820.0, 6.0), (820.0, 640.0, 5.0), (300.0, 600.0, 4.0)] {
        NSColor(white: 1, alpha: 0.6).setFill()
        NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: r * 2, height: r * 2)).fill()
    }
    NSGraphicsContext.current!.restoreGraphicsState()

    // 그림자
    NSColor(white: 0, alpha: 0.22).setFill()
    NSBezierPath(ovalIn: NSRect(x: 352, y: 212, width: 320, height: 56)).fill()
    // 알
    let egg = NSBezierPath()
    egg.move(to: NSPoint(x: 512, y: 760))
    egg.curve(to: NSPoint(x: 680, y: 400), controlPoint1: NSPoint(x: 640, y: 760), controlPoint2: NSPoint(x: 690, y: 520))
    egg.curve(to: NSPoint(x: 512, y: 240), controlPoint1: NSPoint(x: 672, y: 300), controlPoint2: NSPoint(x: 600, y: 240))
    egg.curve(to: NSPoint(x: 344, y: 400), controlPoint1: NSPoint(x: 424, y: 240), controlPoint2: NSPoint(x: 352, y: 300))
    egg.curve(to: NSPoint(x: 512, y: 760), controlPoint1: NSPoint(x: 334, y: 520), controlPoint2: NSPoint(x: 384, y: 760))
    NSColor(red: 0.99, green: 0.97, blue: 0.92, alpha: 1).setFill()
    egg.fill()
    NSGraphicsContext.current!.saveGraphicsState()
    egg.addClip()
    NSColor(white: 0, alpha: 0.07).setFill()
    NSBezierPath(ovalIn: NSRect(x: 470, y: 180, width: 300, height: 420)).fill()
    for (x, y, r, c) in [(450.0, 600.0, 46.0, 0), (590.0, 500.0, 34.0, 1), (470.0, 390.0, 56.0, 2), (620.0, 340.0, 26.0, 0)] {
        [NSColor(red: 0.94, green: 0.5, blue: 0.19, alpha: 1), NSColor(red: 0.47, green: 0.78, blue: 0.31, alpha: 1),
         NSColor(red: 0.41, green: 0.56, blue: 0.94, alpha: 1)][c].setFill()
        NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: r * 2, height: r * 2)).fill()
    }
    NSGraphicsContext.current!.restoreGraphicsState()
    // 반짝임
    func sparkle(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat) {
        let p = NSBezierPath()
        p.move(to: NSPoint(x: cx, y: cy + r))
        p.curve(to: NSPoint(x: cx + r, y: cy), controlPoint1: NSPoint(x: cx + r * 0.12, y: cy + r * 0.12), controlPoint2: NSPoint(x: cx + r * 0.12, y: cy + r * 0.12))
        p.curve(to: NSPoint(x: cx, y: cy - r), controlPoint1: NSPoint(x: cx + r * 0.12, y: cy - r * 0.12), controlPoint2: NSPoint(x: cx + r * 0.12, y: cy - r * 0.12))
        p.curve(to: NSPoint(x: cx - r, y: cy), controlPoint1: NSPoint(x: cx - r * 0.12, y: cy - r * 0.12), controlPoint2: NSPoint(x: cx - r * 0.12, y: cy - r * 0.12))
        p.curve(to: NSPoint(x: cx, y: cy + r), controlPoint1: NSPoint(x: cx - r * 0.12, y: cy + r * 0.12), controlPoint2: NSPoint(x: cx - r * 0.12, y: cy + r * 0.12))
        NSColor(red: 1, green: 0.85, blue: 0.3, alpha: 1).setFill()
        p.fill()
    }
    sparkle(735, 735, 70)
    sparkle(800, 610, 34)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try! draw(size).write(to: out.appendingPathComponent("icon_\(size)x\(size).png"))
    try! draw(size * 2).write(to: out.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
print("아이콘 생성:", out.path)
