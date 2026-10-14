// FigmonPet — 화면 아래를 돌아다니는 투명 데스크톱 펫 창.
// 그림/애니메이션은 server.py가 주는 /pet 페이지(WKWebView)가 그리고,
// 이 파일은 창 위치(걷기, 커서 쫓아가기, 드래그, 낙하)와 클릭만 담당한다.
//
// 빌드: swiftc -O -swift-version 5 FigmonPet.swift -o build/FigmonPet
import Cocoa
import WebKit

let env = ProcessInfo.processInfo.environment
let port = env["FIGMON_PORT"] ?? "7777"
let petURL = URL(string: "http://127.0.0.1:\(port)/pet")!
let dashURL = "http://localhost:\(port)"
let W: CGFloat = 260, H: CGFloat = 260

final class PetPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// 웹뷰 위에 덮어서 마우스를 직접 받는 뷰 (포커스를 뺏지 않고 첫 클릭부터 반응)
final class GrabView: NSView {
    weak var ctl: PetController?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with e: NSEvent) { ctl?.dragBegan() }
    override func mouseDragged(with e: NSEvent) { ctl?.dragMoved() }
    override func mouseUp(with e: NSEvent) { ctl?.dragEnded() }
    override func rightMouseDown(with e: NSEvent) {
        guard let menu = ctl?.makeMenu() else { return }
        NSMenu.popUpContextMenu(menu, with: e, for: self)
    }
}

final class PetController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let panel: PetPanel
    let web: WKWebView
    let grab = GrabView()
    let status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

    var petRect = NSRect(x: 90, y: 4, width: 80, height: 90)   // 창 안에서 펫이 차지하는 영역(아래 왼쪽 기준)
    var mode = "neutral"
    var x: CGFloat = 0, y: CGFloat = 0, vy: CGFloat = 0
    var targetX: CGFloat? = nil
    var speed: CGFloat = 60
    var pauseUntil = Date()
    var dragging = false, falling = false, grabOffset = CGPoint.zero, dragDistance: CGFloat = 0, lastMouse = CGPoint.zero
    var facing = 0, walking = false, lastTick = Date(), hidden = false, clickThrough = true

    override init() {
        panel = PetPanel(contentRect: NSRect(x: 0, y: 0, width: W, height: H),
                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        let cfg = WKWebViewConfiguration()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: W, height: H), configuration: cfg)
        super.init()
        cfg.userContentController.add(self, name: "pet")

        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.isMovable = false
        panel.ignoresMouseEvents = true

        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        let root = NSView(frame: NSRect(x: 0, y: 0, width: W, height: H))
        root.addSubview(web)
        grab.frame = root.bounds
        grab.ctl = self
        root.addSubview(grab)
        panel.contentView = root

        let vf = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        x = vf.maxX - W - 60
        y = vf.minY
        panel.setFrameOrigin(NSPoint(x: x, y: y))
        panel.orderFrontRegardless()
        web.load(URLRequest(url: petURL))

        status.button?.title = "🥚"
        status.menu = makeMenu()

        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)

        // server.py가 꺼지면 같이 종료
        if let pp = env["FIGMON_PARENT"], let pid = Int32(pp) {
            Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in
                if kill(pid, 0) != 0 { NSApp.terminate(nil) }
            }
        }
    }

    // MARK: 메뉴
    func makeMenu() -> NSMenu {
        let m = NSMenu()
        m.addItem(withTitle: "대시보드 열기", action: #selector(openDashboard), keyEquivalent: "").target = self
        m.addItem(withTitle: "내 쪽으로 부르기", action: #selector(callPet), keyEquivalent: "").target = self
        m.addItem(withTitle: hidden ? "펫 보이기" : "펫 숨기기", action: #selector(toggleHidden), keyEquivalent: "").target = self
        m.addItem(.separator())
        m.addItem(withTitle: "펫 종료", action: #selector(quit), keyEquivalent: "").target = self
        return m
    }

    @objc func openDashboard() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        if FileManager.default.fileExists(atPath: "/Applications/Google Chrome.app") {
            p.arguments = ["-na", "Google Chrome", "--args", "--app=\(dashURL)", "--window-size=460,860"]
        } else {
            p.arguments = [dashURL]
        }
        try? p.run()
    }

    @objc func callPet() {
        let mouse = NSEvent.mouseLocation
        targetX = mouse.x - W / 2
        speed = 320
        js("petEvent('come')")
    }

    @objc func toggleHidden() {
        hidden.toggle()
        hidden ? panel.orderOut(nil) : panel.orderFrontRegardless()
        status.menu = makeMenu()
    }

    @objc func quit() { NSApp.terminate(nil) }

    // MARK: 웹뷰 ↔ Swift
    func js(_ code: String) { web.evaluateJavaScript(code, completionHandler: nil) }

    func userContentController(_ uc: WKUserContentController, didReceive msg: WKScriptMessage) {
        guard let d = msg.body as? [String: Any], let type = d["type"] as? String else { return }
        switch type {
        case "rect":
            if let rx = d["x"] as? Double, let ry = d["y"] as? Double, let rw = d["w"] as? Double, let rh = d["h"] as? Double {
                petRect = NSRect(x: rx, y: Double(H) - ry - rh, width: rw, height: rh)
            }
        case "mode":
            let m = d["mode"] as? String ?? "neutral"
            if m != mode && (m == "warning" || m == "penalty") { targetX = nil; pauseUntil = Date() }
            mode = m
        case "title":
            status.button?.title = d["text"] as? String ?? "🥚"
        default: break
        }
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { retryLoad() }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { retryLoad() }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { retryLoad() }
    func retryLoad() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.web.load(URLRequest(url: petURL))
        }
    }

    // MARK: 드래그
    func dragBegan() {
        let m = NSEvent.mouseLocation
        dragging = true; falling = false
        grabOffset = CGPoint(x: m.x - x, y: m.y - y)
        dragDistance = 0; lastMouse = m
    }

    func dragMoved() {
        let m = NSEvent.mouseLocation
        dragDistance += hypot(m.x - lastMouse.x, m.y - lastMouse.y)
        lastMouse = m
        if dragDistance > 6 && !walking { js("petEvent('grabbed')"); setWalking(true) }
    }

    func dragEnded() {
        dragging = false
        setWalking(false)
        if dragDistance <= 6 {                 // 그냥 클릭
            js("petEvent('click')")
            openDashboard()
        } else {
            falling = true; vy = 0
            targetX = nil; pauseUntil = Date().addingTimeInterval(2)
        }
    }

    // MARK: 매 프레임
    func setFacing(_ d: Int) { if d != facing { facing = d; js("petFace(\(d))") } }
    func setWalking(_ b: Bool) { if b != walking { walking = b; js("petWalk(\(b))") } }

    func tick() {
        let now = Date()
        let dt = CGFloat(min(now.timeIntervalSince(lastTick), 0.1))
        lastTick = now
        if hidden { return }
        let mouse = NSEvent.mouseLocation
        let vf = (panel.screen ?? NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        let ground = vf.minY

        if dragging {
            x = mouse.x - grabOffset.x
            y = mouse.y - grabOffset.y
        } else if falling {
            vy -= 2600 * dt
            y += vy * dt
            if y <= ground { y = ground; vy = 0; falling = false; js("petEvent('land')") }
        } else {
            y = ground
            think(now: now, mouse: mouse, vf: vf, dt: dt)
            x = min(max(x, vf.minX - petRect.minX), vf.maxX - petRect.maxX)
        }
        panel.setFrameOrigin(NSPoint(x: x, y: y))

        // 펫 몸 위에서만 마우스를 받고, 나머지 투명한 부분은 클릭이 뒤로 통과
        let hit = NSRect(x: x + petRect.minX, y: y + petRect.minY, width: petRect.width, height: petRect.height)
        let through = !(dragging || hit.contains(mouse))
        if through != clickThrough { clickThrough = through; panel.ignoresMouseEvents = through }
    }

    /// 모드별 행동: 작업 중엔 커서 근처로 와서 지켜보고, 딴짓하면 커서로 달려와 항의한다.
    func think(now: Date, mouse: CGPoint, vf: NSRect, dt: CGFloat) {
        let cursorX = mouse.x - W / 2
        switch mode {
        case "idle":
            setWalking(false)
            return
        case "warning", "penalty":
            if abs(cursorX - x) > 110 { targetX = cursorX + (x < cursorX ? -70 : 70); speed = 300 }
        default:
            if targetX == nil && now >= pauseUntil {
                let r = Double.random(in: 0..<1)
                let watch = mode == "work" ? r < 0.55 : r < 0.2
                targetX = watch ? cursorX + CGFloat.random(in: -180...180)
                                : CGFloat.random(in: vf.minX...(vf.maxX - W))
                speed = mode == "break" ? 35 : mode == "work" ? 80 : 60
            }
        }
        if let t = targetX {
            let d = t - x
            if abs(d) < 3 {
                targetX = nil
                let rest: ClosedRange<Double> = mode == "break" ? 8...20 : mode == "work" ? 5...14 : 3...9
                pauseUntil = now.addingTimeInterval(Double.random(in: rest))
                setWalking(false)
            } else {
                x += (d > 0 ? 1 : -1) * min(abs(d), speed * dt)
                setFacing(d > 0 ? 1 : -1)
                setWalking(true)
                return
            }
        }
        // 멈춰 있을 땐 커서 쪽을 바라본다 (지켜보는 중)
        setFacing(mouse.x > x + W / 2 ? 1 : -1)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = PetController()
app.run()
