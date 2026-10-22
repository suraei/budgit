// Brote as a floating macOS window (R-151). One file, two layers:
//   CORE  = numbers, structs and JavaScriptCore glue. Foundation + JavaScriptCore only, so it builds and self-tests with no window server:
//           swiftc -D CORE_ONLY ... && ./core --selftest --assets <this folder>
//   SHELL = the AppKit part (one panel, timers, mouse), inside `#if !CORE_ONLY`. Compile-checked, never run by the tests.
// His pixels and his words are the plugin's own code, copied byte for byte (sprites.js, say.js) and run in JavaScriptCore.
// Every number below is a design CHOICE, or is measured later by the probes (see README.md).
import Foundation
import JavaScriptCore
#if !CORE_ONLY
import AppKit
import QuartzCore
#endif

// MARK: - constants

enum K {
    static let scale = 3.0                                  // 1 art px = 3 pt
    static let spriteW = 102.0, spriteH = 84.0              // 34 x 28 art px
    static let margin = 8.0                                 // transparent room around him for the bounce
    static let winW = 118.0, winH = 100.0
    static let gravity = 2400.0, friction = 1800.0, restitution = 0.5   // purrsistent's numbers
    static let maxThrow = 2500.0, sampleWindow = 0.1, dragThreshold = 3.0
    static let throwMin = 200.0, dizzyAt = 1500.0           // plan choices, tuned by hand test
    static let pokeWindow = 2.5, pokesForGrumpy = 4
    static let strollRange = 60.0, strollChance = 0.015
    static let perchMargin = 40.0
    static let statusTimeout = 5.0, statusEvery = 30.0
    static let hitMargin = 1                                // art px
    static let logCap = 200_000
    static let bundleID = "es.budgit.brote.float"
    static let claudeBundle = "com.anthropic.claudefordesktop"
    static let budgitRel = "plugin/bin/budgit"
    static let binRel = "plugin/bin"
    static let scriptsRel = "plugin/scripts"
    static let pluginDir = "plugin"
}

// MARK: - geometry (own structs: the core does not import a window framework)

struct Pt: Equatable {
    var x: Double, y: Double
    init(_ x: Double, _ y: Double) { self.x = x; self.y = y }
    static func + (a: Pt, b: Pt) -> Pt { Pt(a.x + b.x, a.y + b.y) }
    static func - (a: Pt, b: Pt) -> Pt { Pt(a.x - b.x, a.y - b.y) }
    func dist(_ o: Pt) -> Double { hypot(x - o.x, y - o.y) }
    var speed: Double { hypot(x, y) }
}

struct Rc: Equatable {
    var x: Double, y: Double, w: Double, h: Double
    init(_ x: Double, _ y: Double, _ w: Double, _ h: Double) { self.x = x; self.y = y; self.w = w; self.h = h }
    var minX: Double { x }
    var maxX: Double { x + w }
    var minY: Double { y }
    var maxY: Double { y + h }
    var center: Pt { Pt(x + w / 2, y + h / 2) }
    var area: Double { w * h }
    func contains(_ p: Pt) -> Bool { p.x >= minX && p.x < maxX && p.y >= minY && p.y < maxY }
    func inflated(_ d: Double) -> Rc { Rc(x - d, y - d, w + 2 * d, h + 2 * d) }
}

func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double { max(lo, min(hi, v)) }

// MARK: - colours, frames, the hit mask

struct RGBA: Equatable { var r: UInt8, g: UInt8, b: UInt8, a: UInt8 }

/// The sprite section writes colours as `#rrggbb` and `rgba(r,g,b,a)` (the ground shadow). Anything else is nil.
func parseColor(_ s: String) -> RGBA? {
    if s.hasPrefix("#"), s.count == 7, let v = UInt32(s.dropFirst(), radix: 16) {
        return RGBA(r: UInt8((v >> 16) & 255), g: UInt8((v >> 8) & 255), b: UInt8(v & 255), a: 255)
    }
    let isRGBA = s.hasPrefix("rgba("), isRGB = s.hasPrefix("rgb(")
    guard (isRGBA || isRGB), s.hasSuffix(")") else { return nil }
    let inner = s.dropFirst(isRGBA ? 5 : 4).dropLast()
    let parts = inner.split(separator: ",").map { Double($0.trimmingCharacters(in: .whitespaces)) }
    guard parts.count == (isRGBA ? 4 : 3), !parts.contains(where: { $0 == nil }) else { return nil }
    let n = parts.map { $0! }
    let byte = { (d: Double) -> UInt8 in UInt8(clamp(d.rounded(), 0, 255)) }
    return RGBA(r: byte(n[0]), g: byte(n[1]), b: byte(n[2]), a: isRGBA ? byte(n[3] * 255) : 255)
}

struct Grid: Equatable {
    var w: Int, h: Int
    var cells: [RGBA?]                                      // row-major, row 0 on top
    subscript(x: Int, y: Int) -> RGBA? { cells[y * w + x] }
    func mirrored() -> Grid {
        var out = cells
        for y in 0..<h { for x in 0..<w { out[y * w + x] = cells[y * w + (w - 1 - x)] } }
        return Grid(w: w, h: h, cells: out)
    }
    /// From the JSON the JS shim returns: an array of rows, each cell a colour string or null.
    init?(json: String) {
        guard let d = json.data(using: .utf8), let rows = (try? JSONSerialization.jsonObject(with: d)) as? [[Any]], let first = rows.first else { return nil }
        w = first.count; h = rows.count; cells = []
        for r in rows {
            guard r.count == w else { return nil }
            for c in r { cells.append((c as? String).flatMap(parseColor)) }
        }
    }
    init(w: Int, h: Int, cells: [RGBA?]) { self.w = w; self.h = h; self.cells = cells }
}

struct HitMask {
    let w: Int, h: Int
    let bits: [Bool]
    /// Opaque cells (alpha >= 0.5) grown by `margin` art px. The shadow (alpha .14 / .06) never counts on its own.
    init(grid: Grid, margin: Int = K.hitMargin) {
        w = grid.w; h = grid.h
        var b = [Bool](repeating: false, count: w * h)
        for y in 0..<h { for x in 0..<w {
            guard let c = grid[x, y], c.a >= 128 else { continue }
            for yy in max(0, y - margin)...min(h - 1, y + margin) { for xx in max(0, x - margin)...min(w - 1, x + margin) { b[yy * w + xx] = true } }
        } }
        bits = b
    }
    /// `x` from the sprite's left edge and `yTop` from its top edge, in points; `flipped` when he faces left.
    func hit(x: Double, yTop: Double, flipped: Bool) -> Bool {
        guard x >= 0, yTop >= 0 else { return false }
        var ax = Int(x / K.scale); let ay = Int(yTop / K.scale)
        guard ax < w, ay < h else { return false }
        if flipped { ax = w - 1 - ax }
        return bits[ay * w + ax]
    }
}

/// Click-through: the window takes mouse events only over his body, or while a press of ours is in progress.
func ignoreMouse(overBody: Bool, pressed: Bool, dragging: Bool) -> Bool { !(overBody || pressed || dragging) }

// MARK: - physics

struct Bounds { var minX: Double, maxX: Double, floorY: Double, ceilY: Double }   // for the sprite's bottom-left corner
struct Body { var p: Pt; var v: Pt }
enum Flight { case flying, landed }
struct Sample { var t: Double; var p: Pt }

func stepFly(_ b: inout Body, dt: Double, in bd: Bounds) -> Flight {
    let onFloor = b.p.y <= bd.floorY + 0.001
    if onFloor && b.v.y <= 0 && b.v.y > -60 {
        b.v.y = 0
        let drop = K.friction * dt
        if abs(b.v.x) <= drop { b.v.x = 0 } else { b.v.x -= b.v.x > 0 ? drop : -drop }
    } else {
        b.v.y -= K.gravity * dt
    }
    b.p.x += b.v.x * dt; b.p.y += b.v.y * dt
    if b.p.x < bd.minX { b.p.x = bd.minX; b.v.x = abs(b.v.x) * K.restitution }
    if b.p.x > bd.maxX { b.p.x = bd.maxX; b.v.x = -abs(b.v.x) * K.restitution }
    if b.p.y > bd.ceilY { b.p.y = bd.ceilY; b.v.y = -abs(b.v.y) * K.restitution }
    if b.p.y <= bd.floorY {
        b.p.y = bd.floorY
        b.v.y = b.v.y < -60 ? -b.v.y * K.restitution : 0
    }
    return (b.p.y == bd.floorY && b.v.y == 0 && b.v.x == 0) ? .landed : .flying
}

/// Release velocity from the pointer samples of the last 0.1 s, clamped per axis. Fewer than two samples: no throw.
func throwVelocity(_ samples: [Sample], now: Double) -> Pt {
    let s = samples.filter { now - $0.t <= K.sampleWindow }
    guard let a = s.first, let z = s.last, z.t - a.t > 0.005 else { return Pt(0, 0) }
    let dt = z.t - a.t
    return Pt(clamp((z.p.x - a.p.x) / dt, -K.maxThrow, K.maxThrow), clamp((z.p.y - a.p.y) / dt, -K.maxThrow, K.maxThrow))
}

func isDrag(from a: Pt, to b: Pt) -> Bool { a.dist(b) >= K.dragThreshold }
func isThrow(speed: Double) -> Bool { speed >= K.throwMin }
func dizzyAfter(speed: Double) -> Bool { speed >= K.dizzyAt }

// MARK: - behaviour: pokes and strolling

enum Click { case poke, countedOnly, grumpy }

struct PokeCounter {
    var times: [Double] = []
    /// Own timestamps: `NSEvent.clickCount` is the double-click counter, not this. Four within 2.5 s = grumpy and reset.
    mutating func click(now: Double, dormido: Bool) -> Click {
        times = times.filter { now - $0 < K.pokeWindow }
        times.append(now)
        if times.count >= K.pokesForGrumpy { times = []; return .grumpy }
        return dormido ? .countedOnly : .poke
    }
}

struct Stroll {
    var target: Double? = nil
    var walking = false
    var facingLeft = false
    static func lively(_ state: String) -> Bool { state == "radiante" || state == "contento" }
    /// One 10 fps tick. `home` = the x he keeps near; `lo...hi` = where he may stand. Moves `x` by 4.5 pt (radiante) or 3 pt (contento).
    mutating func tick(state: String, busy: Bool, x: inout Double, home: Double, lo: Double, hi: Double, rng: () -> Double) {
        guard Stroll.lively(state), !busy else { walking = false; if busy { target = nil }; return }
        guard let t = target else {
            if rng() < K.strollChance { target = clamp(home + (rng() * 2 - 1) * K.strollRange, lo, hi) }
            walking = false
            return
        }
        let step = state == "radiante" ? 4.5 : 3.0, d = t - x
        if abs(d) <= step { x = t; target = nil; walking = false; facingLeft = false }
        else { x += d > 0 ? step : -step; walking = true; facingLeft = d < 0 }
    }
}

// MARK: - where he lives: Claude's window, the perch, the saved offset

protocol Store: AnyObject {
    func number(_ k: String) -> Double?
    func setNumber(_ k: String, _ v: Double?)
    func string(_ k: String) -> String?
    func setString(_ k: String, _ v: String?)
}

final class MemStore: Store {
    var d: [String: Any] = [:]
    var writes = 0
    func number(_ k: String) -> Double? { d[k] as? Double }
    func setNumber(_ k: String, _ v: Double?) { writes += 1; d[k] = v }
    func string(_ k: String) -> String? { d[k] as? String }
    func setString(_ k: String, _ v: String?) { writes += 1; d[k] = v }
}

final class DefaultsStore: Store {
    private let u = UserDefaults.standard                   // the app's own domain, es.budgit.brote.float
    func number(_ k: String) -> Double? { u.object(forKey: k) as? Double }
    func setNumber(_ k: String, _ v: Double?) { if let v = v { u.set(v, forKey: k) } else { u.removeObject(forKey: k) } }
    func string(_ k: String) -> String? { u.string(forKey: k) }
    func setString(_ k: String, _ v: String?) { if let v = v { u.set(v, forKey: k) } else { u.removeObject(forKey: k) } }
}

struct WinInfo { var pid: Int; var layer: Int; var bounds: Rc }       // bounds as the window list gives them: origin top-left

func flipY(_ r: Rc, primaryHeight: Double) -> Rc { Rc(r.x, primaryHeight - (r.y + r.h), r.w, r.h) }

/// Claude's chat window: the largest layer-0 window of its processes. Needs no permission (bounds, pid and layer are not protected).
func pickClaude(_ list: [WinInfo], pids: Set<Int>, primaryHeight: Double) -> Rc? {
    let ws = list.filter { pids.contains($0.pid) && $0.layer == 0 && $0.bounds.w >= 100 && $0.bounds.h >= 100 }
    guard let best = ws.max(by: { $0.bounds.area < $1.bounds.area }) else { return nil }
    return flipY(best.bounds, primaryHeight: primaryHeight)
}

func screenIndex(for p: Pt, in screens: [Rc]) -> Int {
    if let i = screens.firstIndex(where: { $0.contains(p) }) { return i }
    func gap(_ r: Rc) -> Double { hypot(max(r.minX - p.x, 0, p.x - r.maxX), max(r.minY - p.y, 0, p.y - r.maxY)) }
    return screens.indices.min(by: { gap(screens[$0]) < gap(screens[$1]) }) ?? 0
}

struct Anchor: Equatable {
    var relX: Double, perchY: Double                        // from Claude's bottom-left corner
    func apply(to c: Rc) -> Pt { Pt(c.x + relX, c.y + perchY) }
    static func span(_ c: Rc) -> Double { max(c.w, K.spriteW) }
}

func spriteCentre(_ p: Pt) -> Pt { Pt(p.x + K.spriteW / 2, p.y + K.spriteH / 2) }

final class Layout {
    /// Where he is. `intended` is where he would be with no screen edge in the way: it keeps following Claude while `pos` is clamped, so he comes back with it.
    var pos: Pt { didSet { if !following { intended = pos } } }
    private(set) var intended: Pt
    private var following = false
    var anchor: Anchor?
    var claude: Rc?
    var screens: [Rc]                                       // visible frames, the main screen first
    let store: Store

    init(store: Store, screens: [Rc], claude: Rc?) {
        self.store = store; self.screens = screens; self.claude = claude
        let ox = store.number("offsetX"), py = store.number("perchY")
        anchor = (ox != nil && py != nil) ? Anchor(relX: ox!, perchY: py!) : nil
        let main = screens.first ?? Rc(0, 0, 1440, 900)
        pos = Pt(main.maxX - K.spriteW - 16, main.minY)
        intended = pos
        if let a = anchor, let c = claude { pos = a.apply(to: c) }
        else if let lx = store.number("lastX"), let ly = store.number("lastY") { pos = Pt(lx, ly) }
        pos = clamped(pos)
        intended = pos
    }

    func visible(for p: Pt) -> Rc { screens.isEmpty ? Rc(0, 0, 1440, 900) : screens[screenIndex(for: spriteCentre(p), in: screens)] }

    /// Always inside the visible frame of its screen; this wins over "moves with Claude".
    func clamped(_ p: Pt) -> Pt {
        let v = visible(for: p)
        return Pt(clamp(p.x, v.minX, max(v.minX, v.maxX - K.spriteW)), clamp(p.y, v.minY, max(v.minY, v.maxY - K.spriteH)))
    }

    private func nearClaude(_ p: Pt) -> Bool { claude?.inflated(K.perchMargin).contains(spriteCentre(p)) ?? false }

    /// A gentle release (below THROW_MIN): he stays. Inside Claude's frame it saves the offset; outside it saves the absolute spot.
    func gentleDrop(to p: Pt) {
        pos = clamped(p)
        if let c = claude {
            if nearClaude(pos) {
                let a = Anchor(relX: clamp(pos.x - c.x, 0, max(0, Anchor.span(c) - K.spriteW)), perchY: pos.y - c.y)
                anchor = a; pos = clamped(a.apply(to: c))
                store.setNumber("offsetX", a.relX); store.setNumber("perchY", a.perchY)
            } else {
                anchor = nil
                store.setNumber("offsetX", nil); store.setNumber("perchY", nil)
                store.setNumber("lastX", pos.x); store.setNumber("lastY", pos.y)
            }
        } else {
            store.setNumber("lastX", pos.x); store.setNumber("lastY", pos.y)    // nobody to anchor to: the saved offset stays
        }
    }

    /// Where a flight may go. Anchored: the perch line and Claude's span. Free: the screen.
    func flightBounds() -> Bounds {
        let v = visible(for: pos)
        if let a = anchor, let c = claude {
            let lo = max(c.x, v.minX), hi = max(lo, min(c.x + Anchor.span(c) - K.spriteW, v.maxX - K.spriteW))
            return Bounds(minX: lo, maxX: hi, floorY: clamp(c.y + a.perchY, v.minY, v.maxY - K.spriteH), ceilY: v.maxY - K.spriteH)
        }
        return Bounds(minX: v.minX, maxX: max(v.minX, v.maxX - K.spriteW), floorY: v.minY, ceilY: v.maxY - K.spriteH)
    }

    /// A throw leaves the saved values alone. Released outside Claude's span it leaves anchored mode (memory only).
    func beginThrow(from p: Pt) -> Bounds {
        pos = clamped(p)
        if anchor != nil && !nearClaude(pos) { anchor = nil }
        let b = flightBounds()
        pos.y = max(pos.y, b.floorY)
        return b
    }

    /// Claude's window moved or resized (or appeared / vanished). Anchored: he moves by the same delta, kept on screen.
    func claudeChanged(to new: Rc?) {
        let old = claude
        claude = new
        guard let a = anchor, let n = new else { return }
        following = true; defer { following = false }
        if let o = old { intended = Pt(intended.x + (n.x - o.x), intended.y + (n.y - o.y)) } else { intended = a.apply(to: n) }
        pos = clamped(intended)
    }

    /// Where a stroll may go and the x it keeps near.
    func strollRange() -> (home: Double, lo: Double, hi: Double) {
        let b = flightBounds()
        if let a = anchor, let c = claude { return (clamp(c.x + a.relX, b.minX, b.maxX), b.minX, b.maxX) }
        return (pos.x, b.minX, b.maxX)
    }
}

/// The hub is named on the command line (`--hub`) and only used when plugin/bin/budgit is a regular file owned by the current user
/// that no other user can write: the app runs that script every 30 s, so it must not be one somebody else could swap.
func hubTrusted(_ hub: String, fm: FileManager = .default) -> Bool {
    // mine and not writable by others: for a file, a directory, and (since `budgit` imports them) every .py in plugin/scripts
    func sound(_ path: String, _ kind: FileAttributeType) -> Bool {
        guard let at = try? fm.attributesOfItem(atPath: path), (at[.type] as? FileAttributeType) == kind,
              (at[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), let perm = (at[.posixPermissions] as? NSNumber)?.intValue else { return false }
        return perm & 0o022 == 0
    }
    let scripts = hub + "/" + K.scriptsRel
    guard sound(hub + "/" + K.budgitRel, .typeRegular), sound(hub + "/" + K.binRel, .typeDirectory), sound(scripts, .typeDirectory),
          let names = try? fm.contentsOfDirectory(atPath: scripts) else { return false }
    return names.filter { $0.hasSuffix(".py") }.allSatisfy { sound(scripts + "/" + $0, .typeRegular) }
}

// MARK: - the data line

typealias Dict = [String: Any]

func parseStatus(_ data: Data) -> Dict? {
    guard let o = (try? JSONSerialization.jsonObject(with: data)) as? Dict, o["state"] is String else { return nil }
    return o
}

/// register.jsx:125-128: no fresh quota data keeps the last known state, marked old.
/// The one menu title, in the user's language. `table` is menu.json (made from the plugin's catalog, so this file holds no
/// words of its own): the first preferred language it knows wins, English otherwise.
func menuTitle(prefs: [String], table: [String: String]) -> String {
    for p in prefs {
        if let v = table[String(p.lowercased().prefix(2))] { return v }
    }
    return table["en"] ?? "Quit Brote"
}
func loadMenuTable(dir: String) -> [String: String] {
    guard let d = try? Data(contentsOf: URL(fileURLWithPath: dir + "/menu.json")),
          let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: String] else { return [:] }
    return o
}

func mergeStatus(prev: Dict?, new: Dict) -> Dict {
    guard (new["state"] as? String) == "unknown", let p = prev, let ps = p["state"] as? String, ps != "unknown" else { return new }
    var out = new
    for k in ["state", "used", "projection", "runs_out", "resets_at"] { out[k] = p[k] }
    out["old"] = true
    return out
}

/// What a restart needs to show "<state> · old data": the five fields of the keep-last-known rule, h5, the stale COUNT and the language's words (`lang`, `t`).
/// Never the ledger texts that `status --brote` also carries.
func persistable(_ st: Dict) -> Dict {
    var out: Dict = [:]
    for k in ["state", "used", "projection", "runs_out", "resets_at", "h5", "lang", "t"] { if let v = st[k], !(v is NSNull) { out[k] = v } }
    out["stale"] = (st["stale"] as? [Any])?.count ?? 0
    return out
}

func restored(_ saved: Dict) -> Dict {
    var out = saved
    out["stale"] = Array(repeating: "", count: max(0, min(99, (saved["stale"] as? NSNumber)?.intValue ?? 0)))
    out["old"] = true
    return out
}

func statusArgv(plugin hub: String) -> [String] {
    ["/usr/bin/python3", "-B", hub + "/" + K.budgitRel, "status", "--brote", "--project", hub]
}

/// Explicit and small: a GUI app has none of the shell's variables, and Python must not write bytecode into the plugin.
func statusEnv(home: String) -> [String: String] {
    ["PATH": "/usr/bin:/bin", "HOME": home, "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"]
}

struct RunResult { var out: Data; var exit: Int32; var timedOut: Bool; var ms: Int }

private final class Flag { private let l = NSLock(); private var v = false
    func set() { l.lock(); v = true; l.unlock() }
    var isSet: Bool { l.lock(); defer { l.unlock() }; return v } }

private final class Sink {
    private let l = NSLock(); private var d = Data()
    func add(_ x: Data) { l.lock(); d.append(x); l.unlock() }
    var count: Int { l.lock(); defer { l.unlock() }; return d.count }
    var data: Data { l.lock(); defer { l.unlock() }; return d }
}

/// One child, no shell, run from "/", stdin and stderr closed, at most 1 MB of stdout, killed after `timeout` seconds.
/// A grandchild that kept the pipe open cannot hold the caller: the reader is abandoned a moment after the kill.
func runChild(_ argv: [String], env: [String: String], timeout: Double) -> RunResult {
    let t0 = Date()
    let ms = { Int(Date().timeIntervalSince(t0) * 1000) }
    let p = Process(), pipe = Pipe()
    p.executableURL = URL(fileURLWithPath: argv[0]); p.arguments = Array(argv.dropFirst()); p.environment = env
    p.currentDirectoryURL = URL(fileURLWithPath: "/")
    p.standardOutput = pipe; p.standardError = FileHandle.nullDevice; p.standardInput = FileHandle.nullDevice
    do { try p.run() } catch { return RunResult(out: Data(), exit: -1, timedOut: false, ms: ms()) }
    let late = Flag(), pid = p.processIdentifier, sink = Sink(), done = DispatchSemaphore(value: 0)
    let cap = 1_048_576
    DispatchQueue.global().async {
        let h = pipe.fileHandleForReading
        while true {
            let chunk = h.availableData
            if chunk.isEmpty { break }
            sink.add(chunk)
            if sink.count > cap { late.set(); if p.isRunning { p.terminate() }; break }
        }
        done.signal()
    }
    DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
        guard p.isRunning else { return }
        late.set(); p.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { if p.isRunning { kill(pid, SIGKILL) } }
    }
    let waited = done.wait(timeout: .now() + timeout + 1.5)
    if waited == .timedOut { late.set(); if p.isRunning { kill(pid, SIGKILL) } }
    else { p.waitUntilExit() }
    return RunResult(out: sink.data, exit: p.isRunning ? -1 : p.terminationStatus, timedOut: late.isSet, ms: ms())
}

// MARK: - the probe log

final class Probe {
    let path: String
    private var counts: [String: Int] = [:]
    private let f: DateFormatter
    init(path: String) {
        self.path = path
        f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS"
    }
    /// Truncates the log: one start, one file. Never throws.
    func reset() {
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: path, contents: Data())
    }
    /// `PROBE Pn k=v ...`. `cap` limits how many lines a name may write. A failing write is swallowed: the logger never kills the app.
    @discardableResult
    func log(_ name: String, _ fields: [(String, String)], cap: Int = Int.max) -> Bool {
        let n = (counts[name] ?? 0) + 1
        if n > cap { return false }
        counts[name] = n
        let line = f.string(from: Date()) + " PROBE " + name + " " + fields.map { $0.0 + "=" + $0.1.replacingOccurrences(of: "\n", with: " ") }.joined(separator: " ") + "\n"
        guard let h = FileHandle(forWritingAtPath: path) else { return false }
        defer { try? h.close() }
        let size = (try? h.seekToEnd()) ?? 0
        if Int(size) + line.utf8.count > K.logCap { return false }
        h.write(line.data(using: .utf8) ?? Data())
        return true
    }
    func plain(_ text: String) { _ = log("NOTE", [("text", text)]) }
}

// MARK: - the sprite runtime (JavaScriptCore)

/// Runs the plugin's own sprite section and say(), unchanged, behind a few shim functions.
final class PetBridge {
    let ctx = JSContext()!
    var lastError: String?
    static let shim = #"""
    var __p = null;
    function __mk(s){ __p = new Pet(s); }
    function __cfg(j){ var c = JSON.parse(j);
      var want = Object.prototype.hasOwnProperty.call(PALS, c.state) ? c.state : 'contento';
      if (want !== __p.state) { var was = __p.state; __p.setState(want); if (was === 'dormido') __p.do('wake'); }
      __p.h5 = c.h5|0; __p.stale = c.stale|0; __p.held = !!c.held; __p.hover = !!c.hover; __p.walking = !!c.walking; }
    function __tick(){ __p.tick(); }
    function __do(a){ __p.do(a); }
    function __dz(b){ __p.dizzyNext = !!b; }
    function __frame(){ return JSON.stringify(__p.frame()); }
    function __hitGrid(){ var S = __p.spec(); S.ex = []; S.bubble = -1; S.fallen = false; return JSON.stringify(compose(S, __p.pal())); }
    function __act(){ return __p.act === null ? '' : __p.act; }
    function __fid(){ return !!__p.fid; }
    function __state(){ return __p.state; }
    function __say(j){ return say(JSON.parse(j)); }
    """#

    init?(sprites: String, say: String) {
        ctx.exceptionHandler = { [unowned self] _, e in self.lastError = e?.toString() }
        _ = ctx.evaluateScript(sprites)
        _ = ctx.evaluateScript(say)
        _ = ctx.evaluateScript(PetBridge.shim)
        if lastError != nil { return nil }
    }

    /// Reads sprites.js and say.js from `dir`. The reason is returned when it cannot.
    static func load(dir: String) -> (PetBridge?, String) {
        guard let s = try? String(contentsOfFile: dir + "/sprites.js", encoding: .utf8) else { return (nil, "sprites.js not found in " + dir) }
        guard let y = try? String(contentsOfFile: dir + "/say.js", encoding: .utf8) else { return (nil, "say.js not found in " + dir) }
        guard let b = PetBridge(sprites: s, say: y) else { return (nil, "JavaScriptCore refused the scripts") }
        return (b, "")
    }

    private func call(_ f: String, _ args: [Any] = []) -> JSValue? { ctx.objectForKeyedSubscript(f)?.call(withArguments: args) }
    func make(_ state: String) { _ = call("__mk", [state]) }
    func configure(state: String, h5: Int, stale: Int, held: Bool, hover: Bool, walking: Bool) {
        let o: [String: Any] = ["state": state, "h5": h5, "stale": stale, "held": held, "hover": hover, "walking": walking]
        if let d = try? JSONSerialization.data(withJSONObject: o), let s = String(data: d, encoding: .utf8) { _ = call("__cfg", [s]) }
    }
    func tick() { _ = call("__tick") }
    func act(_ name: String) { _ = call("__do", [name]) }
    func dizzyNext(_ on: Bool) { _ = call("__dz", [on]) }
    var currentAct: String? { let a = call("__act")?.toString() ?? ""; return a.isEmpty ? nil : a }
    var fidgeting: Bool { call("__fid")?.toBool() ?? false }
    var state: String { call("__state")?.toString() ?? "" }
    func frame() -> Grid? { call("__frame")?.toString().flatMap { Grid(json: $0) } }
    func hitGrid() -> Grid? { call("__hitGrid")?.toString().flatMap { Grid(json: $0) } }
    func say(_ d: Dict) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: d), let s = String(data: data, encoding: .utf8) else { return nil }
        lastError = nil
        let r = call("__say", [s])?.toString()
        return lastError == nil ? r : nil
    }
}

/// What the app tells the pet: data, held, hover, walking, clicks.
final class PetGlue {
    let bridge: PetBridge
    var state = "contento", h5 = 0, stale = 0
    var held = false, hover = false, walking = false
    var pokes = PokeCounter()
    init(bridge: PetBridge) { self.bridge = bridge; bridge.make(state); push() }

    func push() { bridge.configure(state: state, h5: h5, stale: stale, held: held, hover: hover, walking: walking) }
    func setData(state s: String, h5 h: Int, stale n: Int) { state = s; h5 = h; stale = n; push() }
    func setHeld(_ on: Bool) { held = on; if on { hover = false }; push() }
    func setHover(_ on: Bool) {
        guard on != hover else { return }
        hover = on
        push()
        if on && bridge.currentAct == nil && !held && bridge.state != "dormido" { bridge.act("hello") }
    }
    var busy: Bool { bridge.currentAct != nil || bridge.fidgeting || hover || held }
    @discardableResult
    func click(now: Double) -> Click {
        let c = pokes.click(now: now, dormido: bridge.state == "dormido")
        if c == .grumpy { bridge.act("grumpy") } else if c == .poke { bridge.act("poke") }
        return c
    }
    func tick() { bridge.tick() }
}

// MARK: - the self-test (no window server)

#if CORE_ONLY
let isCoreBuild = true
#else
let isCoreBuild = false
#endif

func cpuSeconds() -> Double {
    var ru = rusage(); getrusage(RUSAGE_SELF, &ru)
    return Double(ru.ru_utime.tv_sec + ru.ru_stime.tv_sec) + Double(ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6
}
var cpuBuckets = [0.0, 0.0, 0.0]                           // CPU seconds spent in the 10 Hz tick, the pointer poll and the Claude-window poll since the last P7
func timed(_ bucket: Int, _ f: () -> Void) { let t = cpuSeconds(); f(); cpuBuckets[bucket] += cpuSeconds() - t }
func runBench(assets: String) -> Never {
    let (bridge, why) = PetBridge.load(dir: assets)
    guard let b = bridge else { fputs("bench: " + why + "\n", stderr); exit(1) }
    let glue = PetGlue(bridge: b)
    let n = 2000
    func per(_ name: String, _ f: () -> Void) {
        let t0 = cpuSeconds(); for _ in 0..<n { f() }
        print(String(format: "%-28@ %8.1f us/call", name as NSString, (cpuSeconds() - t0) / Double(n) * 1e6))
    }
    per("configure (push)") { glue.push() }
    per("pet.tick") { glue.tick() }
    per("frame() = JS + parse") { _ = b.frame() }
    per("hitGrid() = JS + parse") { _ = b.hitGrid() }
    var changed = 0, last: Grid?
    let t0 = cpuSeconds()
    for _ in 0..<n { glue.push(); glue.tick(); let g = b.frame(); if g != last { changed += 1; last = g } }
    let tickUs = (cpuSeconds() - t0) / Double(n) * 1e6
    print(String(format: "full tick (push+tick+frame)  %8.1f us/call; frame changed on %d of %d ticks", tickUs, changed, n))
    print(String(format: "=> at 10 Hz: %.2f %% of one core (tick only, no drawing)", tickUs * 10 / 1e4))
    exit(0)
}

var selfChecks = 0
var selfFails: [String] = []
func check(_ ok: @autoclosure () -> Bool, _ name: String) {
    selfChecks += 1
    if !ok() { selfFails.append(name) }
}
func near(_ a: Double, _ b: Double, _ tol: Double = 0.001) -> Bool { abs(a - b) <= tol }

func runSelftest(assets: String) -> Never {
    setenv("TZ", "UTC", 1); tzset()
    let tmp = NSTemporaryDirectory() + "brote-selftest-" + UUID().uuidString
    try? FileManager.default.createDirectory(atPath: tmp, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(atPath: tmp) }

    // colours
    check(parseColor("#8f74d4") == RGBA(r: 0x8f, g: 0x74, b: 0xd4, a: 255), "hex")
    check(parseColor("#ffffff") == RGBA(r: 255, g: 255, b: 255, a: 255), "hex white")
    check(parseColor("rgba(90,70,120,.14)") == RGBA(r: 90, g: 70, b: 120, a: 36), "rgba shadow")
    check(parseColor("rgba(90,70,120,.06)") == RGBA(r: 90, g: 70, b: 120, a: 15), "rgba shadow 2")
    check(parseColor("rgb(1, 2, 3)") == RGBA(r: 1, g: 2, b: 3, a: 255), "rgb")
    check(parseColor("red") == nil && parseColor("#fff") == nil && parseColor("rgba(1,2)") == nil && parseColor("") == nil, "bad colours")

    // geometry
    check(Rc(0, 0, 10, 10).contains(Pt(0, 0)) && !Rc(0, 0, 10, 10).contains(Pt(10, 5)), "Rc.contains is half open")
    check(Rc(0, 0, 10, 10).inflated(2) == Rc(-2, -2, 14, 14), "Rc.inflated")
    check(flipY(Rc(100, 50, 200, 300), primaryHeight: 900) == Rc(100, 550, 200, 300), "flipY")
    check(near(clamp(5, 0, 3), 3) && near(clamp(-1, 0, 3), 0), "clamp")

    // the sprite runtime
    let t0 = Date()
    let (bridgeOpt, why) = PetBridge.load(dir: assets)
    check(bridgeOpt != nil, "JSC loads sprites.js and say.js (" + why + ")")
    guard let bridge = bridgeOpt else { finishSelftest() }
    let loadMs = Int(Date().timeIntervalSince(t0) * 1000)
    check(loadMs < 3000, "JSC load under 3 s (\(loadMs) ms)")
    let states = ["radiante", "contento", "justo", "cansado", "agotado", "enfermo", "dormido"]
    for s in states {
        let g = PetGlue(bridge: bridge)
        g.setData(state: s, h5: 0, stale: 0)
        check(bridge.state == s, "\(s): state set")
        let f = bridge.frame()
        check(f != nil && f!.w == 34 && f!.h == 28, "\(s): frame 34 x 28")
        var opaque = 0
        for c in f?.cells ?? [] { if let c = c, c.a == 255 { opaque += 1 } }
        check(opaque > 150, "\(s): frame has a body (\(opaque) opaque cells)")
        let hg = bridge.hitGrid()
        check(hg != nil && hg!.w == 34 && hg!.h == 28, "\(s): hit grid 34 x 28")
        var ticks = 0
        for _ in 0..<200 { g.tick(); ticks += 1 }
        check(bridge.frame() != nil && ticks == 200, "\(s): 200 ticks run")
    }
    let unknownGlue = PetGlue(bridge: bridge)
    unknownGlue.setData(state: "unknown", h5: 0, stale: 0)
    check(bridge.state == "contento", "unknown state shows contento")
    unknownGlue.setData(state: "raro", h5: 0, stale: 0)
    check(bridge.state == "contento", "an invented state shows contento")

    // hit mask, per state
    for s in states {
        let g = PetGlue(bridge: bridge)
        g.setData(state: s, h5: 0, stale: 0)
        for _ in 0..<60 { g.tick() }
        guard let hg = bridge.hitGrid() else { check(false, "\(s): hit grid"); continue }
        let mask = HitMask(grid: hg), bare = HitMask(grid: hg, margin: 0)
        var oc: [(Int, Int)] = []
        for y in 0..<hg.h { for x in 0..<hg.w { if let c = hg[x, y], c.a >= 128 { oc.append((x, y)) } } }
        let xs = oc.map { $0.0 }, ys = oc.map { $0.1 }
        let cx = (xs.min()! + xs.max()!) / 2
        let row = ys.min()! + (ys.max()! - ys.min()!) / 2
        let (bx, by) = oc.min(by: { abs($0.0 - cx) + abs($0.1 - row) < abs($1.0 - cx) + abs($1.1 - row) })!
        check(mask.hit(x: Double(bx) * 3 + 1.5, yTop: Double(by) * 3 + 1.5, flipped: false), "\(s): a body cell hits")
        check(bare.hit(x: Double(bx) * 3 + 1.5, yTop: Double(by) * 3 + 1.5, flipped: false), "\(s): a body cell hits without margin")
        // the margin: a cell next to the body hits with margin 1 and not without
        var edgeCell: (Int, Int)? = nil
        for (x, y) in oc where x + 1 < hg.w && (hg[x + 1, y]?.a ?? 0) < 128 && !bare.hit(x: Double(x + 1) * 3 + 1.5, yTop: Double(y) * 3 + 1.5, flipped: false) { edgeCell = (x + 1, y); break }
        if let e = edgeCell { check(mask.hit(x: Double(e.0) * 3 + 1.5, yTop: Double(e.1) * 3 + 1.5, flipped: false), "\(s): the 1 art px margin grabs the cell next to the body") }
        else { check(false, "\(s): found no cell next to the body") }
        // two art px beyond every opaque cell: no hit; the margin is exactly 1
        let far = (xs.max()! + 2, ys.min()!)
        if far.0 < 34 { check(!mask.hit(x: Double(far.0) * 3 + 1.5, yTop: Double(far.1) * 3 + 1.5, flipped: false), "\(s): ground 2 art px beyond the body does not hit") }
        check(!mask.hit(x: Double(xs.min()! - 2) * 3 + 1.5, yTop: Double(by) * 3 + 1.5, flipped: false) || xs.min()! < 2, "\(s): 2 art px left of the body does not hit")
        // every non-opaque cell is excluded from the margin-0 mask: the shadow and nothing else
        var soft = 0, softHit = 0
        for y in 0..<hg.h { for x in 0..<hg.w { if let c = hg[x, y], c.a < 128 { soft += 1; if bare.hit(x: Double(x) * 3 + 1.5, yTop: Double(y) * 3 + 1.5, flipped: false) { softHit += 1 } } } }
        check(softHit == 0, "\(s): shadow cells (\(soft)) never hit on their own")
        // mirrored
        let mm = HitMask(grid: hg.mirrored())
        var same = true
        for y in 0..<hg.h { for x in 0..<hg.w {
            let px = Double(x) * 3 + 1.5, py = Double(y) * 3 + 1.5
            if mask.hit(x: px, yTop: py, flipped: true) != mm.hit(x: px, yTop: py, flipped: false) { same = false }
        } }
        check(same, "\(s): the flipped lookup equals the mask of the mirrored frame")
        check(!mask.hit(x: -1, yTop: 5, flipped: false) && !mask.hit(x: 5, yTop: 90, flipped: false) && !mask.hit(x: 200, yTop: 5, flipped: false), "\(s): outside the sprite never hits")
    }
    // the fallen leaf of enfermo and the moon of dormido are not part of the hit grid
    do {
        let g = PetGlue(bridge: bridge)
        g.setData(state: "enfermo", h5: 0, stale: 0)
        let full = bridge.frame()!, hit = bridge.hitGrid()!
        var onlyInFull = 0
        for i in 0..<full.cells.count { if let c = full.cells[i], c.a >= 128, (hit.cells[i]?.a ?? 0) < 128 { onlyInFull += 1 } }
        check(onlyInFull >= 4, "enfermo: the fallen leaf is drawn but not grabbable (\(onlyInFull) cells)")
        g.setData(state: "dormido", h5: 0, stale: 0)
        var found = false
        for _ in 0..<80 {
            g.tick()
            let f = bridge.frame()!, h = bridge.hitGrid()!
            for i in 0..<f.cells.count { if let c = f.cells[i], c.a >= 128, (h.cells[i]?.a ?? 0) < 128 { found = true } }
        }
        check(found, "dormido: moon / Z are drawn but not grabbable")
    }
    check(ignoreMouse(overBody: false, pressed: false, dragging: false), "transparent point: ignore")
    check(!ignoreMouse(overBody: true, pressed: false, dragging: false), "body: take the mouse")
    check(!ignoreMouse(overBody: false, pressed: true, dragging: false), "pressed: take the mouse")
    check(!ignoreMouse(overBody: false, pressed: false, dragging: true), "dragging: take the mouse")
    check(!ignoreMouse(overBody: true, pressed: true, dragging: true), "all: take the mouse")

    // physics
    let wide = Bounds(minX: 0, maxX: 1000, floorY: 0, ceilY: 800)
    do { // free fall
        var b = Body(p: Pt(500, 300), v: Pt(0, 0)); var t = 0.0; var ev = Flight.flying
        while t < 3 && b.p.y > 0 { ev = stepFly(&b, dt: 1.0 / 60, in: wide); t += 1.0 / 60 }
        check(abs(t - 0.5) < 0.04, "free fall of 300 pt takes 0.5 s (\(t))")
        _ = ev
    }
    do { // bounce keeps half
        var b = Body(p: Pt(500, 0), v: Pt(0, -1000))
        _ = stepFly(&b, dt: 1.0 / 60, in: wide)
        check(near(b.v.y, 0.5 * (1000 + K.gravity / 60), 0.01), "floor bounce keeps 50 % of the impact speed (\(b.v.y))")
    }
    do { // friction stops a slide
        var b = Body(p: Pt(100, 0), v: Pt(1800, 0)); var n = 0; var ev = Flight.flying
        while ev == .flying && n < 600 { ev = stepFly(&b, dt: 1.0 / 60, in: wide); n += 1 }
        check(ev == .landed, "a slide stops")
        check(b.p.x > 100 + 600 && b.p.x < 100 + 1100, "slide distance v^2 / 2f = 900 pt (\(b.p.x))")
    }
    do { // wall bounce flips x
        var b = Body(p: Pt(990, 300), v: Pt(1000, 0))
        for _ in 0..<3 { _ = stepFly(&b, dt: 1.0 / 60, in: wide) }
        check(b.v.x < 0 && b.p.x <= 1000, "wall bounce flips x (\(b.v.x))")
        var c = Body(p: Pt(10, 300), v: Pt(-1000, 0))
        for _ in 0..<3 { _ = stepFly(&c, dt: 1.0 / 60, in: wide) }
        check(c.v.x > 0 && c.p.x >= 0, "left wall bounce flips x")
    }
    do { // ceiling
        var b = Body(p: Pt(500, 790), v: Pt(0, 1000))
        _ = stepFly(&b, dt: 1.0 / 60, in: wide)
        check(b.v.y < 0 && b.p.y <= 800, "ceiling bounce flips y")
    }
    do { // a throw ends on the floor and stays inside the bounds, deterministic
        func fly() -> Body {
            var b = Body(p: Pt(300, 100), v: Pt(900, 700)); var ev = Flight.flying; var n = 0
            while ev == .flying && n < 2000 { ev = stepFly(&b, dt: 1.0 / 60, in: wide); n += 1; if b.p.x < 0 || b.p.x > 1000 || b.p.y < 0 || b.p.y > 800 { return Body(p: Pt(-1, -1), v: Pt(0, 0)) } }
            return b
        }
        let a = fly(), c = fly()
        check(a.p == c.p && a.p.y == 0 && a.v == Pt(0, 0), "a throw lands on the floor, deterministically (\(a.p))")
    }
    do { // the perch is a floor like any other
        let perch = Bounds(minX: 200, maxX: 1000, floorY: 250, ceilY: 800)
        var b = Body(p: Pt(500, 400), v: Pt(0, 300)); var ev = Flight.flying; var n = 0
        while ev == .flying && n < 2000 { ev = stepFly(&b, dt: 1.0 / 60, in: perch); n += 1 }
        check(ev == .landed && b.p.y == 250, "a throw lands on the perch line, not the screen floor")
    }
    do { // throw velocity
        let s = [Sample(t: 0.00, p: Pt(0, 0)), Sample(t: 0.05, p: Pt(50, 20)), Sample(t: 0.10, p: Pt(100, 40))]
        let v = throwVelocity(s, now: 0.10)
        check(near(v.x, 1000, 1) && near(v.y, 400, 1), "throw velocity from 0.1 s of samples (\(v))")
        let old = [Sample(t: 0.0, p: Pt(0, 0)), Sample(t: 0.05, p: Pt(500, 0))]
        check(throwVelocity(old, now: 0.5) == Pt(0, 0), "samples older than 0.1 s are ignored")
        let fast = [Sample(t: 0.0, p: Pt(0, 0)), Sample(t: 0.01, p: Pt(100, -100))]
        let vf = throwVelocity(fast, now: 0.01)
        check(vf.x == 2500 && vf.y == -2500, "velocity clamps at 2500 per axis")
        check(throwVelocity([Sample(t: 0, p: Pt(0, 0))], now: 0) == Pt(0, 0), "one sample is no throw")
    }
    check(!isDrag(from: Pt(0, 0), to: Pt(2, 1)) && isDrag(from: Pt(0, 0), to: Pt(3, 0)), "3 pt is the drag threshold")
    check(!isThrow(speed: 199) && isThrow(speed: 200) && !dizzyAfter(speed: 1499) && dizzyAfter(speed: 1500), "throw 200 / dizzy 1500")

    // pokes
    do {
        var p = PokeCounter()
        check(p.click(now: 0.0, dormido: false) == .poke && p.click(now: 0.5, dormido: false) == .poke && p.click(now: 1.0, dormido: false) == .poke, "three clicks poke")
        check(p.click(now: 1.5, dormido: false) == .grumpy, "the fourth within 2.5 s is grumpy")
        check(p.click(now: 1.6, dormido: false) == .poke, "the counter resets after grumpy")
        var q = PokeCounter()
        _ = q.click(now: 0.0, dormido: false); _ = q.click(now: 0.5, dormido: false); _ = q.click(now: 1.0, dormido: false)
        check(q.click(now: 2.6, dormido: false) == .poke, "the fourth at 2.6 s only pokes")
        var d = PokeCounter()
        check(d.click(now: 0, dormido: true) == .countedOnly, "dormido: no poke act")
        _ = d.click(now: 0.2, dormido: true); _ = d.click(now: 0.4, dormido: true)
        check(d.click(now: 0.6, dormido: true) == .grumpy, "dormido: still counts toward grumpy")
    }
    do { // through the real pet
        let g = PetGlue(bridge: bridge)
        g.setData(state: "contento", h5: 0, stale: 0)
        check(g.click(now: 10) == .poke && bridge.currentAct == "poke", "click: the pet pokes")
        _ = g.click(now: 10.1); _ = g.click(now: 10.2)
        check(g.click(now: 10.3) == .grumpy && bridge.currentAct == "grumpy", "four clicks: the pet is grumpy")
        let z = PetGlue(bridge: bridge)
        z.setData(state: "dormido", h5: 0, stale: 0)
        check(z.click(now: 20) == .countedOnly && bridge.currentAct == nil, "dormido click: no poke act")
        z.setData(state: "contento", h5: 0, stale: 0)
        check(bridge.currentAct == "wake", "dormido -> contento wakes him")
        let h = PetGlue(bridge: bridge)
        h.setData(state: "contento", h5: 0, stale: 0)
        h.setHover(true)
        check(bridge.currentAct == "hello", "hover: hello")
        let hd = PetGlue(bridge: bridge)
        hd.setData(state: "dormido", h5: 0, stale: 0)
        hd.setHover(true)
        check(bridge.currentAct == nil, "dormido hover: no hello")
        let held = PetGlue(bridge: bridge)
        held.setData(state: "contento", h5: 0, stale: 0)
        held.setHeld(true); held.setHover(true)
        check(bridge.currentAct == nil, "held: no hello")
        held.setHeld(false)
        let hot = PetGlue(bridge: bridge)
        hot.setData(state: "contento", h5: 95, stale: 2)
        check(bridge.frame() != nil, "h5 / stale are accepted")
    }

    // strolling
    do {
        var seq: [Double] = [0.0, 0.5], i = 0
        let rng = { () -> Double in defer { i += 1 }; return i < seq.count ? seq[i] : 0.9 }
        var s = Stroll(); var x = 500.0
        s.tick(state: "contento", busy: false, x: &x, home: 500, lo: 0, hi: 1000, rng: rng)
        check(s.target == 500 && !s.walking, "stroll: picks a target near home (+-60)")
        seq = [0.0, 1.0]; i = 0
        var s2 = Stroll(); var x2 = 500.0
        s2.tick(state: "contento", busy: false, x: &x2, home: 500, lo: 0, hi: 1000, rng: rng)
        check(s2.target == 560, "stroll: the farthest target is home + 60")
        s2.tick(state: "contento", busy: false, x: &x2, home: 500, lo: 0, hi: 1000, rng: rng)
        check(near(x2, 503) && s2.walking && !s2.facingLeft, "contento walks 3 pt per tick")
        var r = Stroll(); r.target = 560; var xr = 500.0
        r.tick(state: "radiante", busy: false, x: &xr, home: 500, lo: 0, hi: 1000, rng: rng)
        check(near(xr, 504.5), "radiante walks 4.5 pt per tick")
        var arrive = Stroll(); arrive.target = 502; var xa = 500.0
        arrive.tick(state: "contento", busy: false, x: &xa, home: 500, lo: 0, hi: 1000, rng: rng)
        check(xa == 502 && arrive.target == nil && !arrive.walking && !arrive.facingLeft, "stroll: arrives exactly on the target and faces front")
        var left = Stroll(); left.target = 400; var xl = 500.0
        left.tick(state: "contento", busy: false, x: &xl, home: 500, lo: 0, hi: 1000, rng: rng)
        check(xl < 500 && left.facingLeft, "walking left faces left")
        for st in ["justo", "cansado", "agotado", "enfermo", "dormido", "unknown"] {
            var t = Stroll(); t.target = 560; var xt = 500.0
            t.tick(state: st, busy: false, x: &xt, home: 500, lo: 0, hi: 1000, rng: rng)
            check(xt == 500 && !t.walking, "\(st): never strolls")
        }
        var busy = Stroll(); busy.target = 560; var xb = 500.0
        busy.tick(state: "contento", busy: true, x: &xb, home: 500, lo: 0, hi: 1000, rng: rng)
        check(xb == 500 && !busy.walking && busy.target == nil, "acting / fidgeting / hovered / held: no stroll")
        var wall = Stroll(); var xw = 10.0
        seq = [0.0, 0.0]; i = 0
        wall.tick(state: "contento", busy: false, x: &xw, home: 10, lo: 0, hi: 1000, rng: rng)
        check(wall.target == 0, "stroll targets stay inside the bounds")
        // a long walk stays within +-60 of home
        var n = 0, lo = 1e9, hi = -1e9
        var seed: UInt64 = 7
        let lcg = { () -> Double in seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Double(seed >> 11) / Double(1 << 53) }
        var wk = Stroll(); var xk = 500.0
        while n < 20000 { wk.tick(state: "radiante", busy: false, x: &xk, home: 500, lo: 0, hi: 1000, rng: lcg); lo = min(lo, xk); hi = max(hi, xk); n += 1 }
        check(lo >= 440 - 0.001 && hi <= 560 + 0.001 && hi - lo > 20, "20000 ticks stay within 500 +- 60 (\(lo)...\(hi))")
    }

    // placement
    let main = Rc(0, 0, 1440, 875), side = Rc(1440, -100, 1920, 1055)
    let claude = Rc(200, 100, 900, 700)
    do {
        let st = MemStore()
        let l = Layout(store: st, screens: [main], claude: claude)
        check(l.pos == Pt(1440 - 102 - 16, 0), "nothing saved: bottom-right of the main screen")
        l.gentleDrop(to: Pt(300, 130))
        check(l.anchor == Anchor(relX: 100, perchY: 30) && l.pos == Pt(300, 130), "gentle drop inside Claude's frame anchors")
        check(st.number("offsetX") == 100 && st.number("perchY") == 30, "gentle drop saves offsetX / perchY")
        // Claude moves: he moves by the same delta
        l.claudeChanged(to: Rc(260, 140, 900, 700))
        check(l.pos == Pt(360, 170), "Claude moved by (60, 40): he moves by (60, 40)")
        // a width change keeps him, a bottom-edge resize moves him with the corner
        l.claudeChanged(to: Rc(260, 140, 1000, 650))
        check(l.pos == Pt(360, 170), "a width / top-edge change keeps him")
        l.claudeChanged(to: Rc(260, 120, 1000, 650))
        check(l.pos == Pt(360, 150), "a bottom-edge resize moves him with the corner")
        // Claude off screen: clamped, and he returns when Claude does
        l.claudeChanged(to: Rc(1400, 120, 900, 650))
        check(l.pos.x <= 1440 - 102 && l.pos.x >= 0, "Claude half off-screen: he stays on screen (\(l.pos.x))")
        l.claudeChanged(to: Rc(260, 120, 1000, 650))
        check(l.pos == Pt(360, 150), "Claude off-screen and back: he returns to his spot, not to the clamped one (\(l.pos))")
        l.claudeChanged(to: Rc(-3000, 5000, 1000, 650))
        check(l.pos.x >= 0 && l.pos.y <= 875 - 84, "Claude far away: he is still on screen")
        l.claudeChanged(to: Rc(100, 90, 1000, 650))
        check(l.pos == Pt(200, 120), "after a far trip he is at the same offset from Claude's corner (\(l.pos))")
        l.claudeChanged(to: Rc(260, 120, 1000, 650))
        // the saved offset is only written by a gentle drop
        let w0 = st.writes
        _ = l.beginThrow(from: Pt(400, 300))
        var sg = Stroll(); sg.target = 450; var xs = l.pos.x
        sg.tick(state: "contento", busy: false, x: &xs, home: l.strollRange().home, lo: l.strollRange().lo, hi: l.strollRange().hi, rng: { 0.9 })
        check(st.writes == w0, "a throw and a stroll step never write the store")
        check(l.anchor != nil, "a throw released inside Claude's frame stays anchored")
        let b = l.flightBounds()
        check(b.floorY == 120 + 30 && b.minX == 260 && b.maxX == 260 + 1000 - 102, "anchored flight bounds: the perch line and Claude's span")
        // a drop outside clears the anchor and saves the absolute spot
        l.gentleDrop(to: Pt(1300, 600))
        check(l.anchor == nil && st.number("offsetX") == nil && st.number("perchY") == nil, "gentle drop outside clears the anchor")
        check(st.number("lastX") == 1300 && st.number("lastY") == 600, "gentle drop outside saves lastX / lastY")
        let fb = l.flightBounds()
        check(fb.floorY == 0 && fb.minX == 0 && fb.maxX == 1440 - 102 && fb.ceilY == 875 - 84, "free flight bounds: the screen")
        l.claudeChanged(to: Rc(0, 0, 900, 700))
        check(l.pos == Pt(1300, 600), "free: Claude's moves do not move him")
    }
    do { // restore
        let st = MemStore(); st.setNumber("offsetX", 120); st.setNumber("perchY", 50)
        let l = Layout(store: st, screens: [main], claude: claude)
        check(l.pos == Pt(320, 150), "restart with a saved offset puts him on Claude")
        let none = Layout(store: st, screens: [main], claude: nil)
        check(none.pos == Pt(1440 - 102 - 16, 0), "anchor saved, no Claude, no spot: the corner")
        let s2 = MemStore(); s2.setNumber("lastX", 700); s2.setNumber("lastY", 300)
        check(Layout(store: s2, screens: [main], claude: claude).pos == Pt(700, 300), "restart with a saved absolute spot")
        none.claudeChanged(to: claude)
        check(none.pos == Pt(320, 150), "Claude appearing snaps an anchored pet to its spot")
    }
    do { // narrow window, relX clamped into max(width, sprite)
        let st = MemStore()
        let l = Layout(store: st, screens: [main], claude: Rc(400, 100, 60, 400))
        l.gentleDrop(to: Pt(420, 140))
        check(l.anchor?.relX == 0, "a window narrower than the sprite: relX clamps to 0")
        let l2 = Layout(store: MemStore(), screens: [main], claude: Rc(400, 100, 900, 400))
        l2.gentleDrop(to: Pt(1250, 140))
        check(l2.anchor != nil && l2.anchor!.relX == 900 - 102, "relX clamps into the span (\(l2.anchor?.relX ?? -1))")
    }
    do { // no Claude: a drop saves the spot and leaves the saved offset alone
        let st = MemStore(); st.setNumber("offsetX", 10); st.setNumber("perchY", 20)
        let l = Layout(store: st, screens: [main], claude: nil)
        l.gentleDrop(to: Pt(500, 400))
        check(st.number("lastX") == 500 && st.number("offsetX") == 10, "no Claude: lastX saved, offset kept")
    }
    do { // screens
        check(screenIndex(for: Pt(100, 100), in: [main, side]) == 0 && screenIndex(for: Pt(2000, 100), in: [main, side]) == 1, "screen of a point")
        check(screenIndex(for: Pt(-50, 2000), in: [main, side]) == 0, "a point off every screen: the nearest")
        let l = Layout(store: MemStore(), screens: [main, side], claude: nil)
        l.gentleDrop(to: Pt(2000, 300))
        check(l.pos == Pt(2000, 300) && l.visible(for: l.pos) == side, "a drop on the second screen stays there")
        l.gentleDrop(to: Pt(3300, 2000))
        check(l.pos.x <= 3360 - 102 && l.pos.y <= 955 - 84, "clamped into the second screen")
        // L-shaped gap: a point in the gap goes to the nearest screen
        let gap = Layout(store: MemStore(), screens: [Rc(0, 0, 800, 600), Rc(1000, 400, 800, 600)], claude: nil)
        gap.gentleDrop(to: Pt(850, 100))
        check(gap.pos.x >= 0 && gap.pos.x <= 800 - 102, "the gap between screens never loses him (\(gap.pos.x))")
    }
    do { // pickClaude
        let list = [WinInfo(pid: 5, layer: 0, bounds: Rc(0, 0, 500, 400)), WinInfo(pid: 5, layer: 0, bounds: Rc(10, 10, 900, 600)),
                    WinInfo(pid: 5, layer: 25, bounds: Rc(0, 0, 2000, 30)), WinInfo(pid: 9, layer: 0, bounds: Rc(0, 0, 1400, 800))]
        let r = pickClaude(list, pids: [5], primaryHeight: 875)
        check(r == Rc(10, 875 - 610, 900, 600), "pickClaude: the largest layer-0 window of its processes, flipped")
        check(pickClaude(list, pids: [77], primaryHeight: 875) == nil, "pickClaude: none -> nil")
        check(pickClaude([WinInfo(pid: 5, layer: 0, bounds: Rc(0, 0, 20, 20))], pids: [5], primaryHeight: 875) == nil, "pickClaude: tiny windows are ignored")
    }
    do { // hub
        let fm = FileManager.default
        let root = tmp + "/hub"
        try? fm.createDirectory(atPath: root + "/plugin/bin", withIntermediateDirectories: true)
        try? fm.createDirectory(atPath: root + "/" + K.scriptsRel, withIntermediateDirectories: true)
        fm.createFile(atPath: root + "/" + K.budgitRel, contents: Data("x".utf8), attributes: [.posixPermissions: 0o755])
        fm.createFile(atPath: root + "/" + K.scriptsRel + "/studiolib.py", contents: Data("x".utf8), attributes: [.posixPermissions: 0o644])
        check(hubTrusted(root), "hubTrusted: a script of mine, not writable by others")
        for (dir, why) in [(K.scriptsRel, "plugin/scripts"), (K.binRel, "plugin/bin")] {
            try? fm.setAttributes([.posixPermissions: 0o775], ofItemAtPath: root + "/" + dir)
            check(!hubTrusted(root), "hubTrusted: a group-writable " + why + " is refused")
            try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root + "/" + dir)
        }
        try? fm.setAttributes([.posixPermissions: 0o666], ofItemAtPath: root + "/" + K.scriptsRel + "/studiolib.py")
        check(!hubTrusted(root), "hubTrusted: a world-writable module in plugin/scripts is refused")
        try? fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: root + "/" + K.scriptsRel + "/studiolib.py")
        check(hubTrusted(root), "hubTrusted: trusted again once the modes are back")
        try? fm.setAttributes([.posixPermissions: 0o775], ofItemAtPath: root + "/" + K.budgitRel)
        check(!hubTrusted(root), "hubTrusted: group-writable is refused")
        try? fm.setAttributes([.posixPermissions: 0o757], ofItemAtPath: root + "/" + K.budgitRel)
        check(!hubTrusted(root), "hubTrusted: world-writable is refused")
        check(!hubTrusted(tmp + "/nowhere"), "hubTrusted: no script -> refused")
        let link = tmp + "/linkhub"
        try? fm.createDirectory(atPath: link + "/plugin/bin", withIntermediateDirectories: true)
        try? fm.createSymbolicLink(atPath: link + "/" + K.budgitRel, withDestinationPath: root + "/" + K.budgitRel)
        check(!hubTrusted(link), "hubTrusted: a symlink is refused (attributesOfItem does not follow it)")
    }

    // the data line
    let argv = statusArgv(plugin: "/h")
    check(argv == ["/usr/bin/python3", "-B", "/h/plugin/bin/budgit", "status", "--brote", "--project", "/h"], "status argv")
    check(!argv.contains("--limits-json") && !argv.contains("-c") && !argv.contains { $0.hasPrefix("/bin/") }, "no shell, no limits json")
    let env = statusEnv(home: "/Users/x")
    check(env["PYTHONDONTWRITEBYTECODE"] == "1" && env["PYTHONIOENCODING"] == "utf-8" && env["PATH"] == "/usr/bin:/bin" && env["HOME"] == "/Users/x" && env.count == 5, "status env is explicit and small")
    check(K.statusTimeout == 5 && K.statusEvery == 30, "5 s kill, 30 s refresh")
    check(parseStatus(Data("{\"state\":\"cansado\",\"h5\":80}".utf8)) != nil, "parseStatus: a status object")
    check(parseStatus(Data("not json".utf8)) == nil && parseStatus(Data("[1]".utf8)) == nil && parseStatus(Data("{}".utf8)) == nil && parseStatus(Data()) == nil, "parseStatus: malformed -> nil")
    do {
        let prev: Dict = ["state": "justo", "used": 40.0, "projection": 95.0, "resets_at": 1766400000.0, "runs_out": 1766300000.0]
        let m = mergeStatus(prev: prev, new: ["state": "unknown", "h5": 12])
        check((m["state"] as? String) == "justo" && (m["old"] as? Bool) == true && (m["h5"] as? Int) == 12 && (m["used"] as? Double) == 40, "unknown after a known state keeps it, marked old")
        let m2 = mergeStatus(prev: nil, new: ["state": "unknown"])
        check((m2["state"] as? String) == "unknown" && m2["old"] == nil, "unknown with nothing before stays unknown")
        let m3 = mergeStatus(prev: ["state": "unknown"], new: ["state": "unknown"])
        check((m3["state"] as? String) == "unknown", "unknown after unknown stays unknown")
        let m4 = mergeStatus(prev: prev, new: ["state": "radiante"])
        check((m4["state"] as? String) == "radiante" && m4["old"] == nil, "a known answer replaces the old one")
        let m5 = mergeStatus(prev: ["state": "dormido"], new: ["state": "unknown", "projection": 5.0])
        check((m5["state"] as? String) == "dormido" && m5["projection"] == nil, "fields missing before are missing after (as Object.assign with undefined)")
    }
    // the goldens generated from the plugin's own code: {strings: {lang: t}, cases: [{lang, d, line}]}
    var golden: (strings: [String: Dict], cases: [Dict])? = nil
    if let gd = try? Data(contentsOf: URL(fileURLWithPath: assets + "/tests/golden-say.json")),
       let o = (try? JSONSerialization.jsonObject(with: gd)) as? Dict, let st = o["strings"] as? [String: Dict], let cs = o["cases"] as? [Dict] {
        golden = (st, cs)
    }
    let es: Dict = golden?.strings["es"] ?? [:]
    do {
        let full: Dict = ["state": "justo", "used": 41.5, "projection": 96.0, "runs_out": NSNull(), "resets_at": 1766400000.0, "h5": 12,
                          "stale": ["R-1", "R-2"], "lang": "es", "t": es, "doing": [["id": "R-9", "text": "secret ledger text"]], "queue": [["id": "R-8", "text": "more"]], "counts": ["done": 3]]
        let keep = persistable(full)
        check(Set(keep.keys) == ["state", "used", "projection", "resets_at", "h5", "stale", "lang", "t"], "persistable: only the line's fields and its words (\(keep.keys.sorted()))")
        check((keep["stale"] as? Int) == 2, "persistable: the stale COUNT, not the ids")
        let back = restored(keep)
        check((back["old"] as? Bool) == true && (back["stale"] as? [Any])?.count == 2 && (back["state"] as? String) == "justo", "restored: old, with the stale count back as a list")
        check(bridge.say(back) == bridge.say(["state": "justo", "used": 41.5, "projection": 96.0, "resets_at": 1766400000.0, "h5": 12, "stale": ["a", "b"], "old": true, "t": es]), "a restored answer says the same line")
        check(bridge.say(back)?.hasSuffix(es["brote.suffix.old"] as? String ?? "?") == true, "a restored answer keeps its language")
        check(bridge.say(["state": "constructor", "t": es]) == "Brote · " && bridge.say(["state": "__proto__", "t": es]) == "Brote · ", "say(): a prototype name as state is his name, never \"undefined\"")
        check(bridge.say(["state": "justo", "t": es]) != nil && bridge.say(["state": "justo"]) == "Brote", "say(): without the words it is his name only")
    }
    do {
        let r = runChild(["/bin/sleep", "9"], env: ["PATH": "/usr/bin:/bin"], timeout: 0.5)
        check(r.timedOut && r.ms >= 400 && r.ms < 1500 && r.exit != 0, "a child that outlives its 0.5 s deadline is killed at it (\(r.ms) ms)")
        let ok = runChild(["/bin/echo", "{\"state\":\"justo\"}"], env: ["PATH": "/usr/bin:/bin"], timeout: 5)
        check(!ok.timedOut && ok.exit == 0 && parseStatus(ok.out) != nil, "a quick child returns its output")
        let big = runChild(["/usr/bin/yes"], env: [:], timeout: 5)
        check(big.out.count <= 1_048_576 + 65_536 && big.ms < 4000 && big.timedOut, "a child that talks forever is cut at 1 MB (\(big.out.count) bytes, \(big.ms) ms)")
        let cwd = runChild(["/bin/pwd"], env: [:], timeout: 5)
        check(String(data: cwd.out, encoding: .utf8) == "/\n", "the child runs from /")
        let missing = runChild(["/nonexistent/prog"], env: [:], timeout: 1)
        check(missing.exit == -1 && !missing.timedOut, "a missing program is an error, not a hang")
    }

    // say(): the goldens, in each language
    if let g = golden {
        check(g.cases.count >= 60 && g.strings.count == 2, "golden cases present (\(g.cases.count))")
        var bad = 0
        for c in g.cases {
            if var d = c["d"] as? Dict, let lang = c["lang"] as? String, let line = c["line"] as? String, let t = g.strings[lang] {
                d["t"] = t
                if bridge.say(d) != line { bad += 1; selfFails.append("golden say \(lang) \(d["state"] ?? "?") -> \(bridge.say(d) ?? "nil") != \(line)") }
            } else { bad += 1 }
        }
        check(bad == 0, "every golden say() line matches the Node result")
        selfChecks += g.cases.count
    } else {
        check(false, "tests/golden-say.json not found (or not {strings, cases}) in " + assets)
    }
    check(bridge.say(["state": "contento", "projection": 80.0, "resets_at": 1766400000.0, "t": golden?.strings["en"] ?? [:]]) == "Going well · You reach Monday.", "say() in English")
    do {                                        // the menu title follows the first preferred language it knows, English by default
        let menu = loadMenuTable(dir: assets)
        let en = menu["en"] ?? "?", es = menu["es"] ?? "?"
        check(menu.count == 2 && en == (golden?.strings["en"]?["brote.menu_quit"] as? String) && es == (golden?.strings["es"]?["brote.menu_quit"] as? String), "menu.json carries the catalog's title in both languages")
        check(menuTitle(prefs: ["es-ES", "en"], table: menu) == es && menuTitle(prefs: ["en-US"], table: menu) == en, "the menu follows the first preferred language")
        check(menuTitle(prefs: ["fr-FR", "es"], table: menu) == es && menuTitle(prefs: ["fr-FR"], table: menu) == en && menuTitle(prefs: [], table: menu) == en, "an unknown language is skipped; none at all is English")
        check(menuTitle(prefs: ["es"], table: [:]) == "Quit Brote", "without the table the title is English, never empty")
    }

    // the probe log
    do {
        let path = tmp + "/logs/probe.log"
        let p = Probe(path: path)
        p.reset()
        check(p.log("P1", [("opaque", "false"), ("bgAlpha", "0")]), "probe: a line is written")
        let text = (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
        check(text.range(of: #"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3} PROBE P1 opaque=false bgAlpha=0\n$"#, options: .regularExpression) != nil, "probe: line format")
        for _ in 0..<5 { _ = p.log("P2", [("n", "1")], cap: 3) }
        let lines = ((try? String(contentsOfFile: path, encoding: .utf8)) ?? "").split(separator: "\n").filter { $0.contains("PROBE P2") }
        check(lines.count == 3, "probe: a per-name cap")
        p.reset()
        check(((try? String(contentsOfFile: path, encoding: .utf8)) ?? "x").isEmpty, "probe: truncated at start")
        let broken = Probe(path: "/nonexistent-dir-zzz/probe.log")
        check(!broken.log("P1", [("a", "b")]), "probe: a failing write is swallowed")
        var big = true
        let q = Probe(path: tmp + "/logs/cap.log"); q.reset()
        for i in 0..<50_000 { if !q.log("PX", [("i", String(i)), ("pad", String(repeating: "x", count: 40))]) { big = false; break } }
        let size = (try? FileManager.default.attributesOfItem(atPath: tmp + "/logs/cap.log")[.size] as? Int) ?? 0
        check(!big && size <= K.logCap, "probe: 200 KB cap")
    }

    finishSelftest()
}

func finishSelftest() -> Never {
    if selfFails.isEmpty { print("SELFTEST OK \(selfChecks) checks"); exit(0) }
    for f in selfFails { print("FAIL " + f) }
    print("SELFTEST FAILED \(selfFails.count) of \(selfChecks) checks")
    exit(1)
}

#if !CORE_ONLY

// MARK: - the AppKit shell (one panel, timers, the mouse). Compile-checked by the tests; its behaviour is read from the probe log.

/// The recipe of the research (R s.4): borderless, non-activating, transparent, no shadow, floating, on every Space.
final class HostPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
    init(size: NSSize) {
        super.init(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isOpaque = false; backgroundColor = .clear; hasShadow = false
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        hidesOnDeactivate = false; isReleasedWhenClosed = false; isMovable = false; animationBehavior = .none
        ignoresMouseEvents = true
    }
}

final class SpriteView: NSView {
    let imageLayer = CALayer()
    weak var owner: Brote?
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        imageLayer.frame = CGRect(x: K.margin, y: K.margin, width: K.spriteW, height: K.spriteH)
        imageLayer.magnificationFilter = .nearest; imageLayer.minificationFilter = .nearest; imageLayer.contentsGravity = .resize
        layer?.addSublayer(imageLayer)
    }
    required init?(coder: NSCoder) { fatalError("not used") }
    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { owner?.pressed() }
    override func mouseDragged(with event: NSEvent) { owner?.dragged() }
    override func mouseUp(with event: NSEvent) { owner?.released() }
    override func rightMouseDown(with event: NSEvent) { owner?.showMenu(event, in: self) }
    func setImage(_ img: CGImage?) {
        CATransaction.begin(); CATransaction.setDisableActions(true)
        imageLayer.contents = img
        CATransaction.commit()
    }
}

func makeImage(_ g: Grid, flipped: Bool) -> CGImage? {
    let src = flipped ? g.mirrored() : g
    var bytes = [UInt8](repeating: 0, count: g.w * g.h * 4)
    for (i, c) in src.cells.enumerated() {
        guard let c = c else { continue }
        let a = Int(c.a)
        bytes[i * 4] = UInt8(Int(c.r) * a / 255); bytes[i * 4 + 1] = UInt8(Int(c.g) * a / 255); bytes[i * 4 + 2] = UInt8(Int(c.b) * a / 255); bytes[i * 4 + 3] = c.a
    }
    guard let provider = CGDataProvider(data: Data(bytes) as CFData), let cs = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
    return CGImage(width: g.w, height: g.h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: g.w * 4, space: cs,
                   bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue), provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
}

func uptime() -> Double { ProcessInfo.processInfo.systemUptime }
func mousePt() -> Pt { let m = NSEvent.mouseLocation; return Pt(Double(m.x), Double(m.y)) }

func windowList() -> [WinInfo] {
    guard let arr = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return [] }
    return arr.compactMap { d in
        guard let pid = d[kCGWindowOwnerPID as String] as? Int, let layer = d[kCGWindowLayer as String] as? Int,
              let bd = d[kCGWindowBounds as String] as? [String: Any], let r = CGRect(dictionaryRepresentation: bd as CFDictionary) else { return nil }
        return WinInfo(pid: pid, layer: layer, bounds: Rc(Double(r.origin.x), Double(r.origin.y), Double(r.width), Double(r.height)))
    }
}

enum Mode { case rest, held(grab: Pt, start: Pt, moved: Bool), flying(body: Body, bounds: Bounds, dizzy: Bool) }

final class Brote: NSObject {
    let store = DefaultsStore()
    let probe: Probe
    let glue: PetGlue
    let layout: Layout
    let panel = HostPanel(size: NSSize(width: K.winW, height: K.winH))
    let view: SpriteView
    var hubURL: URL?
    let dataOn: Bool
    var mode = Mode.rest
    var stroll = Stroll()
    var samples: [Sample] = []
    var pressedDown = false
    var mask: HitMask?
    var cachedGrid: Grid?, cachedFlip = false
    var status: Dict?
    var statusBusy = false, docsProbed = false
    var hitTimer: Timer?, flightTimer: Timer?
    var lastFlight = 0.0, lastOrigin: NSPoint?
    var cpuMark = (wall: uptime(), cpu: 0.0), flightSeconds = 0.0, cpuFirst = true, touched = false

    init(args: [String], policyAccepted: Bool) {
        probe = Probe(path: NSHomeDirectory() + "/Library/Logs/BroteWindow/probe.log")
        probe.reset()
        dataOn = !args.contains("--no-data")
        let (bridge, why) = PetBridge.load(dir: Bundle.main.resourcePath ?? ".")
        guard let b = bridge else { fputs("brote: " + why + "\n", stderr); probe.plain("start failed: " + why); exit(1) }
        glue = PetGlue(bridge: b)
        let screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        layout = Layout(store: store, screens: screens, claude: nil)
        view = SpriteView(frame: NSRect(x: 0, y: 0, width: K.winW, height: K.winH))
        super.init()
        view.owner = self
        panel.contentView = view

        // hub: named on the command line, only with the data line on (stage 1 never touches ~/Documents), and only if the script is mine and not writable by others
        if dataOn {
            if let i = args.firstIndex(of: "--hub"), i + 1 < args.count {
                let h = URL(fileURLWithPath: args[i + 1]).resolvingSymlinksInPath()
                if hubTrusted(h.path) { hubURL = h } else { probe.plain("hub refused (plugin/bin/budgit, plugin/bin, plugin/scripts or a .py there is missing, not mine, or writable by others): " + h.path) }
            } else { probe.plain("no --hub: the data line stays off") }
        }

        probe.log("P4", [("policyAccepted", String(policyAccepted)), ("activationPolicy", String(NSApp.activationPolicy().rawValue))])
        probe.log("START", [("dataOn", String(dataOn)), ("hub", hubURL?.path ?? "none"), ("pid", String(getpid()))])
        probeStartup(b)
        claudePoll()
        if dataOn { loadSaved() }
        rebuildFrame()
        positionWindows()
        panel.orderFrontRegardless()
        probe.log("P1", [("opaque", String(panel.isOpaque)), ("bgAlpha", String(Double(panel.backgroundColor?.alphaComponent ?? -1))), ("shadow", String(panel.hasShadow)),
                         ("level", String(panel.level.rawValue)), ("panels", "1"), ("ignoring", String(panel.ignoresMouseEvents))])
        installMonitors()
        schedule()
        if dataOn { refreshStatus() }
        probeHost(); probeResources()
    }

    static func rc(_ r: NSRect) -> Rc { Rc(Double(r.origin.x), Double(r.origin.y), Double(r.width), Double(r.height)) }

    // MARK: timers
    func schedule() {
        func every(_ s: Double, _ f: @escaping () -> Void) { let t = Timer(timeInterval: s, repeats: true) { _ in f() }; t.tolerance = s * 0.2; RunLoop.main.add(t, forMode: .common) }
        every(0.1) { [unowned self] in timed(0) { self.tick() } }
        every(0.5) { [unowned self] in timed(2) { self.claudePoll() } }
        every(K.statusEvery) { [unowned self] in if self.dataOn { self.refreshStatus() } }
        every(60) { [unowned self] in self.probeHost(); self.probeResources() }
        scheduleHit(0.05)
    }
    func scheduleHit(_ s: Double) {
        hitTimer?.invalidate()
        let t = Timer(timeInterval: s, repeats: false) { [unowned self] _ in timed(1) { self.hitPoll() } }
        RunLoop.main.add(t, forMode: .common); hitTimer = t
    }

    // MARK: the 10 fps tick
    func tick() {
        if case .rest = mode {
            let r = layout.strollRange()
            var x = layout.pos.x
            stroll.tick(state: glue.bridge.state, busy: glue.busy || pressedDown, x: &x, home: r.home, lo: r.lo, hi: r.hi, rng: { Double.random(in: 0..<1) })
            if x != layout.pos.x { layout.pos.x = x; positionWindows() }
            glue.walking = stroll.walking
        } else { glue.walking = false }
        glue.push(); glue.tick()
        rebuildFrame()
    }

    func rebuildFrame() {
        guard let g = glue.bridge.frame() else { return }
        let flip = stroll.facingLeft && stroll.walking
        if g == cachedGrid && flip == cachedFlip { return }
        cachedGrid = g; cachedFlip = flip
        view.setImage(makeImage(g, flipped: flip))
        if let hg = glue.bridge.hitGrid() { mask = HitMask(grid: hg) }
    }

    func positionWindows() {
        let o = NSPoint(x: layout.pos.x - K.margin, y: layout.pos.y - K.margin)
        if lastOrigin != o { panel.setFrameOrigin(o); lastOrigin = o }
    }

    // MARK: click-through: an adaptive poll of the pointer, never an event tap
    func hitPoll() {
        let m = mousePt(), p = layout.pos
        let over = mask?.hit(x: m.x - p.x, yTop: K.spriteH - (m.y - p.y), flipped: cachedFlip) ?? false
        var dragging = false
        if case .held(_, _, let moved) = mode { dragging = moved }
        let ignore = ignoreMouse(overBody: over, pressed: pressedDown, dragging: dragging)
        if panel.ignoresMouseEvents != ignore { panel.ignoresMouseEvents = ignore }
        if case .rest = mode, !pressedDown { glue.setHover(over) }
        let near = Rc(p.x, p.y, K.spriteW, K.spriteH).inflated(40).contains(m)
        if near { touched = true }
        var flying = false
        if case .flying = mode { flying = true }
        scheduleHit(near || pressedDown || flying || NSEvent.pressedMouseButtons != 0 ? 1.0 / 60 : 1.0 / 20)
    }

    // MARK: the mouse
    func pressed() {
        touched = true
        stopFlight()
        let m = mousePt()
        mode = .held(grab: m - layout.pos, start: m, moved: false)
        samples = [Sample(t: uptime(), p: m)]
        pressedDown = true
        panel.ignoresMouseEvents = false
    }

    func dragged() {
        guard case .held(let grab, let start, var moved) = mode else { return }
        let m = mousePt()
        if !moved {
            guard isDrag(from: start, to: m) else { return }
            moved = true; glue.setHeld(true); stroll.target = nil; stroll.walking = false
        }
        mode = .held(grab: grab, start: start, moved: moved)
        layout.pos = layout.clamped(m - grab)
        let now = uptime()
        samples.append(Sample(t: now, p: m)); samples.removeAll { now - $0.t > 0.25 }
        positionWindows()
    }

    func released() {
        pressedDown = false
        guard case .held(_, _, let moved) = mode else { return }
        mode = .rest
        glue.setHeld(false)
        guard moved else {
            let c = glue.click(now: uptime())
            probe.log("CLICK", [("result", String(describing: c))], cap: 200)
            return
        }
        let v = throwVelocity(samples, now: uptime())
        if isThrow(speed: v.speed) {
            let bounds = layout.beginThrow(from: layout.pos)
            mode = .flying(body: Body(p: layout.pos, v: v), bounds: bounds, dizzy: dizzyAfter(speed: v.speed))
            probe.log("THROW", [("speed", String(Int(v.speed))), ("anchored", String(layout.anchor != nil))], cap: 200)
            startFlight()
        } else {
            layout.gentleDrop(to: layout.pos)
            positionWindows()
            probe.log("DROP", [("speed", String(Int(v.speed))), ("anchored", String(layout.anchor != nil)), ("x", String(Int(layout.pos.x))), ("y", String(Int(layout.pos.y)))], cap: 200)
        }
    }

    func startFlight() {
        lastFlight = uptime()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [unowned self] _ in self.flightStep() }
        RunLoop.main.add(t, forMode: .common); flightTimer = t
    }
    func stopFlight() { flightTimer?.invalidate(); flightTimer = nil }

    func flightStep() {
        guard case .flying(var body, let bounds, let dizzy) = mode else { stopFlight(); return }
        let now = uptime(), dt = min(now - lastFlight, 1.0 / 30)
        lastFlight = now; flightSeconds += dt
        let ev = stepFly(&body, dt: dt, in: bounds)
        layout.pos = body.p
        positionWindows()
        if ev == .landed {
            mode = .rest; stopFlight()
            glue.bridge.dizzyNext(dizzy); glue.bridge.act("land")
            probe.log("LAND", [("x", String(Int(layout.pos.x))), ("y", String(Int(layout.pos.y))), ("dizzy", String(dizzy))], cap: 200)
        } else { mode = .flying(body: body, bounds: bounds, dizzy: dizzy) }
    }

    func showMenu(_ event: NSEvent, in v: NSView) {
        let menu = NSMenu()
        let item = NSMenuItem(title: menuTitle(prefs: Locale.preferredLanguages, table: loadMenuTable(dir: Bundle.main.resourcePath ?? ".")), action: #selector(quit), keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        NSMenu.popUpContextMenu(menu, with: event, for: v)
    }
    @objc func quit() { probe.plain("quit from the menu"); NSApp.terminate(nil) }

    // MARK: Claude's window
    func claudePoll() {
        let pids = Set(NSRunningApplication.runningApplications(withBundleIdentifier: K.claudeBundle).map { Int($0.processIdentifier) })
        let primary = Double(NSScreen.screens.first?.frame.height ?? 0)
        let c = pickClaude(windowList(), pids: pids, primaryHeight: primary)
        layout.screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        guard c != layout.claude else { return }
        let before = layout.pos, old = layout.claude
        if case .rest = mode { layout.claudeChanged(to: c) } else { layout.claude = c }
        positionWindows()
        probe.log("P10", [("claude", c.map { "\(Int($0.x)),\(Int($0.y)),\(Int($0.w))x\(Int($0.h))" } ?? "none"),
                          ("claudeDelta", old != nil && c != nil ? "\(Int(c!.x - old!.x)),\(Int(c!.y - old!.y))" : "n/a"),
                          ("petDelta", "\(Int(layout.pos.x - before.x)),\(Int(layout.pos.y - before.y))")], cap: 200)
    }

    // MARK: the data line
    func loadSaved() {
        guard let s = store.string("lastStatus"), let d = s.data(using: .utf8), let st = parseStatus(d) else { return }
        apply(restored(st))
    }

    func apply(_ st: Dict) {
        status = st
        let n = { (k: String) -> Int in (st[k] as? NSNumber)?.intValue ?? 0 }
        glue.setData(state: (st["state"] as? String) ?? "contento", h5: n("h5"), stale: (st["stale"] as? [Any])?.count ?? 0)
    }

    func refreshStatus() {
        guard dataOn, let hub = hubURL, !statusBusy else { return }
        guard hubTrusted(hub.path) else { probe.log("P9", [("exit", "hub no longer trusted")], cap: 5); return }
        statusBusy = true
        let argv = statusArgv(plugin: hub.path), env = statusEnv(home: NSHomeDirectory())
        let cache = hub.appendingPathComponent(K.pluginDir).appendingPathComponent("scripts").appendingPathComponent("__pycache__").path
        DispatchQueue.global(qos: .utility).async {
            let before = FileManager.default.fileExists(atPath: cache)
            let r = runChild(argv, env: env, timeout: K.statusTimeout)
            let after = FileManager.default.fileExists(atPath: cache)
            DispatchQueue.main.async {
                self.statusBusy = false
                var state = "none"
                if r.exit == 0, !r.timedOut, let d = parseStatus(r.out) {
                    let merged = mergeStatus(prev: self.status, new: d)
                    state = (merged["state"] as? String) ?? "?"
                    if let j = try? JSONSerialization.data(withJSONObject: persistable(merged)), let s = String(data: j, encoding: .utf8) { self.store.setString("lastStatus", s) }
                    self.apply(merged)
                }
                self.probe.log("P9", [("ms", String(r.ms)), ("exit", String(r.exit)), ("timedOut", String(r.timedOut)), ("state", state),
                                      ("pycacheBefore", String(before)), ("pycacheAfter", String(after))], cap: 60)
            }
        }
    }

    // MARK: the probes (an app cannot see a permission dialog; the hand-test rows (c) and (d) are the detector)
    var monitors: [Any] = []
    func installMonitors() {
        func note(_ via: String) {
            let m = mousePt(), p = layout.pos
            guard Rc(p.x, p.y, K.spriteW, K.spriteH).inflated(60).contains(m) else { return }     // clicks elsewhere on the machine are not logged
            let over = mask?.hit(x: m.x - p.x, yTop: K.spriteH - (m.y - p.y), flipped: cachedFlip) ?? false
            probe.log("P2", [("rel", "\(Int(m.x - p.x)),\(Int(m.y - p.y))"), ("alpha", String(over)), ("ignoring", String(panel.ignoresMouseEvents)), ("via", via)], cap: 200)
        }
        if let g = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown], handler: { _ in note("global") }) { monitors.append(g) }
        if let l = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown], handler: { e in note("local"); return e }) { monitors.append(l) }
    }

    func probeStartup(_ b: PetBridge) {
        let t0 = Date()
        var ok = true
        for _ in 0..<100 { b.tick(); if b.frame() == nil { ok = false } }
        probe.log("P11", [("jscOK", String(ok)), ("frames", "100"), ("ms", String(Int(Date().timeIntervalSince(t0) * 1000)))])
        // P8: the app reads its OWN bundle's attributes (after the log was truncated), no subprocess
        let path = Bundle.main.bundlePath
        var names = "none"
        let n = listxattr(path, nil, 0, 0)
        if n > 0 {
            var buf = [CChar](repeating: 0, count: n)
            if listxattr(path, &buf, n, 0) > 0 { names = buf.split(separator: 0).map { String(cString: Array($0) + [0]) }.joined(separator: ",") }
        }
        probe.log("P8", [("xattr", names), ("bundle", path)])
    }

    func probeHost() {
        let list = windowList()
        let pids = Set(NSRunningApplication.runningApplications(withBundleIdentifier: K.claudeBundle).map { Int($0.processIdentifier) })
        let mine = list.filter { pids.contains($0.pid) && $0.layer == 0 }
        probe.log("P5", [("claudeWindows", String(mine.count)), ("screenCapture", String(CGPreflightScreenCaptureAccess())), ("ax", String(AXIsProcessTrusted()))], cap: 60)
        probe.log("P6", [("ax", String(AXIsProcessTrusted())), ("listen", String(CGPreflightListenEventAccess()))], cap: 60)
        let sizes = NSScreen.screens.map { ($0.frame.width, $0.frame.height) }
        let full = list.contains { w in w.layer == 0 && w.pid != Int(getpid()) && sizes.contains { Double($0.0) == w.bounds.w && Double($0.1) == w.bounds.h } }
        let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "none"
        probe.log("P3", [("case", full ? "fullscreen" : (front == K.claudeBundle ? "claude" : "normal")),
                         ("onActiveSpace", String(panel.isOnActiveSpace)), ("occlusionVisible", String(panel.occlusionState.contains(.visible)))], cap: 120)
        if dataOn, let h = hubURL, !docsProbed {
            docsProbed = true
            DispatchQueue.global(qos: .utility).async {
                var res = "ok"
                do { _ = try FileManager.default.contentsOfDirectory(atPath: h.path) } catch { res = (error as NSError).code == NSFileReadNoPermissionError ? "EPERM" : "error" }
                DispatchQueue.main.async { self.probe.log("P12", [("docsRead", res)], cap: 4) }
            }
        }
    }

    func probeResources() {
        var ru = rusage(); getrusage(RUSAGE_SELF, &ru)
        let cpu = Double(ru.ru_utime.tv_sec + ru.ru_stime.tv_sec) + Double(ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6
        let wall = uptime(), span = max(0.001, wall - cpuMark.wall)
        let pct = (cpu - cpuMark.cpu) / span * 100
        // "idle" means nobody touched him or came near for the whole interval and he did not fly; the first sample spans the whole start (JavaScriptCore, sprites)
        let state = cpuFirst ? "startup" : flightSeconds > 0.5 ? "flight" : touched ? "touched" : "idle"
        let part = { (i: Int) in String(format: "%.2f", cpuBuckets[i] / span * 100) }
        probe.log("P7", [("state", state), ("cpu", String(format: "%.2f", pct)), ("tick", part(0)), ("hit", part(1)), ("claude", part(2)),
                         ("rssMB", String(Int(ru.ru_maxrss) / 1_048_576))], cap: 120)
        cpuMark = (wall, cpu); flightSeconds = 0; cpuFirst = false; touched = false; cpuBuckets = [0, 0, 0]
    }
}

var keepAlive: Brote?

func launchApp() -> Never {
    let app = NSApplication.shared
    let accepted = app.setActivationPolicy(.accessory)
    keepAlive = Brote(args: CommandLine.arguments, policyAccepted: accepted)
    app.run()
    exit(0)
}

#endif

// MARK: - entry

let cli = CommandLine.arguments
if cli.contains("--selftest") {
    var assets = Bundle.main.resourcePath ?? "."
    if let i = cli.firstIndex(of: "--assets"), i + 1 < cli.count { assets = cli[i + 1] }
    runSelftest(assets: assets)
}
if cli.contains("--bench") {                                  // GUI-free cost of one tick's work, in CPU microseconds (R-151 idle CPU)
    var assets = Bundle.main.resourcePath ?? "."
    if let i = cli.firstIndex(of: "--assets"), i + 1 < cli.count { assets = cli[i + 1] }
    runBench(assets: assets)
}
#if CORE_ONLY
fputs("brote core build: only --selftest and --bench are available\n", stderr)
exit(2)
#else
launchApp()
#endif
