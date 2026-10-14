// Figmon.app — 메뉴 막대 앱 + 데스크톱 펫 + 대시보드 창.
//
// - 앱으로 실행하면 번들 안의 server.py(감지·경험치 계산)를 직접 띄우고, 종료할 때 같이 끈다.
// - server.py가 이 바이너리를 띄운 경우(FIGMON_PARENT, 개발용)에는 서버를 따로 띄우지 않는다.
// - 펫 그림은 /pet 페이지, 대시보드는 / 페이지(WKWebView)가 그리고,
//   여기서는 창 위치(걷기, 커서 지켜보기, 드래그, 낙하), 메뉴, 저장/공유 같은 네이티브 기능을 맡는다.
//
// 빌드: ./build_app.sh  (dist/Figmon.app)
import Cocoa
import ServiceManagement
import UniformTypeIdentifiers
import WebKit

let env = ProcessInfo.processInfo.environment
let port = env["FIGMON_PORT"] ?? "7777"
let base = "http://127.0.0.1:\(port)"
let parentPID = env["FIGMON_PARENT"].flatMap { Int32($0) }
let W: CGFloat = 300, H: CGFloat = 320

func post(_ path: String, _ body: [String: Any]) {
    var req = URLRequest(url: URL(string: base + path)!)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try? JSONSerialization.data(withJSONObject: body)
    URLSession.shared.dataTask(with: req).resume()
}

// MARK: - server.py 실행 관리

final class ServerManager {
    var proc: Process?

    /// server.py가 있는 폴더 (앱 번들 Resources 또는 개발 폴더)
    func resourceDir() -> URL? {
        var dirs: [URL] = []
        if let r = Bundle.main.resourceURL { dirs.append(r) }
        if let exe = Bundle.main.executableURL?.deletingLastPathComponent() {
            dirs += [exe, exe.deletingLastPathComponent()]
        }
        return dirs.first { FileManager.default.fileExists(atPath: $0.appendingPathComponent("server.py").path) }
    }

    func isUp(_ done: @escaping (Bool) -> Void) {
        URLSession.shared.dataTask(with: URL(string: base + "/api/meta")!) { _, r, _ in
            DispatchQueue.main.async { done((r as? HTTPURLResponse)?.statusCode == 200) }
        }.resume()
    }

    func start(_ ready: @escaping (Bool) -> Void) {
        isUp { up in
            if up { return ready(true) }   // 이미 실행 중인 서버가 있으면 그대로 사용
            guard let dir = self.resourceDir() else { return ready(false) }
            let logDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".figmon")
            try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
            let logURL = logDir.appendingPathComponent("server.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil)

            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            p.arguments = [dir.appendingPathComponent("server.py").path, "--no-open", "--no-pet"]
            var e = env
            e["FIGMON_PORT"] = port
            e["PYTHONDONTWRITEBYTECODE"] = "1"   // 앱 번들 안에 캐시 파일을 만들지 않게
            e["PYTHONUNBUFFERED"] = "1"
            p.environment = e
            if let log = try? FileHandle(forWritingTo: logURL) { p.standardOutput = log; p.standardError = log }
            do { try p.run() } catch { return ready(false) }
            self.proc = p
            self.wait(tries: 60, ready)
        }
    }

    private func wait(tries: Int, _ ready: @escaping (Bool) -> Void) {
        isUp { up in
            if up { ready(true) }
            else if tries == 0 || !(self.proc?.isRunning ?? false) { ready(false) }
            else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.wait(tries: tries - 1, ready) } }
        }
    }

    func stop() { if proc?.isRunning == true { proc?.terminate() } }
}

// MARK: - 대시보드 창

final class Dashboard: NSObject, NSWindowDelegate, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow?
    var web: WKWebView?

    func show(title: Bool = false) {
        if window == nil { build(title: title) }
        else if title { web?.load(URLRequest(url: URL(string: base + "/?title=1")!)) }
        NSApp.setActivationPolicy(.regular)       // 창이 열려 있는 동안은 Dock·⌘Tab에 보이게
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    private func build(title: Bool) {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "figmon")
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 860),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        w.title = "Figmon"
        w.minSize = NSSize(width: 360, height: 520)
        w.isReleasedWhenClosed = false
        w.delegate = self
        w.backgroundColor = NSColor(red: 0.07, green: 0.08, blue: 0.11, alpha: 1)
        w.center()
        w.setFrameAutosaveName("FigmonDashboard")
        let v = WKWebView(frame: w.contentView!.bounds, configuration: cfg)
        v.autoresizingMask = [.width, .height]
        v.uiDelegate = self
        v.navigationDelegate = self
        v.setValue(false, forKey: "drawsBackground")
        w.contentView?.addSubview(v)
        v.load(URLRequest(url: URL(string: base + (title ? "/?title=1" : "/"))!))
        window = w
        web = v
    }

    func windowWillClose(_ n: Notification) { NSApp.setActivationPolicy(.accessory) }

    // window.open(...) → 기본 브라우저
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction,
                 windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let u = a.request.url { NSWorkspace.shared.open(u) }
        return nil
    }

    // 외부 링크는 앱 안이 아니라 브라우저로
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, let host = u.host, host != "127.0.0.1", host != "localhost", a.targetFrame?.isMainFrame ?? true,
           a.navigationType == .linkActivated {
            NSWorkspace.shared.open(u)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func userContentController(_ uc: WKUserContentController, didReceive msg: WKScriptMessage) {
        guard let d = msg.body as? [String: Any], let type = d["type"] as? String else { return }
        switch type {
        case "saveImage":
            guard let data = imageData(d["dataURL"]), let win = window else { return }
            let panel = NSSavePanel()
            panel.allowedContentTypes = [.png]
            panel.nameFieldStringValue = d["name"] as? String ?? "figmon.png"
            panel.beginSheetModal(for: win) { r in if r == .OK, let u = panel.url { try? data.write(to: u) } }
        case "shareImage":
            guard let data = imageData(d["dataURL"]), let img = NSImage(data: data), let v = web else { return }
            var items: [Any] = [img]
            if let t = d["text"] as? String { items.append(t) }
            let picker = NSSharingServicePicker(items: items)
            let r = v.bounds
            picker.show(relativeTo: NSRect(x: r.midX - 1, y: r.midY, width: 2, height: 2), of: v, preferredEdge: .minY)
        case "copy":
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(d["text"] as? String ?? "", forType: .string)
        case "openURL":
            if let s = d["url"] as? String, let u = URL(string: s) { NSWorkspace.shared.open(u) }
        default: break
        }
    }

    private func imageData(_ any: Any?) -> Data? {
        guard let s = any as? String, let comma = s.firstIndex(of: ",") else { return nil }
        return Data(base64Encoded: String(s[s.index(after: comma)...]))
    }
}

// MARK: - 데스크톱 펫

final class PetPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// 웹뷰 위에 덮어서 마우스를 직접 받는 뷰. 포커스를 뺏지 않고 첫 클릭부터 반응하며, VoiceOver에는 버튼으로 보인다.
final class GrabView: NSView {
    weak var ctl: PetController?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with e: NSEvent) { ctl?.dragBegan() }
    override func mouseDragged(with e: NSEvent) { ctl?.dragMoved() }
    override func mouseUp(with e: NSEvent) { ctl?.dragEnded(clicks: e.clickCount) }
    override func rightMouseDown(with e: NSEvent) {
        guard let menu = ctl?.app?.makeMenu() else { return }
        NSMenu.popUpContextMenu(menu, with: e, for: self)
    }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func accessibilityPerformPress() -> Bool { ctl?.app?.dashboard.show(); return true }   // VoiceOver는 한 번에 대시보드
}

final class PetController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    weak var app: AppDelegate?
    let panel: PetPanel
    let web: WKWebView
    let grab = GrabView()

    var petRect = NSRect(x: 110, y: 4, width: 80, height: 90)   // 창 안에서 펫 몸이 차지하는 영역(아래 왼쪽 기준)
    var mode = "neutral"
    var x: CGFloat = 0, y: CGFloat = 0, vy: CGFloat = 0
    var targetX: CGFloat? = nil
    var speed: CGFloat = 60          // 목표 최고 속도
    var vx: CGFloat = 0              // 현재 속도 (가속·감속해서 자연스럽게)
    var lookUntil = Date()           // 출발 전에 목표 쪽을 잠깐 바라보는 시간
    var hovering = false, lastHover = Date.distantPast, nextLook = Date()
    var pauseUntil = Date()
    var dragging = false, falling = false, grabOffset = CGPoint.zero, dragDistance: CGFloat = 0, lastMouse = CGPoint.zero
    var facing = 0, gait = 0, lastTick = Date(), hidden = false, clickThrough = true   // gait: 0 멈춤, 1 걷기, 2 달리기

    init(app: AppDelegate) {
        self.app = app
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
        panel.title = "Figmon 펫"

        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        web.setAccessibilityElement(false)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: W, height: H))
        root.addSubview(web)
        grab.frame = root.bounds
        grab.ctl = self
        grab.setAccessibilityLabel("Figmon 펫. 누르면 대시보드가 열려요")
        root.addSubview(grab)
        panel.contentView = root

        let vf = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        x = vf.maxX - W - 60
        y = vf.minY
        panel.setFrameOrigin(NSPoint(x: x, y: y))
        if UserDefaults.standard.bool(forKey: "petHidden") { hidden = true } else { panel.orderFrontRegardless() }
        web.load(URLRequest(url: URL(string: base + "/pet")!))

        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
    }

    func toggleHidden() {
        hidden.toggle()
        UserDefaults.standard.set(hidden, forKey: "petHidden")
        hidden ? panel.orderOut(nil) : panel.orderFrontRegardless()
    }

    func callPet() {
        if hidden { toggleHidden() }
        targetX = NSEvent.mouseLocation.x - W / 2
        speed = 260
        js("petEvent('come')")
    }

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
            app?.setStatus(title: d["text"] as? String ?? "🥚", label: d["label"] as? String)
            if let l = d["label"] as? String { grab.setAccessibilityLabel("\(l). 누르면 대시보드가 열려요") }
        default: break
        }
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { retryLoad() }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { retryLoad() }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { retryLoad() }
    func retryLoad() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.web.load(URLRequest(url: URL(string: base + "/pet")!))
        }
    }

    // MARK: 드래그
    var grabbed = false
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
        if dragDistance > 6 && !grabbed { grabbed = true; js("petEvent('grabbed')") }
    }

    func dragEnded(clicks: Int) {
        dragging = false
        grabbed = false
        setGait(0)
        if dragDistance <= 6 {
            if clicks >= 2 {                 // 두 번 누르기 → 대시보드
                js("petEvent('dashboard')")
                app?.dashboard.show()
            } else {                         // 한 번 → 반응하고 잠깐 멈춰서 나를 봄
                js("petEvent('click')")
                targetX = nil; vx = 0
                pauseUntil = Date().addingTimeInterval(4)
                setFacing(NSEvent.mouseLocation.x > x + W / 2 ? 1 : -1)
            }
        } else {
            falling = true; vy = 0
            targetX = nil; pauseUntil = Date().addingTimeInterval(2)
        }
    }

    // MARK: 매 프레임
    func setFacing(_ d: Int) { if d != facing { facing = d; js("petFace(\(d))") } }
    func setGait(_ g: Int) { if g != gait { gait = g; js("petWalk(\(g))") } }

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
            if y <= ground { y = ground; vy = 0; vx = 0; falling = false; js("petEvent('land')") }
        } else {
            y = ground
            think(now: now, mouse: mouse, vf: vf, dt: dt)
            x = min(max(x, vf.minX - petRect.minX), vf.maxX - petRect.maxX)
        }
        panel.setFrameOrigin(NSPoint(x: x, y: y))

        // 펫 몸 위에서만 마우스를 받고, 나머지 투명한 부분은 클릭이 뒤로 통과
        let hit = NSRect(x: x + petRect.minX, y: y + petRect.minY, width: petRect.width, height: petRect.height)
        let over = hit.contains(mouse)
        let through = !(dragging || over)
        if through != clickThrough { clickThrough = through; panel.ignoresMouseEvents = through }
        // 커서가 올라오면 알아챔 (자주 반복하지 않게 6초 간격)
        if over && !hovering && !dragging && now.timeIntervalSince(lastHover) > 6 {
            lastHover = now
            js("petEvent('hover')")
            if targetX != nil { targetX = nil; pauseUntil = now.addingTimeInterval(3) }
        }
        hovering = over
    }

    /// 모드별 행동: 작업 중엔 가끔 커서 근처로 와서 지켜보고, 딴짓하면 커서로 달려와 항의한다.
    /// 시스템의 "동작 줄이기"가 켜져 있으면 스스로 돌아다니지 않는다(부르기만 가능).
    func think(now: Date, mouse: CGPoint, vf: NSRect, dt: CGFloat) {
        let cursorX = mouse.x - W / 2
        let calm = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        switch mode {
        case "idle", "paused":
            if speed < 200 { targetX = nil }
        case "warning", "penalty":
            if !calm && abs(cursorX - x) > 120 && targetX == nil {
                targetX = cursorX + (x < cursorX ? -80 : 80); speed = 220
            }
        default:
            if !calm && targetX == nil && now >= pauseUntil {
                let watch = Double.random(in: 0..<1) < (mode == "work" ? 0.6 : 0.25)
                var t = watch ? cursorX + CGFloat.random(in: -160...160) : CGFloat.random(in: vf.minX...(vf.maxX - W))
                if abs(t - x) < 40 { t = x + (t > x ? 60 : -60) }
                targetX = t
                speed = mode == "break" ? 32 : 55
                lookUntil = now.addingTimeInterval(0.6)          // 출발 전에 갈 쪽을 먼저 봄
                setFacing(t > x ? 1 : -1)
            }
        }
        if let t = targetX, now >= lookUntil {
            let d = t - x
            // 도착할수록 천천히 (arrive), 출발은 부드럽게 (가속도 제한)
            let want = (d > 0 ? 1 : -1) * min(speed, abs(d) * 2.2)
            let accel: CGFloat = speed > 150 ? 520 : 160
            vx += max(-accel * dt, min(accel * dt, want - vx))
            x += vx * dt
            if abs(d) < 2 && abs(vx) < 12 {
                targetX = nil; vx = 0
                let rest: ClosedRange<Double> = mode == "break" ? 14...30 : mode == "work" ? 10...24 : 6...16
                pauseUntil = now.addingTimeInterval(Double.random(in: rest))
                speed = 55
                setGait(0)
            } else {
                setFacing(vx > 0 ? 1 : -1)
                setGait(abs(vx) > 140 ? 2 : abs(vx) > 6 ? 1 : 0)
                return
            }
        } else if targetX != nil {
            setGait(0)
            return
        }
        setGait(0)
        // 멈춰 있을 땐 가까운 커서를 바라본다 (멀리 있으면 가끔 두리번)
        if abs(mouse.x - (x + W / 2)) < 500 { setFacing(mouse.x > x + W / 2 ? 1 : -1) }
        else if now >= nextLook {
            nextLook = now.addingTimeInterval(Double.random(in: 4...9))
            setFacing(facing > 0 ? -1 : 1)
        }
    }
}

// MARK: - 앱

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let server = ServerManager()
    let dashboard = Dashboard()
    var pet: PetController?
    var status: NSStatusItem?
    let isBundle = Bundle.main.bundlePath.hasSuffix(".app")

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildMainMenu()
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "🥚"
        item.button?.setAccessibilityLabel("Figmon")
        let menu = NSMenu()
        menu.delegate = self            // 열 때마다 최신 상태로 다시 그림
        item.menu = menu
        status = item

        if let pid = parentPID {        // 개발 모드: server.py가 띄움, 서버가 꺼지면 같이 종료
            Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in if kill(pid, 0) != 0 { NSApp.terminate(nil) } }
            pet = PetController(app: self)
            return
        }
        server.start { ok in
            if ok {
                self.pet = PetController(app: self)
                self.dashboard.show(title: true)   // 실행하면 타이틀 화면부터
            } else {
                self.failAlert()
            }
        }
    }

    func applicationWillTerminate(_ n: Notification) { server.stop() }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows: Bool) -> Bool { dashboard.show(); return true }

    func failAlert() {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = "Figmon을 시작하지 못했어요"
        a.informativeText = "Python 3가 필요해요. 터미널에서 아래 명령으로 Command Line Tools를 설치한 뒤 다시 실행해 주세요.\n\nxcode-select --install\n\n자세한 기록: ~/.figmon/server.log"
        a.alertStyle = .warning
        a.addButton(withTitle: "종료")
        a.addButton(withTitle: "설치 창 열기")
        if a.runModal() == .alertSecondButtonReturn {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select"); p.arguments = ["--install"]
            try? p.run(); p.waitUntilExit()
        }
        NSApp.terminate(nil)
    }

    func setStatus(title: String, label: String?) {
        status?.button?.title = title
        status?.button?.setAccessibilityLabel(label.map { "Figmon: \($0)" } ?? "Figmon")
    }

    // MARK: 메뉴 막대 메뉴 (펫 우클릭 메뉴와 같음)
    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        for item in makeMenu().items { item.menu?.removeItem(item); menu.addItem(item) }
    }

    func makeMenu() -> NSMenu {
        let m = NSMenu()
        add(m, "대시보드 열기", #selector(openDashboard), "d")
        add(m, "펫을 내 쪽으로 부르기", #selector(callPet), "")
        add(m, pet?.hidden == true ? "펫 보이기" : "펫 숨기기", #selector(toggleHidden), "")
        m.addItem(.separator())

        let pause = NSMenuItem(title: "추적 일시정지 (회의·외출)", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        for (t, min) in [("30분", 30), ("1시간", 60), ("2시간", 120), ("오늘 하루", 60 * 12)] {
            let i = add(sub, t, #selector(pauseFor(_:)), "")
            i.tag = min
        }
        sub.addItem(.separator())
        add(sub, "다시 시작", #selector(pauseFor(_:)), "").tag = 0
        pause.submenu = sub
        m.addItem(pause)

        if isBundle {
            let login = add(m, "로그인할 때 자동 실행", #selector(toggleLogin), "")
            login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        }
        m.addItem(.separator())
        add(m, "Figmon 종료", #selector(quit), "q")
        return m
    }

    @discardableResult
    func add(_ m: NSMenu, _ title: String, _ sel: Selector, _ key: String) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        i.target = self
        m.addItem(i)
        return i
    }

    @objc func openDashboard() { dashboard.show() }
    @objc func callPet() { pet?.callPet() }
    @objc func toggleHidden() { pet?.toggleHidden() }
    @objc func pauseFor(_ s: NSMenuItem) { post("/api/pause", ["minutes": s.tag]) }
    @objc func quit() { NSApp.terminate(nil) }

    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else { try SMAppService.mainApp.register() }
        } catch {
            let a = NSAlert()
            a.messageText = "자동 실행을 바꾸지 못했어요"
            a.informativeText = "Figmon.app을 '응용 프로그램' 폴더로 옮긴 뒤 다시 시도해 주세요.\n(\(error.localizedDescription))"
            a.runModal()
        }
    }

    // 대시보드 창이 앞에 있을 때 쓰는 기본 메뉴 (⌘Q, ⌘W, 복사·붙여넣기 등)
    func buildMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Figmon 정보", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Figmon 가리기", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Figmon 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "편집")
        edit.addItem(withTitle: "실행 취소", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "실행 복귀", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "오려두기", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "복사하기", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "붙여넣기", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "모두 선택", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "윈도우")
        win.addItem(withTitle: "닫기", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        win.addItem(withTitle: "최소화", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winItem.submenu = win
        NSApp.mainMenu = main
        NSApp.windowsMenu = win
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
