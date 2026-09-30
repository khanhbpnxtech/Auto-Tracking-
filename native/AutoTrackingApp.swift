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
        setupWindow()
        ensureServerRunning { [weak self] ok in
            guard let self else { return }
            if ok {
                self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/")!))
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

    func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1360, height: 900)
        window = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Auto Tracking Test"
        window.minSize = NSSize(width: 900, height: 560)
        window.center()

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

    // ---- server lifecycle: same logic the old bash launcher had, ported to Process ----

    func ensureServerRunning(completion: @escaping (Bool) -> Void) {
        if isPortOpen(PORT) {
            completion(true)  // đã có server chạy sẵn (app mở lần 2, hoặc dev chạy tay) — dùng luôn
            return
        }

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
