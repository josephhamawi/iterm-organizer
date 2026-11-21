// iTerm Organizer - menu bar item.
//
// A restore offer used to exist only as a modal alert on iTerm2. If that alert
// opened behind other windows, or iTerm2 went away while it was up, the offer
// was gone. This process keeps the offer reachable: it watches the agent's
// state file and puts the same choice one click away, for as long as it stands.
//
// It owns no logic of its own. Everything it does is a call into the Python
// command line interface in the same bundle.

import AppKit
import Foundation

let storeRoot = ("~/.iterm-organizer" as NSString).expandingTildeInPath

struct Offer {
    let windows: Int
    let panes: Int
    let createdAt: Date
}

struct State {
    var offer: Offer?
    var snapshotAt: Date?
    var agentRunning: Bool
}

func readState() -> State {
    var state = State(offer: nil, snapshotAt: nil, agentRunning: false)

    let statePath = (storeRoot as NSString).appendingPathComponent("state.json")
    if let data = FileManager.default.contents(atPath: statePath),
       let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let pending = root["pending_offer"] as? [String: Any] {
        state.offer = Offer(
            windows: (pending["windows"] as? Int) ?? 0,
            panes: (pending["panes"] as? Int) ?? 0,
            createdAt: Date(timeIntervalSince1970: (pending["created_at"] as? Double) ?? 0)
        )
    }

    let snapshotPath = (storeRoot as NSString).appendingPathComponent("snapshot.json")
    if let attrs = try? FileManager.default.attributesOfItem(atPath: snapshotPath) {
        state.snapshotAt = attrs[.modificationDate] as? Date
    }

    // The daemon writes its pid here and removes it on a clean exit.
    let lockPath = (storeRoot as NSString).appendingPathComponent("daemon.lock")
    if let text = try? String(contentsOfFile: lockPath, encoding: .utf8),
       let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)) {
        state.agentRunning = kill(pid, 0) == 0
    }
    return state
}

func describe(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "EEE HH:mm"
    return formatter.string(from: date)
}

final class Controller: NSObject, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var state = readState()
    var busy = false

    // The Python entry point lives next to this binary in the bundle.
    var cli: String {
        Bundle.main.bundleURL
            .appendingPathComponent("Contents/MacOS/iTermOrganizer").path
    }

    func start() {
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        refresh()
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in self.refresh() }
    }

    func refresh() {
        state = readState()
        guard let button = item.button else { return }
        let pending = state.offer != nil
        let name = pending ? "arrow.clockwise.circle.fill" : "rectangle.split.3x1"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "iTerm Organizer")
        image?.isTemplate = true
        button.image = image
        button.toolTip = pending
            ? "iTerm Organizer - a restore is waiting"
            : "iTerm Organizer"
    }

    // Rebuilt on every open so it always shows current state.
    func menuNeedsUpdate(_ menu: NSMenu) {
        state = readState()
        menu.removeAllItems()

        if let offer = state.offer {
            let title = "Restore \(offer.windows) window(s) from \(describe(offer.createdAt))…"
            add(menu, title, #selector(restore))
            add(menu, "Dismiss this offer", #selector(dismissOffer))
            menu.addItem(.separator())
        }

        let status: String
        if let at = state.snapshotAt {
            status = "Last snapshot \(describe(at))"
        } else {
            status = "No snapshot yet"
        }
        add(menu, status, nil)
        add(menu, state.agentRunning ? "Agent running" : "Agent not running", nil)
        menu.addItem(.separator())

        if state.offer == nil {
            add(menu, "Restore last session…", #selector(restore))
        }
        add(menu, "Snapshot now", #selector(snapshotNow))
        add(menu, "Show details…", #selector(showStatus))
        add(menu, "Open log", #selector(openLog))
        menu.addItem(.separator())
        add(menu, "Quit", #selector(quit))
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, _ action: Selector?) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = action == nil ? nil : self
        entry.isEnabled = action != nil && !busy
        menu.addItem(entry)
        return entry
    }

    // --- actions ----------------------------------------------------------

    @objc func restore() { run(["--restore"], report: "Restore") }

    @objc func snapshotNow() { run(["--snapshot-now"], report: "Snapshot") }

    @objc func showStatus() { run(["--status"], report: "iTerm Organizer", always: true) }

    @objc func dismissOffer() {
        let path = (storeRoot as NSString).appendingPathComponent("state.json")
        guard let data = FileManager.default.contents(atPath: path),
              var root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        root.removeValue(forKey: "pending_offer")
        if let out = try? JSONSerialization.data(withJSONObject: root, options: .prettyPrinted) {
            try? out.write(to: URL(fileURLWithPath: path))
        }
        refresh()
    }

    @objc func openLog() {
        let log = (storeRoot as NSString).appendingPathComponent("organizer.log")
        NSWorkspace.shared.open(URL(fileURLWithPath: log))
    }

    @objc func quit() { NSApp.terminate(nil) }

    /// Run the CLI off the main thread; report failures, and successes only
    /// when the output is the point (--status).
    func run(_ args: [String], report: String, always: Bool = false) {
        guard !busy else { return }
        busy = true
        DispatchQueue.global(qos: .userInitiated).async {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: self.cli)
            task.arguments = args
            let pipe = Pipe()
            task.standardOutput = pipe
            task.standardError = pipe
            var output = ""
            var failed = false
            do {
                try task.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                output = String(data: data, encoding: .utf8) ?? ""
                failed = task.terminationStatus != 0
            } catch {
                output = error.localizedDescription
                failed = true
            }
            DispatchQueue.main.async {
                self.busy = false
                self.refresh()
                if failed || always {
                    let alert = NSAlert()
                    alert.messageText = report
                    alert.informativeText = output.isEmpty ? "No output." : output
                    alert.alertStyle = failed ? .warning : .informational
                    NSApp.activate(ignoringOtherApps: true)
                    alert.runModal()
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = Controller()
controller.start()
app.run()
