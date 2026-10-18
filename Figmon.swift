// Figmon.app — 메뉴 막대 앱 + 데스크톱 펫 + 대시보드 창.
//
// - 앱으로 실행하면 번들 안의 server.py(감지·경험치 계산)를 직접 띄우고, 종료할 때 같이 끈다.
// - server.py가 이 바이너리를 띄운 경우(FIGMON_PARENT, 개발용)에는 서버를 따로 띄우지 않는다.
// - 펫 그림은 /pet 페이지, 대시보드는 / 페이지(WKWebView)가 그리고,
//   여기서는 창 위치(걷기, 커서 지켜보기, 드래그, 낙하), 메뉴, 저장/공유 같은 네이티브 기능을 맡는다.
//
// 빌드: ./build_app.sh  (dist/Figmon.app)
import Cocoa
import UniformTypeIdentifiers
import WebKit

let env = ProcessInfo.processInfo.environment
let port = env["FIGMON_PORT"] ?? "7777"
let base = "http://127.0.0.1:\(port)"
let parentPID = env["FIGMON_PARENT"].flatMap { Int32($0) }
var W: CGFloat = 300, H: CGFloat = 320   // 펫 창 크기 (펫 크기 설정에 따라 바뀜)
let args = CommandLine.arguments
let launchedInBackground = args.contains("--background")   // 로그인 자동 실행: 작업을 시작하면 펫이 걸어 나옴
let justUpdated = args.contains("--updated")
let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"

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
        case "petSize":   // 대시보드 슬라이더 (저장은 대시보드가 함)
            delegate.pet?.applyLiveSize(d["size"] as? Double ?? 1, save: false)
        case "checkUpdate":
            delegate.updater.check(manual: true)
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
    // macOS는 창이 화면 위로 못 넘어가게 위치를 고치는데, 펫 창은 크면 투명한 윗부분이 넘어가야
    // 펫 몸이 화면 맨 위까지 갈 수 있다. 화면 안에 머무는 건 PetController가 펫 몸 기준으로 직접 맞춘다.
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
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
    var stuckX: CGFloat = 0, stuckSince = Date(), stuckGoal: CGFloat? = nil   // 걷는데 제자리면(벽에 막힘) 몇 초 뒤 다른 곳으로
    var hovering = false, lastHover = Date.distantPast, nextLook = Date()
    // 쓰다듬기(커서 비비기) 감지
    var lastMouseX: CGFloat = 0, rubSign = 0, rubTravel: CGFloat = 0, rubTurns = 0, rubLast = Date.distantPast, lastPet = Date.distantPast
    // 던지기
    var dragSamples: [(t: TimeInterval, p: CGPoint)] = []
    var thrown = false, bounced = false, vxAir: CGFloat = 0
    var pauseUntil = Date()
    var dragging = false, falling = false, grabOffset = CGPoint.zero, dragDistance: CGFloat = 0, lastMouse = CGPoint.zero
    var facing = 0, gait = 0, lastTick = Date(), hidden = false, clickThrough = true   // gait: 0 멈춤, 1 걷기, 2 달리기

    init(app: AppDelegate, startHidden: Bool = false) {
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
        web.autoresizingMask = [.width, .height]
        grab.autoresizingMask = [.width, .height]
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
        if startHidden || UserDefaults.standard.bool(forKey: "petHidden") { hidden = true } else { panel.orderFrontRegardless() }
        web.load(URLRequest(url: URL(string: base + "/pet")!))

        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
    }

    func toggleHidden() {
        hidden.toggle()
        UserDefaults.standard.set(hidden, forKey: "petHidden")
        hidden ? panel.orderOut(nil) : panel.orderFrontRegardless()
    }

    var petSize: Double = 1

    /// 펫 크기에 맞춰 창도 키움 (크게 하거나 점프·회전해도 잘리지 않게). 가운데·바닥 위치는 그대로
    func resize(to size: Double) {
        petSize = size
        let f = CGFloat(max(1, size * 1.15))
        let newW = (300 * f).rounded(), newH = (320 * f).rounded()
        guard newW != W || newH != H else { return }
        x -= (newW - W) / 2
        W = newW; H = newH
        panel.setFrame(NSRect(x: x, y: y, width: W, height: H), display: true)
    }

    /// 슬라이더로 바꾸는 중: 창과 펫을 바로 바꾸고, 저장은 잠깐 멈췄을 때 한 번만
    var saveSizeWork: DispatchWorkItem?
    func applyLiveSize(_ size: Double, save: Bool) {
        let v = min(1.6, max(0.6, size))
        resize(to: v)
        js("setSizeLive(\(v))")
        if save {
            saveSizeWork?.cancel()
            let w = DispatchWorkItem { post("/api/config", ["pet_size": v]) }
            saveSizeWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: w)
        }
    }

    /// 작업 앱을 켜서 불려 나옴: 숨어 있었으면 커서와 가까운 화면 가장자리에서 걸어 나온다
    func summon() {
        let mouse = NSEvent.mouseLocation
        let vf = (panel.screen ?? NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        if hidden {
            hidden = false
            UserDefaults.standard.set(false, forKey: "petHidden")
            x = mouse.x > vf.midX ? vf.maxX - petRect.maxX : vf.minX - petRect.minX
            y = vf.minY; falling = false
            panel.setFrameOrigin(NSPoint(x: x, y: y))
            panel.orderFrontRegardless()
        }
        targetX = mouse.x - W / 2 + (Bool.random() ? 120 : -120)
        speed = 170; vx = 0
        lookUntil = Date().addingTimeInterval(0.4)
        pauseUntil = Date()
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
        case "summon":
            summon()
        case "size":
            resize(to: d["size"] as? Double ?? 1)
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
        dragSamples = [(ProcessInfo.processInfo.systemUptime, m)]
    }

    func dragMoved() {
        let m = NSEvent.mouseLocation
        dragDistance += hypot(m.x - lastMouse.x, m.y - lastMouse.y)
        lastMouse = m
        let t = ProcessInfo.processInfo.systemUptime
        dragSamples.append((t, m))
        dragSamples.removeAll { t - $0.t > 0.12 }   // 놓기 직전 0.12초로 던지는 속도 계산
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
            falling = true; vy = 0; vxAir = 0; thrown = false; bounced = false
            targetX = nil; vx = 0; pauseUntil = Date().addingTimeInterval(2)
            if let a = dragSamples.first, let b = dragSamples.last, b.t - a.t > 0.01 {
                let vel = CGPoint(x: (b.p.x - a.p.x) / CGFloat(b.t - a.t), y: (b.p.y - a.p.y) / CGFloat(b.t - a.t))
                if hypot(vel.x, vel.y) > 900 {       // 세게 던짐 → 날아감
                    thrown = true
                    vxAir = max(-2600, min(2600, vel.x))
                    vy = max(-2600, min(2600, vel.y))
                    js("petEvent('fly')")
                }
            }
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
            x += vxAir * dt
            vxAir *= CGFloat(pow(0.6, Double(dt)))          // 공기 저항
            // 화면 가장자리에 튕김
            let minX = vf.minX - petRect.minX, maxX = vf.maxX - petRect.maxX
            if x < minX { x = minX; vxAir = abs(vxAir) * 0.5 }
            if x > maxX { x = maxX; vxAir = -abs(vxAir) * 0.5 }
            // 화면 위쪽은 창이 아니라 펫 몸 윗부분 기준 (창이 커져도 던지는 범위가 줄지 않게)
            let top = vf.maxY - petRect.maxY
            if y > top { y = top; vy = -abs(vy) * 0.3 }
            if y <= ground {
                let impact = -vy
                y = ground
                if thrown && !bounced && impact > 900 {     // 한 번 통 튕기고
                    bounced = true; vy = impact * 0.35; vxAir *= 0.6
                } else {
                    vy = 0; vx = 0; vxAir = 0; falling = false
                    js(thrown ? "petEvent('surprised')" : "petEvent('land')")
                    if thrown { pauseUntil = now.addingTimeInterval(4) }
                    thrown = false
                }
            }
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
        // 커서를 펫 위에서 좌우로 비비면 쓰다듬기 (경험치와는 무관한 반응만)
        let dxm = mouse.x - lastMouseX
        lastMouseX = mouse.x
        if over && !dragging && !falling {
            if abs(dxm) > 1.5 {
                let sign = dxm > 0 ? 1 : -1
                if sign != rubSign && rubTravel > 6 { rubTurns += 1; rubTravel = 0 }
                rubSign = sign; rubTravel += abs(dxm); rubLast = now
            }
            if now.timeIntervalSince(rubLast) > 0.6 { rubTurns = 0 }
            if rubTurns >= 3 && now.timeIntervalSince(lastPet) > 1.2 {
                lastPet = now; rubTurns = 0
                js("petEvent('pet')")
                targetX = nil; vx = 0; pauseUntil = now.addingTimeInterval(4)
            }
        } else { rubTurns = 0 }
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
        if var t = targetX, now >= lookUntil {
            // 목적지가 화면 밖(커서 근처·크기 변경)이면 끝에 닿아도 도착을 못 해서 벽에 계속 박는다 → 갈 수 있는 범위로
            let lo = vf.minX - petRect.minX, hi = vf.maxX - petRect.maxX
            t = min(max(t, lo), hi); targetX = t
            // 그래도 2초 넘게 제자리걸음이면 반대쪽 아무 데나로 방향을 바꾼다
            if t != stuckGoal || abs(x - stuckX) > 4 { stuckGoal = t; stuckX = x; stuckSince = now }
            else if now.timeIntervalSince(stuckSince) > 2 {
                let mid = (lo + hi) / 2
                t = x > mid ? CGFloat.random(in: lo...max(lo, mid)) : CGFloat.random(in: min(hi, mid)...hi)
                targetX = t; vx = 0; speed = 55
                lookUntil = now.addingTimeInterval(0.4)
                setFacing(t > x ? 1 : -1)
                setGait(0)
                return
            }
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

// MARK: - 로그인 자동 실행 (LaunchAgent, --background로 조용히 시작)

enum LoginItem {
    static let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents/io.github.jinjwon.figmon.plist")
    static var enabled: Bool { FileManager.default.fileExists(atPath: url.path) }

    static func set(_ on: Bool) {
        if on {
            let plist: [String: Any] = [
                "Label": "io.github.jinjwon.figmon",
                "ProgramArguments": ["/usr/bin/open", "-a", Bundle.main.bundlePath, "--args", "--background"],
                "RunAtLoad": true,
            ]
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            (plist as NSDictionary).write(to: url, atomically: true)
        } else {
            try? FileManager.default.removeItem(at: url)
        }
    }
}

// MARK: - 응용 프로그램 폴더로 옮기기
// 다운로드 폴더 등에서 바로 실행하면 macOS가 앱을 읽기 전용 임시 위치(App Translocation)에서 돌려서
// 자동 업데이트로 앱을 바꿔 끼울 수 없다. 버튼 한 번으로 응용 프로그램 폴더에 복사하고 거기서 다시 켠다.

enum Mover {
    static let path = Bundle.main.bundlePath
    static var inApplications: Bool {
        path.hasPrefix("/Applications/") || path.hasPrefix(NSHomeDirectory() + "/Applications/")
    }
    static var translocated: Bool { path.contains("/AppTranslocation/") }
    static var canSelfUpdate: Bool {
        !translocated && FileManager.default.isWritableFile(atPath: (path as NSString).deletingLastPathComponent)
    }

    /// 처음 켤 때 (응용 프로그램 폴더 밖이면) 또는 업데이트가 막혔을 때 묻기
    static func offer(reason: String) {
        guard path.hasSuffix(".app"), !inApplications || !canSelfUpdate else { return }
        if reason == "launch" && UserDefaults.standard.bool(forKey: "declinedMove-\(path)") { return }
        let a = NSAlert()
        a.messageText = "Figmon을 '응용 프로그램' 폴더로 옮길까요?"
        a.informativeText = "지금 위치(다운로드 폴더 등)에서는 macOS 보안 때문에 자동 업데이트를 할 수 없어요.\n옮기면 다음부터 새 버전이 나올 때 알아서 업데이트돼요. 키우던 포켓몬은 그대로예요."
        a.addButton(withTitle: "옮기고 다시 켜기")
        a.addButton(withTitle: "나중에")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else {
            UserDefaults.standard.set(true, forKey: "declinedMove-\(path)")
            return
        }
        for dir in ["/Applications", NSHomeDirectory() + "/Applications"] {
            let dest = dir + "/Figmon.app"
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/sh")
            // 옛 버전이 있으면 지우고 복사, 다운로드 표시(quarantine)도 떼서 다시 막히지 않게
            p.arguments = ["-c", "rm -rf \"$1\" && /usr/bin/ditto \"$0\" \"$1\" && /usr/bin/xattr -dr com.apple.quarantine \"$1\"", path, dest]
            try? p.run(); p.waitUntilExit()
            if p.terminationStatus == 0 {
                let relaunch = Process()
                relaunch.executableURL = URL(fileURLWithPath: "/bin/sh")
                relaunch.arguments = ["-c", "sleep 1.5; /usr/bin/open -a \"$0\"", dest]
                try? relaunch.run()
                NSApp.terminate(nil)
                return
            }
        }
        let f = NSAlert()
        f.messageText = "옮기지 못했어요"
        f.informativeText = "Finder에서 Figmon.app을 '응용 프로그램' 폴더로 직접 끌어다 놓은 뒤 거기서 실행해 주세요."
        f.runModal()
    }
}

// MARK: - 자동 업데이트 (GitHub 최신 릴리스 → 앱 교체 → 다시 실행)

final class Updater {
    var busy = false

    func start() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { self.check(manual: false) }
        Timer.scheduledTimer(withTimeInterval: 3600, repeats: true) { _ in self.check(manual: false) }   // 1시간마다
    }

    static func newer(_ a: String, than b: String) -> Bool {
        let num = { (v: String) in v.split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) } }
        return num(a).lexicographicallyPrecedes(num(b)) == false && num(a) != num(b)
    }

    func check(manual: Bool) {
        guard !busy, Bundle.main.bundlePath.hasSuffix(".app") else { return }
        // 다운로드 폴더 등에서 실행 중이면 바꿔 끼울 수 없음 → 응용 프로그램 폴더로 옮기기부터
        if !Mover.canSelfUpdate {
            if manual { Mover.offer(reason: "update") }
            return
        }
        // 설정에서 자동 업데이트를 끈 경우 (수동 확인은 항상 가능)
        URLSession.shared.dataTask(with: URL(string: base + "/api/state")!) { data, _, _ in
            let cfg = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any])?["config"] as? [String: Any]
            let auto = cfg?["auto_update"] as? Bool ?? true
            DispatchQueue.main.async { if manual || auto { self.fetchLatest(manual: manual) } }
        }.resume()
    }

    /// 최신 릴리스: API 대신 releases/latest 리디렉트를 따라가 태그를 알아냄 (요청 횟수 제한 없음)
    private func fetchLatest(manual: Bool) {
        var req = URLRequest(url: URL(string: "https://github.com/jinjwon/figmon/releases/latest")!)
        req.setValue("Figmon", forHTTPHeaderField: "User-Agent")
        req.httpMethod = "HEAD"
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            guard let final = resp?.url, final.path.contains("/releases/tag/") else {
                if manual { DispatchQueue.main.async { self.tell("업데이트를 확인하지 못했어요", "인터넷 연결을 확인해 주세요.") } }
                return
            }
            let tag = final.lastPathComponent
            let ver = tag.trimmingCharacters(in: CharacterSet(charactersIn: "v"))
            guard Updater.newer(ver, than: appVersion),
                  let zipURL = URL(string: "https://github.com/jinjwon/figmon/releases/download/\(tag)/Figmon-macOS.zip") else {
                if manual { DispatchQueue.main.async { self.tell("최신 버전이에요", "Figmon v\(appVersion)") } }
                return
            }
            DispatchQueue.main.async {
                if manual {
                    let a = NSAlert()
                    a.messageText = "새 버전 v\(ver)이 있어요"
                    a.informativeText = "지금 업데이트하고 다시 시작할까요? 키우던 포켓몬은 그대로예요."
                    a.addButton(withTitle: "업데이트")
                    a.addButton(withTitle: "나중에")
                    NSApp.activate(ignoringOtherApps: true)
                    guard a.runModal() == .alertFirstButtonReturn else { return }
                }
                self.install(zipURL, version: ver)
            }
        }.resume()
    }

    private func install(_ zipURL: URL, version: String) {
        busy = true
        URLSession.shared.downloadTask(with: zipURL) { file, _, err in
            let fm = FileManager.default
            let work = fm.temporaryDirectory.appendingPathComponent("figmon-update-\(UUID().uuidString)")
            defer { DispatchQueue.main.async { self.busy = false } }
            guard let file = file, err == nil else { return self.failed(version, "새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요.") }
            do {
                try fm.createDirectory(at: work, withIntermediateDirectories: true)
                let zip = work.appendingPathComponent("update.zip")
                try fm.moveItem(at: file, to: zip)
                let unzip = Process()
                unzip.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
                unzip.arguments = ["-x", "-k", zip.path, work.path]
                try unzip.run(); unzip.waitUntilExit()
                let newApp = work.appendingPathComponent("Figmon.app")
                let info = NSDictionary(contentsOf: newApp.appendingPathComponent("Contents/Info.plist"))
                guard info?["CFBundleIdentifier"] as? String == Bundle.main.bundleIdentifier,
                      info?["CFBundleShortVersionString"] as? String == version else {
                    return self.failed(version, "받은 파일이 올바르지 않아요.")
                }
                let dest = URL(fileURLWithPath: Bundle.main.bundlePath)
                _ = try fm.replaceItemAt(dest, withItemAt: newApp)
                DispatchQueue.main.async {
                    // 지금 앱이 끝난 뒤 새 앱을 다시 켬
                    let p = Process()
                    p.executableURL = URL(fileURLWithPath: "/bin/sh")
                    p.arguments = ["-c", "sleep 1.5; /usr/bin/open -a \"$0\" --args --updated", dest.path]
                    try? p.run()
                    NSApp.terminate(nil)
                }
            } catch {
                self.failed(version, "앱을 바꿔 끼우지 못했어요. (\(error.localizedDescription))")
            }
        }.resume()
    }

    /// 자동 업데이트 실패: 버전마다 한 번만 이유를 알려 주고 다운로드 페이지를 열 수 있게
    private func failed(_ version: String, _ why: String) {
        DispatchQueue.main.async {
            let key = "updateFailed-\(version)"
            if UserDefaults.standard.bool(forKey: key) { return }
            UserDefaults.standard.set(true, forKey: key)
            let a = NSAlert()
            a.messageText = "v\(version) 자동 업데이트를 못 했어요"
            a.informativeText = why + "\n\n다운로드 페이지에서 새 버전을 받아 응용 프로그램 폴더에 덮어써 주세요. 키우던 포켓몬은 그대로예요."
            a.addButton(withTitle: "다운로드 페이지 열기")
            a.addButton(withTitle: "나중에")
            NSApp.activate(ignoringOtherApps: true)
            if a.runModal() == .alertFirstButtonReturn {
                NSWorkspace.shared.open(URL(string: "https://github.com/jinjwon/figmon/releases/latest")!)
            }
        }
    }

    private func tell(_ title: String, _ text: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }
}

// MARK: - 앱

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let server = ServerManager()
    let dashboard = Dashboard()
    var pet: PetController?
    var status: NSStatusItem?
    let isBundle = Bundle.main.bundlePath.hasSuffix(".app")

    /// 이미 다른 Figmon이 켜져 있으면 (다른 폴더에 있는 복사본 포함) 그쪽 펫만 불러내고 이 쪽은 끝낸다
    func quitIfAlreadyRunning() -> Bool {
        guard parentPID == nil, let id = Bundle.main.bundleIdentifier else { return false }
        let me = ProcessInfo.processInfo.processIdentifier
        let others = { NSRunningApplication.runningApplications(withBundleIdentifier: id).filter { $0.processIdentifier != me } }
        if justUpdated {   // 업데이트 직후엔 옛 앱이 끝나길 잠깐 기다림
            for _ in 0..<50 where !others().isEmpty { Thread.sleep(forTimeInterval: 0.1) }
        }
        guard let other = others().first else { return false }
        post("/api/summon", [:])
        other.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { NSApp.terminate(nil) }
        return true
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        if quitIfAlreadyRunning() { return }
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
                self.pet = PetController(app: self, startHidden: launchedInBackground)
                if justUpdated {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                        self.pet?.js("say('v\(appVersion)로 업데이트했어요 ✨', 6000)")
                    }
                } else if !launchedInBackground {
                    self.dashboard.show(title: true)   // 직접 켜면 타이틀 화면부터
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                        Mover.offer(reason: "launch")   // 다운로드 폴더에서 켰으면 옮기기부터 (자동 업데이트를 위해)
                        self.askLoginOnce()
                    }
                }
                if LoginItem.enabled { LoginItem.set(true) }   // 앱 위치가 바뀌었어도 자동 실행이 지금 위치를 가리키게
                self.updater.start()
            } else {
                self.failAlert()
            }
        }
    }

    let updater = Updater()

    /// 처음 한 번: 작업 앱을 켜면 펫이 바로 나오도록 로그인 자동 실행을 켤지 묻기
    func askLoginOnce() {
        guard isBundle, !UserDefaults.standard.bool(forKey: "askedLogin"), !LoginItem.enabled else { return }
        UserDefaults.standard.set(true, forKey: "askedLogin")
        let a = NSAlert()
        a.messageText = "작업 앱을 켜면 포켓몬이 바로 나오게 할까요?"
        a.informativeText = "Mac에 로그인할 때 Figmon을 조용히 켜 두고, Figma·VS Code 같은 작업 앱을 켜면 펫이 화면 가장자리에서 걸어 나와요.\n\n나중에 메뉴 막대 › 로그인할 때 자동 실행에서 바꿀 수 있어요."
        a.addButton(withTitle: "켜기")
        a.addButton(withTitle: "나중에")
        if a.runModal() == .alertFirstButtonReturn { LoginItem.set(true) }
    }

    func applicationWillTerminate(_ n: Notification) { server.stop() }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows: Bool) -> Bool {
        pet?.summon()       // 앱을 다시 켜면 펫도 불러냄
        dashboard.show()
        return true
    }

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
        m.addItem(sizeSliderItem())
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
            let login = add(m, "로그인할 때 자동 실행 (작업 앱을 켜면 펫 등장)", #selector(toggleLogin), "")
            login.state = LoginItem.enabled ? .on : .off
            add(m, "업데이트 확인…", #selector(checkUpdate), "")
        }
        m.addItem(.separator())
        let ver = NSMenuItem(title: "Figmon v\(appVersion)", action: nil, keyEquivalent: "")
        ver.isEnabled = false
        m.addItem(ver)
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
    /// 메뉴 안의 펫 크기 슬라이더 (끄는 동안 실시간으로 바뀜)
    var sizeLabel: NSTextField?
    func sizeSliderItem() -> NSMenuItem {
        let item = NSMenuItem()
        let v = NSView(frame: NSRect(x: 0, y: 0, width: 240, height: 46))
        let cur = pet?.petSize ?? 1
        let label = NSTextField(labelWithString: "🐾 펫 크기  \(Int((cur * 100).rounded()))%")
        label.frame = NSRect(x: 18, y: 25, width: 210, height: 17)
        label.font = .menuFont(ofSize: 13)
        let slider = NSSlider(value: cur, minValue: 0.6, maxValue: 1.6, target: self, action: #selector(sizeSlid(_:)))
        slider.frame = NSRect(x: 16, y: 4, width: 210, height: 20)
        slider.isContinuous = true
        slider.setAccessibilityLabel("펫 크기")
        v.addSubview(label); v.addSubview(slider)
        sizeLabel = label
        item.view = v
        return item
    }
    @objc func sizeSlid(_ s: NSSlider) {
        let v = (s.doubleValue * 20).rounded() / 20   // 5% 단위
        sizeLabel?.stringValue = "🐾 펫 크기  \(Int((v * 100).rounded()))%"
        pet?.applyLiveSize(v, save: true)
    }
    @objc func pauseFor(_ s: NSMenuItem) { post("/api/pause", ["minutes": s.tag]) }
    @objc func quit() { NSApp.terminate(nil) }

    @objc func toggleLogin() { LoginItem.set(!LoginItem.enabled) }
    @objc func checkUpdate() { updater.check(manual: true) }

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
