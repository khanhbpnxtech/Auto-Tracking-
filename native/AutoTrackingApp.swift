// Auto Tracking Test — cửa sổ app thật (WKWebView), không phải mở trình duyệt.
//
// Tự khởi động server.py (nếu chưa chạy) giống hệt bash launcher trước đây, rồi hiện dashboard
// trong 1 cửa sổ Cocoa có WKWebView — không thanh địa chỉ, không tab, không URL bar. Quit app
// (Cmd+Q / đóng cửa sổ) thì tắt luôn server nếu chính app này đã khởi động nó.
//
// Build: xem native/build.sh — biên dịch bằng swiftc, không cần Xcode project.

import Cocoa
import WebKit

let PORT: UInt16 = 8765
let LOG_PATH = "/tmp/auto-tracking-test.log"

func isPortOpen(_ port: UInt16) -> Bool {
    let sock = socket(AF_INET, SOCK_STREAM, 0)
    if sock < 0 { return false }
    defer { close(sock) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = port.bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let result = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return result == 0
}

class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var serverTask: Process?  // set only if THIS launch started the server (so we know to stop it)

    // .app bundle's own folder IS Contents/MacOS/../../.. — Bundle.main gives that directly,
    // no manual path-climbing needed (unlike the old bash launcher's dirname chain).
    lazy var appDir: URL = Bundle.main.bundleURL.deletingLastPathComponent()
    lazy var vendorBin: URL = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/vendor/bin")
    lazy var vendorPython: URL = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/vendor/python")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        setupMainMenu()
        setupWindow()
        ensureServerRunning { [weak self] ok in
            guard let self else { return }
            if ok {
                // Our own server already sends Cache-Control: no-store on every response, but that
                // only stops the HTTP cache — macOS can separately restore the WKWebView's actual
                // rendered session state across a relaunch (window/app state restoration), which
                // would show old content without ever making a new network request at all.
                // reloadIgnoringLocalAndRemoteCacheData forces a genuine fresh load every launch.
                var request = URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/")!)
                request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
                self.webView.load(request)
            } else {
                self.showFatal("Server không khởi động được sau nhiều giây chờ.\n\nXem log: \(LOG_PATH)")
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        // Chỉ tắt server nếu CHÍNH lần mở app này là bên khởi động nó — server ai khác đang
        // chạy sẵn (vd. dev chạy tay qua Terminal) thì để yên, không đụng vào.
        serverTask?.terminate()
    }

    // A bare AppKit app built with `swiftc` (no Storyboard/XIB) starts with NO menu bar at all —
    // Cmd+C/V/X/A are standard EDIT MENU key equivalents in AppKit, dispatched by the menu item
    // that owns them, not something a focused view just receives on its own. With no Edit menu,
    // those shortcuts do nothing at the OS level before the keystroke ever reaches WKWebView, no
    // matter what's focused inside the page — confirmed this was the actual reason Cmd+V "didn't
    // work" in Màn hình máy (not a DOM/focus issue on the web side, which was the wrong fix first
    // tried). The action is left nil-targeted so AppKit forwards it up the normal responder chain;
    // WKWebView implements cut:/copy:/paste:/selectAll: itself for whatever's focused inside it.
    func setupMainMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        appMenu.addItem(withTitle: "Quit Auto Tracking Test", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        // Paste alone gets a direct target instead of the nil-targeted responder-chain forward the
        // others use — confirmed that path still wasn't reaching the page even with the Edit menu
        // in place (adding the menu was necessary but turned out not to be sufficient): WKWebView's
        // own view hierarchy is who'd actually need to answer `paste:`, and whether that happens
        // depends on its internal focus/editor-state plumbing recognizing our hidden input as a
        // genuine editable target, which evidently it does not reliably do here. Going native
        // instead removes that uncertainty completely: this reads the pasteboard directly and
        // hands the text to the page over evaluateJavaScript, regardless of DOM focus state.
        editMenu.addItem(withTitle: "Paste", action: #selector(AppDelegate.handlePasteMenu(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        NSApp.mainMenu = mainMenu
    }

    @objc func handlePasteMenu(_ sender: Any?) {
        guard let text = NSPasteboard.general.string(forType: .string),
              let data = try? JSONEncoder().encode(text),
              let jsonText = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.__mirrorPasteFromNative && window.__mirrorPasteFromNative(\(jsonText))")
    }

    func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1360, height: 900)
        window = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Auto Tracking Test"
        window.minSize = NSSize(width: 900, height: 560)
        window.center()
        window.isRestorable = false  // always a genuine fresh load — never macOS's saved-state restore

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")  // right-click → Inspect Element, hữu ích lúc debug
        // Cho phép WebCodecs (VideoDecoder) dùng cho tính năng Màn hình máy — mặc định bật sẵn
        // trên WebKit bản mới, khai rõ ra để không bị tắt bởi cấu hình khác.
        config.preferences.setValue(true, forKey: "mediaDevicesEnabled")

        webView = WKWebView(frame: rect, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showLoading()
    }

    func showLoading() {
        let html = """
        <html><body style="background:#0f172a;color:#94a3b8;font:14px -apple-system;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        Đang khởi động server...</body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func showFatal(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Auto Tracking Test"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.runModal()
        NSApp.terminate(nil)
    }

    // ---- WKUIDelegate: bridge JS alert()/confirm()/prompt() to real dialogs ----
    //
    // WKWebView does NOTHING for these by default unless the delegate implements them — a page's
    // alert()/confirm() call just silently no-ops (confirm() returns as if the user hit Cancel,
    // no dialog ever appears). That's exactly what broke the dashboard's Update button: it calls
    // confirm("Có bản mới... cập nhật ngay?") before pulling, which silently came back "false"
    // here with no visible dialog at all, so every click looked like nothing happened.

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "Auto Tracking Test"
        alert.informativeText = message
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "Auto Tracking Test"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = "Auto Tracking Test"
        alert.informativeText = prompt
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        input.stringValue = defaultText ?? ""
        alert.accessoryView = input
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? input.stringValue : nil)
    }

    // A link with target="_blank" (the "Tracking Auto (Lark)" link on the Auto Event Tracking
    // tab, same pattern as window.open()) asks WKWebView to create a whole second web view to
    // load it into — same class of gap as alert()/confirm() above: WKWebView does nothing with
    // that request unless a delegate handles it, so the click silently no-ops with no new window,
    // no error, nothing. This app has no second window to give it anyway (and doesn't want one —
    // that Lark page belongs in the user's regular browser, with their real Lark login), so hand
    // the URL to the system default browser instead and tell WebKit no new web view was created.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    // ---- server lifecycle: same logic the old bash launcher had, ported to Process ----

    func ensureServerRunning(completion: @escaping (Bool) -> Void) {
        guard isPortOpen(PORT) else { startServer(completion: completion); return }
        // Something already holds the port. Only reuse it if it's this folder's own server running
        // the code currently on disk — blindly reusing it is how a colleague's forgotten second copy
        // of the tool (older, in ~/Documents) kept serving the dashboard whichever app they opened.
        askWhoami { [weak self] ours in
            guard let self else { return }
            if ours { completion(true); return }
            guard let pid = self.pidListeningOnPort(), self.isToolServer(pid: pid) else {
                self.showFatal("Cổng \(PORT) đang bị 1 chương trình khác (không phải Auto Tracking Test) "
                             + "chiếm. Tắt chương trình đó rồi mở lại app.")
                completion(false)
                return
            }
            self.stopServer(pid: pid) { self.startServer(completion: completion) }
        }
    }

    /// true = the server on the port runs from this app's folder and isn't behind the code on disk.
    /// Anything else (other folder, stale code, an old server with no /api/whoami) is false.
    func askWhoami(completion: @escaping (Bool) -> Void) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/api/whoami")!)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 3
        let mine = appDir.resolvingSymlinksInPath().path.lowercased()
        URLSession.shared.dataTask(with: request) { data, _, _ in
            var ours = false
            if let data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let baseDir = json["base_dir"] as? String {
                let theirs = URL(fileURLWithPath: baseDir).resolvingSymlinksInPath().path.lowercased()
                ours = theirs == mine && (json["stale"] as? Bool) == false
            }
            DispatchQueue.main.async { completion(ours) }
        }.resume()
    }

    func pidListeningOnPort() -> pid_t? {
        let out = runCapture("/usr/sbin/lsof", ["-tiTCP:\(PORT)", "-sTCP:LISTEN"])
        return out.split(separator: "\n").first.flatMap { pid_t($0.trimmingCharacters(in: .whitespaces)) }
    }

    /// Never kill something that isn't this tool's own server.py, whatever else might be on the port.
    func isToolServer(pid: pid_t) -> Bool {
        runCapture("/bin/ps", ["-p", "\(pid)", "-o", "command="]).contains("server.py")
    }

    /// SIGTERM first (server.py turns it into a clean shutdown that also stops its adb children),
    /// SIGKILL only if the port is still held ~5s later.
    func stopServer(pid: pid_t, then: @escaping () -> Void) {
        kill(pid, SIGTERM)
        func wait(_ attemptsLeft: Int) {
            if !isPortOpen(PORT) { then(); return }
            if attemptsLeft == 0 { kill(pid, SIGKILL) }
            if attemptsLeft < -5 { then(); return }  // give up waiting; startServer reports if bind fails
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { wait(attemptsLeft - 1) }
        }
        wait(25)
    }

    func runCapture(_ path: String, _ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }

    func startServer(completion: @escaping (Bool) -> Void) {
        let serverPy = appDir.appendingPathComponent("server.py")
        guard FileManager.default.fileExists(atPath: serverPy.path) else {
            showFatal("Không tìm thấy server.py cạnh app này (đang tìm ở: \(appDir.path)).\n\n"
                     + "App này phải nằm ngay trong thư mục tool đã git clone — đừng copy app ra ngoài một mình.")
            completion(false)
            return
        }

        let python3 = findPython3()
        guard let python3 else {
            showFatal("Không tìm thấy Python 3 trên máy này — bình thường mọi Mac đều có sẵn.\n\n"
                     + "Thử mở Terminal, gõ 'xcode-select --install' rồi mở lại app.")
            completion(false)
            return
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: python3)
        task.arguments = ["server.py"]
        task.currentDirectoryURL = appDir

        var env = ProcessInfo.processInfo.environment
        let existingPath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = "\(vendorBin.path):/opt/homebrew/bin:/usr/local/bin:\(existingPath)"
        let existingPyPath = env["PYTHONPATH"]
        env["PYTHONPATH"] = vendorPython.path + (existingPyPath.map { ":" + $0 } ?? "")
        task.environment = env

        FileManager.default.createFile(atPath: LOG_PATH, contents: nil)
        let logHandle = FileHandle(forWritingAtPath: LOG_PATH)
        task.standardOutput = logHandle
        task.standardError = logHandle

        do {
            try task.run()
            serverTask = task
        } catch {
            showFatal("Không chạy được server.py: \(error.localizedDescription)")
            completion(false)
            return
        }

        // Đợi server thật sự bind port, tối đa ~10s (poll mỗi 300ms, không chặn main thread).
        pollForServer(attemptsLeft: 33, completion: completion)
    }

    func pollForServer(attemptsLeft: Int, completion: @escaping (Bool) -> Void) {
        if isPortOpen(PORT) { completion(true); return }
        if attemptsLeft <= 0 { completion(false); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.pollForServer(attemptsLeft: attemptsLeft - 1, completion: completion)
        }
    }

    func findPython3() -> String? {
        for candidate in ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"] {
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return nil
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
