// Brote as a floating macOS window. One file, two layers:
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
    static let bookW = 60.0, bookH = 84.0                   // 20 x 28 art px, the same height as him
    static let bookGap = 8.0, bookRoom = 68.0, sideDeadband = 24.0
    static let libretaW = 380.0, libretaGap = 12.0, libretaRadius = 14.0, titleCap = 46
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
        guard let d = json.data(using: .utf8), let rows = (try? JSONSerialization.jsonObject(with: d)) as? [[Any]] else { return nil }
        self.init(rows: rows)
    }
    init?(rows: [[Any]]) {
        guard let first = rows.first else { return nil }
        w = first.count; h = rows.count; cells = []
        for r in rows {
            guard r.count == w else { return nil }
            for c in r { cells.append((c as? String).flatMap(parseColor)) }
        }
    }
    init(w: Int, h: Int, cells: [RGBA?]) { self.w = w; self.h = h; self.cells = cells }
}

/// True when the picture on screen is certainly still right: the sprite runtime gave the same key as last time and he faces the same way.
func frameUnchanged(key: String?, cachedKey: String?, flip: Bool, cachedFlip: Bool) -> Bool {
    guard let k = key, let c = cachedKey else { return false }
    return k == c && flip == cachedFlip
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


// MARK: - the book: a second body with his physics, its picture, its place

/// What the book looks like. `open` beats `hover` (being held counts as hover) beats `marked` beats `closed`.
enum BookLook: String { case closed, marked, hover, open }

func bookLook(open: Bool, hover: Bool, held: Bool, stale: Int) -> BookLook {
    open ? .open : (hover || held) ? .hover : stale > 0 ? .marked : .closed
}
/// The ribbon stays while the pointer is on the book and goes while the Libreta is open (the panel lists the stale rows itself).
func bookRibbon(look: BookLook, stale: Int) -> Bool { look != .open && stale > 0 }

/// Where its bottom-left corner may be: the visible frame, the whole book inside.
func bookBounds(_ v: Rc) -> Bounds { Bounds(minX: v.minX, maxX: max(v.minX, v.maxX - K.bookW), floorY: v.minY, ceilY: max(v.minY, v.maxY - K.bookH)) }

/// Always entirely inside the visible frame of the screen under its centre.
func bookClamp(_ p: Pt, screens: [Rc]) -> Pt {
    guard !screens.isEmpty else { return p }
    let b = bookBounds(screens[screenIndex(for: Pt(p.x + K.bookW / 2, p.y + K.bookH / 2), in: screens)])
    return Pt(clamp(p.x, b.minX, b.maxX), clamp(p.y, b.floorY, b.ceilY))
}

/// The first place: on his right (8 pt from his edge, his y); on his left when fewer than 68 pt remain on his right.
func bookSpawn(sprite: Pt, visible: Rc) -> Pt {
    let edge = sprite.x + K.spriteW
    let p = visible.maxX - edge >= K.bookRoom ? Pt(edge + K.bookGap, sprite.y) : Pt(sprite.x - K.bookGap - K.bookW, sprite.y)
    let b = bookBounds(visible)
    return Pt(clamp(p.x, b.minX, b.maxX), clamp(p.y, b.floorY, b.ceilY))
}

enum BookAction: Equatable { case stay, fly }
/// A release below throw speed leaves the book exactly where it was dropped (kept inside the bounds); at throw speed it flies.
func bookRelease(speed: Double, pos: Pt, bounds: Bounds) -> (action: BookAction, pos: Pt) {
    (isThrow(speed: speed) ? .fly : .stay, Pt(clamp(pos.x, bounds.minX, bounds.maxX), clamp(pos.y, bounds.floorY, bounds.ceilY)))
}

/// A press is a mouse-up that did not travel 3 pt, on an opaque pixel of the book.
func isBookPress(from a: Pt, to b: Pt, overBook: Bool) -> Bool { overBook && !isDrag(from: a, to: b) }

enum PressTarget { case book, brote, none }
func pressTarget(overBook: Bool, overBrote: Bool) -> PressTarget { overBook ? .book : overBrote ? .brote : .none }

/// Which side the book is on, with a deadband so it cannot flicker while he strolls past it: the previous side is kept
/// unless the centres differ by 24 pt or more. The first value is the sign of the difference. No book on screen: no side.
func bookOnLeft(bookOn: Bool, prevLeft: Bool?, bookCx: Double, broteCx: Double) -> Bool {
    guard bookOn else { return false }
    let d = bookCx - broteCx
    if let p = prevLeft, abs(d) < K.sideDeadband { return p }
    return d < 0
}
/// Pointing mirrors his frame toward a book on his left; otherwise he faces left only while walking left.
func faceFlip(pointing: Bool, bookOnLeft: Bool, facingLeft: Bool, walking: Bool) -> Bool { pointing ? bookOnLeft : (facingLeft && walking) }
/// The Libreta lists the stale rows itself: while it is open the sprite engine is told there are none.
func spriteStale(real: Int, panelOpen: Bool) -> Int { panelOpen ? 0 : real }

/// Drag, throw and rest, the state both bodies share: the 3 pt threshold, the 0.1 s pointer samples, the fly step. No window, no timer.
struct Dragger {
    enum Mode { case rest, held(grab: Pt, start: Pt, moved: Bool), flying(body: Body, bounds: Bounds, dizzy: Bool) }
    enum Drag: Equatable { case idle, started(Pt), moving(Pt) }          // the origin the body should take
    enum Release: Equatable { case notHeld, click, moved(velocity: Pt) }
    struct FlightTick { var body: Body; var landed: Bool; var dizzy: Bool }
    private(set) var mode = Mode.rest
    private(set) var samples: [Sample] = []
    var isHeld: Bool { if case .held = mode { return true }; return false }
    var isDragging: Bool { if case .held(_, _, let moved) = mode { return moved }; return false }
    var isFlying: Bool { if case .flying = mode { return true }; return false }
    var isRest: Bool { if case .rest = mode { return true }; return false }

    mutating func press(at m: Pt, origin: Pt, now: Double) {
        mode = .held(grab: m - origin, start: m, moved: false)
        samples = [Sample(t: now, p: m)]
    }
    mutating func drag(to m: Pt, now: Double) -> Drag {
        guard case .held(let grab, let start, let was) = mode else { return .idle }
        var started = false
        if !was {
            guard isDrag(from: start, to: m) else { return .idle }
            started = true
        }
        mode = .held(grab: grab, start: start, moved: true)
        samples.append(Sample(t: now, p: m)); samples.removeAll { now - $0.t > 0.25 }
        return started ? .started(m - grab) : .moving(m - grab)
    }
    mutating func release(now: Double) -> Release {
        guard case .held(_, _, let moved) = mode else { return .notHeld }
        mode = .rest
        return moved ? .moved(velocity: throwVelocity(samples, now: now)) : .click
    }
    mutating func fly(_ body: Body, in bounds: Bounds, dizzy: Bool) { mode = .flying(body: body, bounds: bounds, dizzy: dizzy) }
    mutating func step(dt: Double) -> FlightTick? {
        guard case .flying(var body, let bounds, let dizzy) = mode else { return nil }
        let landed = stepFly(&body, dt: dt, in: bounds) == .landed
        mode = landed ? .rest : .flying(body: body, bounds: bounds, dizzy: dizzy)
        return FlightTick(body: body, landed: landed, dizzy: dizzy)
    }
}

/// Where the book is. `pos` is nil until the first feed of this run (no feed, no book). Written to the store only on a landing.
final class BookPlace {
    private(set) var pos: Pt?
    let store: Store
    var on: Bool { pos != nil }
    init(store: Store) { self.store = store }
    func firstFeed(sprite: Pt, screens: [Rc]) {
        guard pos == nil else { return }
        if let x = store.number("bookX"), let y = store.number("bookY") { pos = bookClamp(Pt(x, y), screens: screens) }
        else { pos = bookSpawn(sprite: sprite, visible: screens.isEmpty ? Rc(0, 0, 1440, 900) : screens[screenIndex(for: spriteCentre(sprite), in: screens)]) }
    }
    func move(to p: Pt) { if pos != nil { pos = p } }
    func land(at p: Pt, screens: [Rc]) {
        let c = bookClamp(p, screens: screens)
        pos = c
        store.setNumber("bookX", c.x); store.setNumber("bookY", c.y)
    }
    /// A display went away or the Dock moved: back inside, which counts as one landing. True when it moved.
    @discardableResult
    func screensChanged(_ screens: [Rc]) -> Bool {
        guard let p = pos else { return false }
        let c = bookClamp(p, screens: screens)
        guard c != p else { return false }
        land(at: c, screens: screens)
        return true
    }
}

/// The book's colours: his state's palette (`c C l L s o` tint it like his cheeks and leaf) plus the one new key `k`.
func bookPalette(_ pal: [String: String]) -> [Character: RGBA] {
    var out: [Character: RGBA] = [:]
    for (k, v) in pal where k.count == 1 { if let c = parseColor(v) { out[Character(k)] = c } }
    // the book is brown, as in the new sprite: cover #a8683f, spine and border #5f3b27, label #f0c862, pages #fff3d6 (the same in every state; the ribbon keeps its red)
    for (k, v) in ["o": "#5f3b27", "G": "#5f3b27", "C": "#a8683f", "c": "#a8683f", "k": "#bd7f55", "l": "#f0c862", "L": "#f0c862", "s": "#5f3b27", "W": "#fff3d6", "g": "#e6cf9e"] { out[Character(k)] = parseColor(v) }
    return out
}

/// The art as data (book-icon.json, copied from the plugin's design folder): book states, the panel's icons and the progress bar.
struct BookIcon {
    struct Part { var x: Int, y: Int, rows: [String] }
    struct St { var parts: [String], lift: Int, half: Double, spark: (Int, Int)? }
    var w = 0, h = 0, ground = 0, barW = 0, barH = 0
    var parts: [String: Part] = [:], states: [BookLook: St] = [:], panel: [String: [String]] = [:]
    init?(dir: String) {
        guard let d = try? Data(contentsOf: URL(fileURLWithPath: dir + "/book-icon.json")), let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict,
              let box = o["box"] as? Dict, let bw = box["w"] as? Int, let bh = box["h"] as? Int, let gr = box["ground_row"] as? Int,
              let ps = o["parts"] as? [String: Dict], let sts = o["states"] as? [String: Dict],
              let pi = o["panel_icons"] as? [String: Any], let bar = o["progress_bar"] as? Dict, let barw = bar["w"] as? Int, let barh = bar["h"] as? Int else { return nil }
        w = bw; h = bh; ground = gr; barW = barw; barH = barh
        for (n, p) in ps { if let rows = p["rows"] as? [String] { parts[n] = Part(x: p["x"] as? Int ?? 0, y: p["y"] as? Int ?? 0, rows: rows) } }
        for (n, s) in sts {
            guard let look = BookLook(rawValue: n), let pl = s["parts"] as? [String], let half = (s["shadow_half"] as? NSNumber)?.doubleValue else { return nil }
            let sp = s["spark"] as? [Int]
            states[look] = St(parts: pl, lift: s["lift"] as? Int ?? 0, half: half, spark: sp.flatMap { $0.count == 2 ? ($0[0], $0[1]) : nil })
        }
        for (n, v) in pi { if let rows = v as? [String] { panel[n] = rows } }
        guard states.count == 4, parts["closed"] != nil, parts["open"] != nil else { return nil }
    }
    /// The picture of one state. The ribbon is a flag of its own (stale items), never drawn on the open book.
    func grid(look: BookLook, ribbon: Bool, pal: [Character: RGBA]) -> Grid {
        var cells = [RGBA?](repeating: nil, count: w * h)
        func put(_ x: Int, _ y: Int, _ c: RGBA?) { if let c = c, x >= 0, x < w, y >= 0, y < h { cells[y * w + x] = c } }
        func stamp(_ rows: [String], _ x0: Int, _ y0: Int) {
            for (j, row) in rows.enumerated() { for (i, ch) in row.enumerated() where ch != "." { put(x0 + i, y0 + j, pal[ch]) } }
        }
        guard let st = states[look] else { return Grid(w: w, h: h, cells: cells) }
        let lo = Int((Double(w) / 2 - st.half).rounded()), hi = Int((Double(w) / 2 + st.half).rounded())
        for x in lo..<hi { put(x, ground, parseColor((x == lo || x == hi - 1) ? "rgba(90,70,120,.06)" : "rgba(90,70,120,.14)")) }
        for n in st.parts where n != "ribbon" { if let p = parts[n] { stamp(p.rows, p.x, p.y - st.lift) } }
        if ribbon && look != .open, let r = parts["ribbon"] { stamp(r.rows, r.x, r.y - st.lift) }
        if let sp = st.spark, let s = parts["spark"] { stamp(s.rows, sp.0, sp.1) }
        return Grid(w: w, h: h, cells: cells)
    }
}

extension BookIcon {
    /// The progress bar: 62 x 5 art px, outline `o`, the filled part `l` (rows 1-2) and `L` (row 3) up to `fill`, the rest `P`.
    func barGrid(fill: Int, pal: [Character: RGBA]) -> Grid {
        let bw = max(barW, 3), bh = max(barH, 3)
        var cells = [RGBA?](repeating: nil, count: bw * bh)
        for y in 0..<bh { for x in 0..<bw {
            let edge = y == 0 || y == bh - 1 || x == 0 || x == bw - 1
            let ch: Character = edge ? "o" : (x <= fill ? (y == bh - 2 ? "L" : "l") : "P")
            cells[y * bw + x] = pal[ch]
        } }
        return Grid(w: bw, h: bh, cells: cells)
    }
    /// One of the panel's 8 x 8 icons (doing, next, done, later), nil when the name is not in the art file.
    func iconGrid(_ name: String, pal: [Character: RGBA]) -> Grid? {
        guard let rows = panel[name], let first = rows.first else { return nil }
        let iw = first.count
        var cells = [RGBA?](repeating: nil, count: iw * rows.count)
        for (j, row) in rows.enumerated() { for (i, ch) in row.enumerated() where ch != "." && i < iw { cells[j * iw + i] = pal[ch] } }
        return Grid(w: iw, h: rows.count, cells: cells)
    }
}

// MARK: - the Libreta: where it opens, what it lists

/// 60 art px of bar, full when everything is done.
func barFill(done: Int, total: Int) -> Int {
    guard total > 0, done > 0 else { return 0 }
    return min(60, Int((60.0 * Double(done) / Double(total)).rounded()))
}

/// Beside Claude's window when there is room, inside its right edge when there is not, and on the book's half of the screen
/// (12 pt in from the edge, full height) with no Claude window. Never taller than the visible frame, never outside it.
func libretaRect(claude: Rc?, visible: Rc, bookCentre: Pt?) -> Rc {
    let w = K.libretaW, gap = K.libretaGap
    guard let c = claude else {
        let left = bookCentre.map { $0.x < visible.center.x } ?? false
        return Rc(left ? visible.minX + gap : visible.maxX - w - gap, visible.minY, w, visible.h)
    }
    let h = min(c.h, visible.h)
    var x = c.maxX + gap
    if x + w > visible.maxX { x = min(c.maxX, visible.maxX) - w }
    x = max(visible.minX, x)
    return Rc(x, clamp(c.y, visible.minY, visible.maxY - h), w, h)
}

/// The panel is a rounded rectangle: its four corners let the pointer through.
func libretaOpaque(_ p: Pt, size: Pt, radius: Double = K.libretaRadius) -> Bool {
    guard p.x >= 0, p.y >= 0, p.x < size.x, p.y < size.y else { return false }
    let cx = p.x < radius ? radius : (p.x > size.x - radius ? size.x - radius : p.x)
    let cy = p.y < radius ? radius : (p.y > size.y - radius ? size.y - radius : p.y)
    return hypot(p.x - cx, p.y - cy) <= radius
}

/// Up to 46 characters, cut at a word with "…" when longer.
func capTitle(_ s: String, limit: Int = K.titleCap) -> String {
    let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
    guard t.count > limit else { return t }
    let head = String(t.prefix(limit - 1))
    let atWord = t[t.index(t.startIndex, offsetBy: limit - 1)] == " "
    if !atWord, let sp = head.lastIndex(of: " "), head.distance(from: head.startIndex, to: sp) > 8 {
        return String(head[..<sp]).trimmingCharacters(in: .whitespaces) + "…"
    }
    return head.trimmingCharacters(in: .whitespaces) + "…"
}

/// Version 1 feeds carry no title: the text up to its first separator, capitalised. A head under 3 characters takes what follows.
func autoTitle(_ text: String) -> String {
    var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
    for _ in 0..<2 {
        let cuts = [" (", ": ", " — ", " – ", ". ", " = "].compactMap { s.range(of: $0) }
        guard let r = cuts.min(by: { $0.lowerBound < $1.lowerBound }) else { break }
        let head = String(s[..<r.lowerBound])
        if head.count < 3, s[r].hasPrefix(":") { s = String(s[r.upperBound...]).trimmingCharacters(in: .whitespaces); continue }
        s = head; break
    }
    guard let f = s.first else { return s }
    return capTitle(f.uppercased() + s.dropFirst())
}

struct LibretaRow: Equatable { var id: String, title: String, stale: Bool, paused: Bool, caption: String? }
struct LibretaSection: Equatable { var key: String, heading: String, count: Int, rows: [LibretaRow], more: String?, empty: String? }
struct LibretaProgress: Equatable { var fill: Int?; var caption: String }
struct LibretaModel: Equatable {
    var title: String, head: String, old: String?
    var progress: LibretaProgress?
    var sections: [LibretaSection]
    var parked: String?
}

private func intOf(_ v: Any?) -> Int { (v as? NSNumber)?.intValue ?? 0 }
private func fillT(_ s: String?, _ vars: [String: String] = [:]) -> String {
    var out = s ?? ""
    for (k, v) in vars { out = out.replacingOccurrences(of: "{" + k + "}", with: v) }
    return out
}

/// Everything the panel prints, from the feed alone. Words come from the feed's catalog; a missing key prints nothing rather than a wrong word.
func libretaModel(feed: Dict, old: Bool) -> LibretaModel {
    let t = (feed["t"] as? Dict ?? [:]).compactMapValues { $0 as? String }
    let counts = feed["counts"] as? Dict ?? [:]
    let v2 = intOf(feed["version"]) >= 2
    let staleIds = Set((feed["stale"] as? [Any] ?? []).compactMap { $0 as? String })
    let idleIds = Set((feed["idle"] as? [Any] ?? []).compactMap { $0 as? String })
    func rows(_ key: String) -> [LibretaRow] {
        (feed[key] as? [Dict] ?? []).map { d in
            let id = d["id"] as? String ?? ""
            let given = (d["title"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            let title = given.isEmpty ? autoTitle(d["text"] as? String ?? "") : capTitle(given)
            let stale = staleIds.contains(id), paused = idleIds.contains(id) || (d["state"] as? String) == "idle"
            let cap = stale ? t["book.stale_cap"] : (paused ? t["book.paused"].map { $0.trimmingCharacters(in: CharacterSet(charactersIn: " ·")) } : nil)
            return LibretaRow(id: id, title: title, stale: stale, paused: paused && !stale, caption: cap)
        }
    }
    let doing = rows("doing"), doingRows = doing.filter { $0.stale } + doing.filter { !$0.stale }
    func section(_ key: String, heading: [String], empty: String, rows r: [LibretaRow], count: Int, moreKey: String) -> LibretaSection {
        let n = max(count, r.count)
        let more = n > r.count ? fillT(t[moreKey] ?? t["book.more"], ["n": String(n - r.count)]) : nil
        let hd = heading.compactMap { t[$0] }.first ?? ""
        return LibretaSection(key: key, heading: hd, count: n, rows: r, more: more,
                              empty: r.isEmpty ? (t[empty] ?? t["book.nothing"]) : nil)
    }
    var sections = [
        section("doing", heading: ["book.h_doing", "book.now"], empty: "book.empty_doing", rows: doingRows, count: intOf(counts["doing"]), moreKey: "book.more"),
        section("next", heading: ["book.h_next", "book.queue"], empty: "book.empty_next", rows: rows(v2 ? "queue_recent" : "queue"), count: intOf(counts["next"]),
                moreKey: v2 ? "book.more_newest" : "book.more")]
    var progress: LibretaProgress?
    if v2 {
        sections.append(section("done", heading: ["book.h_done"], empty: "book.empty_done", rows: rows("done_recent"), count: intOf(counts["done"]), moreKey: "book.more"))
        let total = intOf(feed["total"]), done = intOf(feed["done_total"])
        progress = total > 0
            ? LibretaProgress(fill: barFill(done: max(0, done), total: total),
                              caption: fillT(t["book.progress"], ["done": String(done), "total": String(total), "pct": String(Int((100.0 * Double(done) / Double(total)).rounded()))]))
            : LibretaProgress(fill: nil, caption: t["book.progress_none"] ?? "")
    }
    let nDoing = max(intOf(counts["doing"]), doing.count), nNext = intOf(counts["next"]), later = intOf(counts["later"])
    let head: String
    if !staleIds.isEmpty { head = t[staleIds.count == 1 ? "book.head_stale_one" : "book.head_stale_many"] ?? "" }
    else if nDoing > 0 { head = fillT(t[nDoing == 1 ? "book.head_one" : "book.head_many"], ["n": String(nDoing)]) }
    else if nNext > 0 { head = fillT(t["book.head_quiet"], ["n": String(nNext)]) }
    else { head = t["book.head_empty"] ?? "" }
    return LibretaModel(title: t["book.button"] ?? "", head: head, old: old ? t["book.old"] : nil, progress: progress, sections: sections,
                        parked: later > 0 ? fillT(t[later == 1 ? "book.parked_one" : "book.parked_many"], ["n": String(later)]) : nil)
}

// MARK: - the data line

typealias Dict = [String: Any]

/// the new whole lines of the events file since `offset` (the plugin's hook channel). Only a regular file that I own is read, at most 64 KB at a time,
/// never past the last newline; a file that shrank starts again; one over 256 KB is emptied (the hooks append, so that is safe). Returns nil when there is nothing new.
func eventsRead(path: String, offset: UInt64) -> (text: String, offset: UInt64)? {
    var st = stat()
    guard stat(path, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, st.st_uid == getuid() else { return nil }
    let size = UInt64(st.st_size)
    if size > 262_144 { truncate(path, 0); return ("", 0) }
    let from = size < offset ? 0 : offset
    if size == from { return from == offset ? nil : ("", from) }
    guard let h = FileHandle(forReadingAtPath: path) else { return nil }
    defer { try? h.close() }
    do { try h.seek(toOffset: from) } catch { return nil }
    let data = h.readData(ofLength: 65_536)
    guard let nl = data.lastIndex(of: 10) else { return data.count >= 65_536 ? ("", from + UInt64(data.count)) : nil }
    let whole = data.prefix(nl + 1)
    return (String(decoding: whole, as: UTF8.self), from + UInt64(whole.count))
}

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
    private(set) var generation = 0, cfgCalls = 0      // a new Pet each time make() runs; how many times the settings went to JS
    static let shim = #"""
    var __p = null, __d = null, __bookOn = false;
    function __mk(s){ __p = new Pet(s);
      if (typeof DIRECTOR !== 'undefined' && typeof PERSONALITY !== 'undefined') { if (!__d) __d = new DIRECTOR.Director({}); __d.attach(__p); } }
    function __S(){ var S = __p.spec(), D = __d ? __d.frameSpec() : null; if (D && S.armR !== 'point') S = D; if (__bookOn) S.ex = S.ex.filter(function(e){ return e[0] !== 'book'; }); return S; }
    function __cfg(j){ var c = JSON.parse(j);
      var want = Object.prototype.hasOwnProperty.call(PALS, c.state) ? c.state : 'contento';
      if (__d) __d.setState(want);
      if (want !== __p.state) { var was = __p.state; __p.setState(want); if (was === 'dormido') __p.do('wake'); }
      __p.h5 = c.h5|0; __p.stale = c.stale|0; __p.held = !!c.held; __p.hover = !!c.hover; __p.walking = !!c.walking; __bookOn = !!c.bookOn; }
    function __tick(){ __p.tick(); if (__d) __d.tick(); }
    function __do(a){ __p.do(a); }
    function __dz(b){ __p.dizzyNext = !!b; }
    function __frame(){ var S = __S(); return JSON.stringify({g: compose(S, __p.pal()), pt: S.armR === 'point'}); }
    function __pal(s){ return JSON.stringify(Object.prototype.hasOwnProperty.call(PALS, s) ? PALS[s] : PALS.contento); }
    function __flags(){ return '|' + (__d && __d.directing() ? 1 : 0) + (__d && __d.calling() ? 1 : 0); }
    function __key(){ return JSON.stringify([__S(), __p.pal()]) + __flags(); }
    function __step(){ __p.tick(); if (__d) __d.tick(); return JSON.stringify([__S(), __p.pal()]) + __flags(); }
    function __ev(t){ if (__d) __d.feed(t); }
    function __front(b){ if (__d) __d.setFront(b); }
    function __rm(b){ if (__d) __d.setReduceMotion(b); }
    function __touch(){ if (__d) __d.touch(); }
    function __poke(dir){ return __d ? __d.poke(dir) : false; }
    function __amb(l){ if (__d) __d.setAmbient(l); }
    function __hitGrid(){ var S = __S(); S.ex = []; S.bubble = -1; S.fallen = false; return JSON.stringify(compose(S, __p.pal())); }
    function __act(){ return __p.act === null ? '' : __p.act; }
    function __fid(){ return !!__p.fid; }
    function __state(){ return __p.state; }
    function __say(j){ return say(JSON.parse(j)); }
    """#

    init?(sprites: String, say: String, personality: String? = nil, director: String? = nil) {
        ctx.exceptionHandler = { [unowned self] _, e in self.lastError = e?.toString() }
        _ = ctx.evaluateScript(sprites)
        _ = ctx.evaluateScript(say)
        if let p = personality, let d = director { _ = ctx.evaluateScript(p); _ = ctx.evaluateScript(d) }      // the animation bank and the director; without them he keeps his own animation
        _ = ctx.evaluateScript(PetBridge.shim)
        if lastError != nil { return nil }
    }

    /// Reads sprites.js and say.js from `dir`. The reason is returned when it cannot.
    static func load(dir: String) -> (PetBridge?, String) {
        guard let s = try? String(contentsOfFile: dir + "/sprites.js", encoding: .utf8) else { return (nil, "sprites.js not found in " + dir) }
        guard let y = try? String(contentsOfFile: dir + "/say.js", encoding: .utf8) else { return (nil, "say.js not found in " + dir) }
        let pers = try? String(contentsOfFile: dir + "/personality.js", encoding: .utf8), dire = try? String(contentsOfFile: dir + "/director.js", encoding: .utf8)
        guard let b = PetBridge(sprites: s, say: y, personality: pers, director: dire) else { return (nil, "JavaScriptCore refused the scripts") }
        return (b, "")
    }

    private func call(_ f: String, _ args: [Any] = []) -> JSValue? { ctx.objectForKeyedSubscript(f)?.call(withArguments: args) }
    func make(_ state: String) { generation += 1; _ = call("__mk", [state]) }
    func configure(state: String, h5: Int, stale: Int, held: Bool, hover: Bool, walking: Bool, bookOn: Bool = false) {
        let o: [String: Any] = ["state": state, "h5": h5, "stale": stale, "held": held, "hover": hover, "walking": walking, "bookOn": bookOn]
        if let d = try? JSONSerialization.data(withJSONObject: o), let s = String(data: d, encoding: .utf8) { cfgCalls += 1; _ = call("__cfg", [s]) }
    }
    func tick() { _ = call("__tick") }
    func act(_ name: String) { _ = call("__do", [name]) }
    func dizzyNext(_ on: Bool) { _ = call("__dz", [on]) }
    var currentAct: String? { let a = call("__act")?.toString() ?? ""; return a.isEmpty ? nil : a }
    var fidgeting: Bool { call("__fid")?.toBool() ?? false }
    var state: String { call("__state")?.toString() ?? "" }
    /// A text that is the same whenever the picture is the same (the picture is a pure function of the spec and the palette); far cheaper than building and parsing it.
    func frameKey() -> String? { call("__key")?.toString() }
    /// advance one tick and return the key, in one JS call
    func step() -> String? {
        guard let r = call("__step")?.toString() else { return nil }
        let u = Array(r.utf8.suffix(2)); directing = u.count == 2 && u[0] == 49; calling = u.count == 2 && u[1] == 49
        return r
    }
    /// the director (what Claude is doing, shown on his face). `directing`: a clip of the director is on screen; `calling`: it is a call (turn finished, question, permission).
    private(set) var directing = false, calling = false
    func feed(_ lines: String) { _ = call("__ev", [lines]) }
    func setFront(_ on: Bool) { _ = call("__front", [on]) }
    func setReduceMotion(_ on: Bool) { _ = call("__rm", [on]) }
    func setAmbient(_ level: String) { _ = call("__amb", [level]) }
    func touch() { _ = call("__touch") }
    /// A click on an agotado Brote: true when the director answers it (1 neutral, 2 annoyed, 3+ angry and away from the pointer); `away` is -1 or 1.
    func pokeAgotado(away: Int) -> Bool { call("__poke", [away])?.toBool() ?? false }
    /// The picture and whether the arm is the pointing one, from one call: `{"g": grid, "pt": bool}`.
    func frameInfo() -> (grid: Grid, pointing: Bool)? {
        guard let t = call("__frame")?.toString(), let d = t.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict,
              let rows = o["g"] as? [[Any]], let g = Grid(rows: rows) else { return nil }
        return (g, (o["pt"] as? Bool) ?? false)
    }
    func frame() -> Grid? { frameInfo()?.grid }
    /// PALS[state] as colour strings: the book takes its cover, outline and leaf from it.
    func palette(_ state: String) -> [String: String] {
        guard let t = call("__pal", [state])?.toString(), let d = t.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: String] else { return [:] }
        return o
    }
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
    var bookOn = false, panelOpen = false                       // the real book is on screen; the Libreta is open (he is told there are no stale items then)
    var pokes = PokeCounter()
    init(bridge: PetBridge) { self.bridge = bridge; bridge.make(state); push() }

    private struct Cfg: Equatable { var state: String, h5: Int, stale: Int, held: Bool, hover: Bool, walking: Bool, bookOn: Bool }
    private var pushed: (cfg: Cfg, generation: Int)?
    /// Only these values are ever written to the pet, and only here, so a push of the same values to the same pet changes nothing and is skipped.
    func push() {
        let c = Cfg(state: state, h5: h5, stale: spriteStale(real: stale, panelOpen: panelOpen), held: held, hover: hover, walking: walking, bookOn: bookOn)
        if let p = pushed, p.cfg == c, p.generation == bridge.generation { return }
        pushed = (c, bridge.generation)
        bridge.configure(state: c.state, h5: c.h5, stale: c.stale, held: c.held, hover: c.hover, walking: c.walking, bookOn: c.bookOn)
    }
    func setData(state s: String, h5 h: Int, stale n: Int) { state = s; h5 = h; stale = n; push() }
    func setHeld(_ on: Bool) { held = on; if on { hover = false }; push() }
    func setHover(_ on: Bool) {
        guard on != hover else { return }
        hover = on
        push()
        if on { bridge.touch() }
        if on && bridge.currentAct == nil && !held && bridge.state != "dormido" { bridge.act("hello") }
    }
    var busy: Bool { bridge.currentAct != nil || bridge.fidgeting || bridge.directing || hover || held }
    @discardableResult
    func click(now: Double, away: Int = -1) -> Click {
        let c = pokes.click(now: now, dormido: bridge.state == "dormido")
        bridge.touch()
        if c != .countedOnly, bridge.state == "agotado", bridge.pokeAgotado(away: away) { return c }      // his own answer to pokes when worn out
        if c == .grumpy { bridge.act("grumpy") } else if c == .poke { bridge.act("poke") }
        return c
    }
    func tick() { bridge.tick() }
    /// One 10 Hz step: the settings if they changed, then one tick and the picture's key in a single JS call.
    func step() -> String? { push(); return bridge.step() }
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
let cpuBucketNames = ["tick", "hit", "claude", "js", "hitgrid", "img", "layer", "move"]
/// CPU seconds since the last P7: the 10 Hz tick (0), the pointer poll (1), the Claude-window poll (2), then the parts of the tick: JS calls (3), the hitGrid JS call (4), building the image (5), handing it to the layer (6), moving the panel (7)
var cpuBuckets = [Double](repeating: 0, count: cpuBucketNames.count)
func timed(_ bucket: Int, _ f: () -> Void) { let t = cpuSeconds(); f(); cpuBuckets[bucket] += cpuSeconds() - t }
func cpuParts(_ b: [Double], span: Double) -> [(String, String)] {
    cpuBucketNames.enumerated().map { ($0.element, String(format: "%.2f", ($0.offset < b.count ? b[$0.offset] : 0) / span * 100)) }
}

// MARK: - which thread burns the CPU (P7 `threads`)
struct ThreadCPU: Equatable { var id: UInt64; var name: String; var seconds: Double }
func currentThreadID() -> UInt64 { var t: UInt64 = 0; pthread_threadid_np(nil, &t); return t }
let mainThreadID = currentThreadID()      // this file's top level runs on the main thread

/// CPU seconds (user + system) of every live thread of this process.
func threadCPU() -> [ThreadCPU] {
    var list: thread_act_array_t?
    var count: mach_msg_type_number_t = 0
    guard task_threads(mach_task_self_, &list, &count) == KERN_SUCCESS, let l = list else { return [] }
    defer { vm_deallocate(mach_task_self_, vm_address_t(UInt(bitPattern: l)), vm_size_t(Int(count) * MemoryLayout<thread_t>.size)) }
    var out: [ThreadCPU] = []
    for i in 0..<Int(count) {
        let port = l[i]
        defer { mach_port_deallocate(mach_task_self_, port) }
        var basic = thread_basic_info(), n = mach_msg_type_number_t(MemoryLayout<thread_basic_info>.size / MemoryLayout<integer_t>.size)
        let okB = withUnsafeMutablePointer(to: &basic) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(n)) { thread_info(port, thread_flavor_t(THREAD_BASIC_INFO), $0, &n) } }
        var ident = thread_identifier_info(), m = mach_msg_type_number_t(MemoryLayout<thread_identifier_info>.size / MemoryLayout<integer_t>.size)
        let okI = withUnsafeMutablePointer(to: &ident) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(m)) { thread_info(port, thread_flavor_t(THREAD_IDENTIFIER_INFO), $0, &m) } }
        guard okB == KERN_SUCCESS, okI == KERN_SUCCESS else { continue }
        var name = ""
        if let pt = pthread_from_mach_thread_np(port) {
            var buf = [CChar](repeating: 0, count: 64)
            if pthread_getname_np(pt, &buf, 64) == 0 { name = String(cString: buf) }
        }
        let secs = Double(basic.user_time.seconds + basic.system_time.seconds) + Double(basic.user_time.microseconds + basic.system_time.microseconds) / 1e6
        out.append(ThreadCPU(id: ident.thread_id, name: name, seconds: secs))
    }
    return out
}

/// "main:0.31,name:0.12": percent of one core each thread used between two readings, the main thread first, then the busiest others (under 0.02 left out).
func threadShares(before: [ThreadCPU], after: [ThreadCPU], span: Double, mainID: UInt64, top: Int) -> String {
    var prev: [UInt64: Double] = [:]
    for t in before { prev[t.id] = t.seconds }
    var rows: [(String, Double, Bool)] = []
    for t in after {
        let pct = (t.seconds - (prev[t.id] ?? 0)) / span * 100
        let isMain = t.id == mainID
        if pct < 0.02 && !isMain { continue }
        let label = isMain ? "main" : (t.name.isEmpty ? "t\(t.id % 1000)" : t.name.replacingOccurrences(of: " ", with: "_"))
        rows.append((label, pct, isMain))
    }
    guard !rows.isEmpty else { return "none" }
    let main = rows.filter { $0.2 }, others = rows.filter { !$0.2 }.sorted { $0.1 > $1.1 }.prefix(top)
    return (main + others).map { String(format: "%@:%.2f", $0.0 as NSString, $0.1) }.joined(separator: ",")
}

/// The source stamp build.sh leaves in Resources/build.txt: exactly 12 lowercase hex characters, else "unknown".
func buildStamp(_ text: String?) -> String {
    guard let t = text?.trimmingCharacters(in: .whitespacesAndNewlines), t.count == 12, t.allSatisfy({ ("0"..."9").contains($0) || ("a"..."f").contains($0) }) else { return "unknown" }
    return t
}

func runBench(assets: String, pacedSeconds: Double? = nil) -> Never {
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
    per("key (spec+pal as text)") { _ = b.frameKey() }
    var changed = 0, last: Grid?
    let t0 = cpuSeconds()
    var lastKey: String?, built = 0
    for _ in 0..<n { let k = glue.step(); if k != lastKey { lastKey = k; built += 1; let g = b.frame(); if g != last { changed += 1; last = g } } }
    let tickUs = (cpuSeconds() - t0) / Double(n) * 1e6
    print(String(format: "full tick (push+tick+frame)  %8.1f us/call; frame changed on %d of %d ticks, built on %d (the key changed)", tickUs, changed, n, built))
    print(String(format: "=> at 10 Hz: %.2f %% of one core (tick only, no drawing)", tickUs * 10 / 1e4))
    // the same tick at the app's own pace: 10 Hz, one tick per 100 ms, so JavaScriptCore's compiled code goes cold between calls
    if let secs = pacedSeconds {
        let pg = PetGlue(bridge: b)
        pg.setData(state: cli.firstIndex(of: "--state").flatMap { i in i + 1 < cli.count ? cli[i + 1] : nil } ?? "agotado", h5: 0, stale: 0)
        var builds = 0, cpuBuild = 0.0
        var cpu = 0.0, ticks = 0, pk: String?
        let burst = cli.firstIndex(of: "--burst").flatMap { i in i + 1 < cli.count ? Int(cli[i + 1]) : nil } ?? 1
        let end = Date().addingTimeInterval(secs)
        while Date() < end {
            var c0 = cpuSeconds()
            for _ in 0..<burst { let k = pg.step(); _ = pg.busy; var built = false; if k != pk { pk = k; builds += 1; built = true; _ = b.frame() }; ticks += 1; if built { cpuBuild += cpuSeconds() - c0; c0 = cpuSeconds() } }
            cpu += cpuSeconds() - c0
            Thread.sleep(forTimeInterval: 0.1 * Double(burst))
        }
        let us = (cpu + cpuBuild) / Double(max(1, ticks)) * 1e6
        print(String(format: "paced 10 Hz tick (push+tick+key, picture only on a new key) %8.1f us/call over %d ticks, %d pictures built", us, ticks, builds))
        print(String(format: "   of which: %d ticks with a new picture cost %.0f us each, the other %d ticks %.0f us each", builds, cpuBuild / Double(max(1, builds)) * 1e6, ticks - builds, cpu / Double(max(1, ticks - builds)) * 1e6))
        print(String(format: "=> at 10 Hz: %.2f %% of one core (tick only, no drawing)", us * 10 / 1e4))
    }
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

    // P7 splits the tick cost: measure only
    do {
        let b = [0.57, 0.04, 0.06, 0.20, 0.10, 0.17, 0.05, 0.02]      // seconds over a 100 s span = percent of one core
        let parts = cpuParts(b, span: 100)
        check(parts.map { $0.0 } == ["tick", "hit", "claude", "js", "hitgrid", "img", "layer", "move"], "P7 names the tick and its five parts, in order (\(parts.map { $0.0 }))")
        check(parts.map { $0.1 } == ["0.57", "0.04", "0.06", "0.20", "0.10", "0.17", "0.05", "0.02"], "P7 prints each bucket as a percent of the span (\(parts.map { $0.1 }))")
        check(cpuParts([0, 0, 0], span: 100).count == 8, "P7 survives a short bucket array (zeros)")
        let before = cpuBuckets
        timed(5) { var x = 0.0; for i in 0..<200_000 { x += Double(i).squareRoot() }; if x < 0 { print(x) } }
        check(cpuBuckets[5] > before[5] && cpuBuckets[3] == before[3] && cpuBuckets[0] == before[0], "timed(5) adds to the img bucket only")
        cpuBuckets = before
    }

    // skip the picture when its key has not changed (idle CPU plan, step 1): the key is far cheaper than building and parsing the grid
    do {
        check(!frameUnchanged(key: nil, cachedKey: "a", flip: false, cachedFlip: false), "frame skip: no key, no skip")
        check(!frameUnchanged(key: "a", cachedKey: nil, flip: false, cachedFlip: false), "frame skip: nothing cached, no skip")
        check(frameUnchanged(key: "a", cachedKey: "a", flip: true, cachedFlip: true), "frame skip: same key, same side")
        check(!frameUnchanged(key: "a", cachedKey: "b", flip: false, cachedFlip: false) && !frameUnchanged(key: "a", cachedKey: "a", flip: true, cachedFlip: false), "frame skip: another key or another side")
        // soundness: the same key must always mean the same picture, in every state, with pokes, hover, walking and held
        var seen: [String: Grid] = [:], bad = 0, keyChanges = 0, total = 0
        for st in states {
            let kg = PetGlue(bridge: bridge)
            kg.setData(state: st, h5: st == "cansado" ? 80 : 0, stale: st == "justo" ? 3 : 0)
            var lastKey: String?
            for i in 0..<900 {
                kg.walking = (i / 150) % 2 == 1; kg.hover = (i / 90) % 3 == 1
                if i % 211 == 0 { _ = kg.click(now: Double(i)) }
                kg.push(); kg.tick()
                guard let k = bridge.frameKey(), let g = bridge.frame() else { bad += 1; continue }
                total += 1
                if k != lastKey { keyChanges += 1; lastKey = k }
                if let prev = seen[k] { if prev != g { bad += 1 } } else { seen[k] = g }
            }
        }
        check(bad == 0 && total == states.count * 900, "frame key: the same key always gave the same picture (\(bad) bad of \(total), \(seen.count) keys)")
        let ag = PetGlue(bridge: bridge); ag.setData(state: "agotado", h5: 0, stale: 0)
        var n = 0, lk: String?
        for _ in 0..<600 { ag.push(); ag.tick(); let k = bridge.frameKey(); if k != lk { n += 1; lk = k } }
        check(n * 4 < 600, "600 idle ticks of a tired pet: the key changes only \(n) times, so the picture is built \(n) times, not 600")
    }

    // one JS call per tick, and a push only when something changed (idle CPU plan: a cold JS call costs about 50 us at 10 Hz)
    do {
        let sg = PetGlue(bridge: bridge)
        sg.setData(state: "cansado", h5: 10, stale: 0)
        let c0 = bridge.cfgCalls
        sg.push(); sg.push()
        check(bridge.cfgCalls == c0, "push: the same settings are not sent again")
        sg.setHover(true)
        check(bridge.cfgCalls == c0 + 1, "push: a changed setting is sent once")
        let other = PetGlue(bridge: bridge); _ = other
        let c1 = bridge.cfgCalls
        sg.push()
        check(bridge.cfgCalls == c1 + 1, "push: a pet made since then (another glue) gets the settings again")
        sg.walking = true
        let key = sg.step()
        check(key != nil && key == bridge.frameKey(), "step: one call advances the pet and returns the key a second call would give")
        let c2 = bridge.cfgCalls
        check(c2 == c1 + 2, "step: a changed setting is sent by the step itself")
        check(sg.step() != nil && bridge.cfgCalls == c2, "step: and not again on the next tick")
    }

    // P7 says which thread burns the CPU (idle CPU plan: "other" was 0.9 % of a core that no timer of ours accounts for)
    do {
        let before = [ThreadCPU(id: 1, name: "", seconds: 1.0), ThreadCPU(id: 2, name: "JavaScriptCore Heap", seconds: 0.5), ThreadCPU(id: 3, name: "x", seconds: 2.0)]
        let after = [ThreadCPU(id: 1, name: "", seconds: 1.30), ThreadCPU(id: 2, name: "JavaScriptCore Heap", seconds: 0.62), ThreadCPU(id: 3, name: "x", seconds: 2.0), ThreadCPU(id: 4, name: "new", seconds: 0.05)]
        check(threadShares(before: before, after: after, span: 100, mainID: 1, top: 3) == "main:0.30,JavaScriptCore_Heap:0.12,new:0.05", "threadShares: percent of one core per thread, main first, quiet threads left out, spaces made safe (\(threadShares(before: before, after: after, span: 100, mainID: 1, top: 3)))")
        check(threadShares(before: before, after: after, span: 100, mainID: 1, top: 1) == "main:0.30,JavaScriptCore_Heap:0.12", "threadShares: top limits the others, not the main thread")
        check(threadShares(before: [], after: [], span: 100, mainID: 1, top: 3) == "none", "threadShares: nothing to say")
        let t0 = threadCPU(), a0 = cpuSeconds()
        var x = 0.0; let until = a0 + 0.15
        while cpuSeconds() < until { for i in 0..<20_000 { x += Double(i).squareRoot() } }
        if x < 0 { print(x) }
        let t1 = threadCPU()
        let me = mainThreadID
        let grew = (t1.first { $0.id == me }?.seconds ?? 0) - (t0.first { $0.id == me }?.seconds ?? 0)
        check(!t1.isEmpty && grew > 0.1, "threadCPU: the thread that ran 0.15 s of work shows it (+\(String(format: "%.3f", grew)) s)")
    }

    // START says which source built the bundle (build.sh writes the first 12 hex of brote.swift's sha256 into Resources/build.txt)
    do {
        check(buildStamp("0123456789ab\n") == "0123456789ab", "buildStamp: a 12-hex line is kept")
        check(buildStamp(nil) == "unknown" && buildStamp("") == "unknown", "buildStamp: no file, no stamp")
        check(buildStamp("rm -rf /\n") == "unknown" && buildStamp("0123456789AB") == "unknown" && buildStamp("0123456789abc") == "unknown" && buildStamp("0123456789a") == "unknown", "buildStamp: only exactly 12 lowercase hex characters count, anything else is unknown")
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

    do { // the director on the real sprites.js, personality.js and director.js, and the events file
        let (dbOpt, dwhy) = PetBridge.load(dir: assets)
        check(dbOpt != nil && dbOpt!.ctx.evaluateScript("typeof DIRECTOR")?.toString() == "object", "JSC loads personality.js and director.js (" + dwhy + ")")
        if let db = dbOpt {
            db.make("contento")
            db.feed("prompt abc\npre abc Bash\nrm -rf /\npre abc Edit extra words\n")
            db.tick()
            check(db.ctx.evaluateScript("__d.mode()")?.toString() == "pc_run", "a Bash event shows the terminal (and junk lines are ignored)")
            check(db.step() != nil && db.directing && !db.calling, "the director is on screen and it is not a call")
            db.feed("post abc Bash\nstop abc\n"); for _ in 0..<35 { db.tick() }; _ = db.step()
            check(db.calling && db.ctx.evaluateScript("__d.mode()")?.toString() == "att_1", "a finished turn calls him")
            check(db.ctx.evaluateScript("JSON.stringify(__S().ex.map(function(e){return e[0]}))")?.toString().contains("bub_call_check") == true, "with the peach check badge")
            check(db.frame() != nil, "and the picture builds")
            db.setFront(true); db.tick(); check(!db.calling || db.ctx.evaluateScript("__d.mode()")?.toString() == "c_answered", "coming to Claude answers the call")
            db.make("agotado"); db.configure(state: "agotado", h5: 0, stale: 0, held: false, hover: false, walking: false)
            check(db.pokeAgotado(away: 1) && db.ctx.evaluateScript("__d.state")?.toString() == "agotado", "an agotado poke is answered by the director")
        }
        let tmp = NSTemporaryDirectory() + "brote-events-\(getpid())"
        try? "".write(toFile: tmp, atomically: true, encoding: .utf8)
        check(eventsRead(path: tmp, offset: 0) == nil, "an empty events file has nothing new")
        if let h = FileHandle(forWritingAtPath: tmp) { h.seekToEndOfFile(); h.write("pre a Edit\npost a Edit\nstop a".data(using: .utf8)!); try? h.close() }
        let r1 = eventsRead(path: tmp, offset: 0)
        check(r1?.text == "pre a Edit\npost a Edit\n", "only whole lines are read")
        if let h = FileHandle(forWritingAtPath: tmp) { h.seekToEndOfFile(); h.write("\n".data(using: .utf8)!); try? h.close() }
        check(eventsRead(path: tmp, offset: r1?.offset ?? 0)?.text == "stop a\n", "the cut line is finished on the next read")
        check(eventsRead(path: tmp, offset: 9999)?.offset == 0 || eventsRead(path: tmp, offset: 9999)?.text.isEmpty == false, "a file that shrank starts again")
        check(eventsRead(path: NSTemporaryDirectory(), offset: 0) == nil && eventsRead(path: "/nonexistent/x", offset: 0) == nil, "a folder or a missing file is ignored")
        try? FileManager.default.removeItem(atPath: tmp)
    }

    // the book: geometry, states, the drag machine both bodies share, the stored place, the shim on the real sprites.js
    let vis = Rc(0, 0, 1440, 875)
    do { // bookBounds / bookClamp / bookSpawn (B9, B10)
        let bb = bookBounds(vis)
        check(bb.minX == 0 && bb.maxX == 1380 && bb.floorY == 0 && bb.ceilY == 791, "bookBounds: the visible frame minus the book's 60 x 84")
        check(bookClamp(Pt(-50, -9), screens: [vis]) == Pt(0, 0) && bookClamp(Pt(5000, 5000), screens: [vis]) == Pt(1380, 791) && bookClamp(Pt(300, 200), screens: [vis]) == Pt(300, 200), "bookClamp: all four sides, and inside stays")
        check(bookClamp(Pt(1500, 100), screens: [vis, side]) == Pt(1500, 100) && bookClamp(Pt(3400, 100), screens: [vis, side]).x == 3300, "bookClamp: the screen under the book's centre decides")
        check(bookClamp(Pt(1400, 100), screens: [vis, side]).x == 1380 && bookClamp(Pt(1420, 100), screens: [vis, side]).x == 1440, "bookClamp: a book across the seam goes to the screen its centre is on")
        check(bookClamp(Pt(5, 5), screens: []) == Pt(5, 5), "bookClamp: no screens, no change")
        check(bookSpawn(sprite: Pt(1000, 0), visible: vis) == Pt(1110, 0), "bookSpawn: on his right, 8 pt from his edge, same y")
        check(bookSpawn(sprite: Pt(1440 - 102 - 68, 0), visible: vis) == Pt(1380, 0), "bookSpawn: exactly 68 pt of room still goes right")
        check(bookSpawn(sprite: Pt(1440 - 102 - 67, 0), visible: vis) == Pt(1440 - 102 - 67 - 8 - 60, 0), "bookSpawn: 67 pt of room goes to his left")
        let tiny = bookSpawn(sprite: Pt(0, 0), visible: Rc(0, 0, 150, 875))
        check(tiny.x >= 0 && tiny.x <= 90, "bookSpawn: even a tiny screen keeps the book inside (\(tiny.x))")
    }
    do { // bookRelease (B2, B8)
        let b = bookBounds(vis)
        check(bookRelease(speed: 199, pos: Pt(400, 300), bounds: b).action == .stay && bookRelease(speed: 199, pos: Pt(400, 300), bounds: b).pos == Pt(400, 300), "bookRelease: below throw speed it stays exactly where it was dropped")
        check(bookRelease(speed: 200, pos: Pt(400, 300), bounds: b).action == .fly, "bookRelease: at throw speed it flies")
        check(bookRelease(speed: 0, pos: Pt(-30, 9000), bounds: b).pos == Pt(0, 791), "bookRelease: a drop is clamped into the bounds")
        var body = Body(p: Pt(300, 300), v: Pt(900, 600)); var ev = Flight.flying; var n = 0, inside = true
        while ev == .flying && n < 3000 { ev = stepFly(&body, dt: 1.0 / 60, in: b); n += 1; if body.p.x < 0 || body.p.x > 1380 || body.p.y < 0 || body.p.y > 791 { inside = false } }
        check(ev == .landed && inside && body.p.y == 0, "a thrown book lands on the floor of the visible frame, inside the bounds (\(body.p))")
    }
    do { // precedence (B5), ribbon (B4)
        check(bookLook(open: true, hover: true, held: true, stale: 2) == .open, "book look: open beats hover, held and marked")
        check(bookLook(open: false, hover: true, held: false, stale: 2) == .hover && bookLook(open: false, hover: false, held: true, stale: 0) == .hover, "book look: hover, and being held counts as hover")
        check(bookLook(open: false, hover: false, held: false, stale: 1) == .marked && bookLook(open: false, hover: false, held: false, stale: 0) == .closed, "book look: marked with a stale item, else closed")
        check(bookRibbon(look: .marked, stale: 1) && bookRibbon(look: .hover, stale: 1) && !bookRibbon(look: .open, stale: 1) && !bookRibbon(look: .closed, stale: 0) && !bookRibbon(look: .hover, stale: 0), "ribbon: shown with stale items except while open")
    }
    do { // press and overlap (B1, B12)
        check(isBookPress(from: Pt(10, 10), to: Pt(12, 10), overBook: true) && !isBookPress(from: Pt(10, 10), to: Pt(13, 10), overBook: true) && !isBookPress(from: Pt(10, 10), to: Pt(10, 10), overBook: false), "book press: under 3 pt on an opaque pixel; 3 pt is a drag")
        check(pressTarget(overBook: true, overBrote: false) == .book && pressTarget(overBook: false, overBrote: true) == .brote && pressTarget(overBook: true, overBrote: true) == .book && pressTarget(overBook: false, overBrote: false) == .none, "pressTarget: the book wins where both are under the pointer")
        check(!ignoreMouse(overBody: pressTarget(overBook: true, overBrote: false) != .none, pressed: false, dragging: false) && ignoreMouse(overBody: pressTarget(overBook: false, overBrote: false) != .none, pressed: false, dragging: false), "click-through follows the union of both masks")
    }
    do { // which way he points (B7, B11)
        check(bookOnLeft(bookOn: true, prevLeft: nil, bookCx: 100, broteCx: 200) && !bookOnLeft(bookOn: true, prevLeft: nil, bookCx: 300, broteCx: 200), "book side: the sign of the difference the first time")
        check(!bookOnLeft(bookOn: false, prevLeft: true, bookCx: 100, broteCx: 200), "book side: no book on screen, no side")
        check(bookOnLeft(bookOn: true, prevLeft: true, bookCx: 215, broteCx: 200) && !bookOnLeft(bookOn: true, prevLeft: false, bookCx: 185, broteCx: 200), "book side: inside 24 pt the previous side is kept")
        check(!bookOnLeft(bookOn: true, prevLeft: true, bookCx: 225, broteCx: 200) && bookOnLeft(bookOn: true, prevLeft: false, bookCx: 175, broteCx: 200), "book side: 24 pt or more flips it")
        check(faceFlip(pointing: true, bookOnLeft: true, facingLeft: false, walking: false) && !faceFlip(pointing: true, bookOnLeft: false, facingLeft: true, walking: true), "faceFlip: pointing mirrors only with the book on his left")
        check(faceFlip(pointing: false, bookOnLeft: true, facingLeft: true, walking: true) && !faceFlip(pointing: false, bookOnLeft: true, facingLeft: true, walking: false) && !faceFlip(pointing: false, bookOnLeft: false, facingLeft: false, walking: true), "faceFlip: not pointing keeps the walking-facing rule")
        check(spriteStale(real: 3, panelOpen: true) == 0 && spriteStale(real: 3, panelOpen: false) == 3, "the sprite gets stale 0 while the panel is open")
    }
    do { // the drag machine both bodies use (extracted from Brote; B2, B3, B8)
        var d = Dragger()
        check(!d.isHeld && !d.isFlying, "dragger: starts at rest")
        d.press(at: Pt(110, 120), origin: Pt(100, 100), now: 0)
        check(d.isHeld && !d.isDragging, "dragger: a press holds, it is not yet a drag")
        check(d.drag(to: Pt(112, 120), now: 0.02) == .idle && !d.isDragging, "dragger: 2 pt is not a drag")
        check(d.drag(to: Pt(113, 120), now: 0.04) == .started(Pt(103, 100)) && d.isDragging, "dragger: 3 pt starts the drag and gives the new origin (grab kept)")
        check(d.drag(to: Pt(150, 130), now: 0.06) == .moving(Pt(140, 110)), "dragger: then it follows the pointer")
        check(d.release(now: 0.07) == .moved(velocity: throwVelocity([Sample(t: 0, p: Pt(110, 120)), Sample(t: 0.04, p: Pt(113, 120)), Sample(t: 0.06, p: Pt(150, 130))], now: 0.07)) && !d.isHeld, "dragger: release gives the velocity of the last 0.1 s and goes back to rest")
        d.press(at: Pt(5, 5), origin: Pt(0, 0), now: 1)
        check(d.release(now: 1.1) == .click && d.release(now: 1.2) == .notHeld, "dragger: a press without a drag is a click; a second release is nothing")
        d.fly(Body(p: Pt(300, 300), v: Pt(0, 0)), in: bookBounds(vis), dizzy: true)
        check(d.isFlying, "dragger: flying")
        var last: Dragger.FlightTick?; var steps = 0
        while d.isFlying && steps < 3000 { last = d.step(dt: 1.0 / 60); steps += 1 }
        check(last?.landed == true && last?.dizzy == true && last?.body.p.y == 0 && !d.isFlying, "dragger: a fall lands, reports the dizzy flag and goes back to rest")
        check(d.step(dt: 0.016) == nil, "dragger: no step when not flying")
        d.fly(Body(p: Pt(300, 300), v: Pt(0, 0)), in: bookBounds(vis), dizzy: false)
        d.press(at: Pt(1, 1), origin: Pt(0, 0), now: 2)
        check(d.isHeld && !d.isFlying, "dragger: a press catches a flying body")
    }
    do { // the stored place (B10, L14)
        let st = MemStore(); st.setNumber("bookX", 700); st.setNumber("bookY", 20)
        let bp = BookPlace(store: st)
        check(!bp.on && bp.pos == nil, "book place: nothing on screen before the first feed")
        let w0 = st.writes
        bp.firstFeed(sprite: Pt(1000, 0), screens: [vis])
        check(bp.on && bp.pos == Pt(700, 20) && st.writes == w0, "book place: the first feed puts it at the stored place, no write")
        for i in 0..<100 { bp.move(to: Pt(Double(i), Double(i))) }
        check(st.writes == w0 && bp.pos == Pt(99, 99), "book place: dragging never writes")
        bp.land(at: Pt(5000, 5000), screens: [vis])
        check(bp.pos == Pt(1380, 791) && st.writes == w0 + 2 && st.number("bookX") == 1380 && st.number("bookY") == 791, "book place: a landing is clamped and written once (two numbers)")
        check(Set(st.d.keys) == ["bookX", "bookY"], "book place: the store holds bookX and bookY and nothing else")
        let again = BookPlace(store: st)
        again.firstFeed(sprite: Pt(1000, 0), screens: [vis])
        check(again.pos == Pt(1380, 791), "book place: the next run finds it where it landed")
        let fresh = MemStore(), fp = BookPlace(store: fresh)
        fp.firstFeed(sprite: Pt(1000, 0), screens: [vis])
        check(fp.pos == Pt(1110, 0) && fresh.writes == 0, "book place: with nothing stored it spawns on his right and writes nothing")
        fp.firstFeed(sprite: Pt(200, 0), screens: [vis])
        check(fp.pos == Pt(1110, 0), "book place: the spawn is used once")
        let far = MemStore(); far.setNumber("bookX", 9000); far.setNumber("bookY", -40)
        let farp = BookPlace(store: far); farp.firstFeed(sprite: Pt(0, 0), screens: [vis])
        check(farp.pos == Pt(1380, 0), "book place: a stored place off screen is clamped")
        let w1 = st.writes
        check(again.screensChanged([Rc(0, 0, 800, 600)]) && again.pos == Pt(740, 516) && st.writes == w1 + 2, "book place: a display change that hides it moves it and counts as one landing write")
        check(!again.screensChanged([Rc(0, 0, 800, 600)]) && st.writes == w1 + 2, "book place: and nothing more when it is already inside")
    }
    // the picture
    let iconOpt = BookIcon(dir: assets)
    check(iconOpt != nil, "book-icon.json loads from the assets folder")
    if let icon = iconOpt {
        func palette(_ s: String) -> [Character: RGBA] { bookPalette(bridge.palette(s)) }
        let calm = palette("contento")
        check(icon.w == 20 && icon.h == 28 && icon.ground == 26, "book icon: 20 x 28 art px, shadow row 26")
        func opaqueRows(_ g: Grid) -> (lo: Int, hi: Int) {
            var lo = 99, hi = -1
            for y in 0..<g.h { for x in 0..<g.w { if let c = g[x, y], c.a == 255 { lo = min(lo, y); hi = max(hi, y) } } }
            return (lo, hi)
        }
        for look in [BookLook.closed, .marked, .hover, .open] {
            let g = icon.grid(look: look, ribbon: look == .marked, pal: calm)
            check(g.w == 20 && g.h == 28, "book \(look): a 20 x 28 grid")
            let want = look == .hover ? 24 : 25
            check(opaqueRows(g).hi == want, "book \(look): the lowest opaque row is \(want) (base 25 minus the lift)")
        }
        let closed = icon.grid(look: .closed, ribbon: false, pal: calm), marked = icon.grid(look: .marked, ribbon: true, pal: calm), open = icon.grid(look: .open, ribbon: false, pal: calm)
        check(closed[13, 26] != nil && (closed[13, 26]?.a ?? 255) < 128 && closed[3, 26] != nil, "book closed: a soft shadow on row 26, 14 wide (7 each side of the middle)")
        check(closed[2, 26] == nil && closed[17, 26] == nil, "book closed: and nothing beyond it")
        let hov = icon.grid(look: .hover, ribbon: false, pal: calm)
        check(hov[16, 26] == nil && hov[4, 26] != nil, "book hover: the shadow shrinks to 6 each side")
        check(open[1, 26] != nil && open[18, 26] != nil && open[0, 26] == nil, "book open: the shadow widens to 9 each side")
        check(closed[13, 10] == nil && marked[13, 10] != nil && marked[14, 11] != nil && opaqueRows(marked).lo == 10, "book marked: the ribbon sits above the top edge (rows 10 and 11)")
        check(icon.grid(look: .open, ribbon: true, pal: calm) == open, "book open: no ribbon, whatever the flag says")
        check(hov[17, 10] != nil && closed[17, 10] == nil, "book hover: the spark")
        check(closed[5, 12] == calm["o"] && closed[5, 13] == calm["C"], "book: the outline is the state's outline, the cover its cheek colour")
        let tired = palette("dormido")
        check(icon.grid(look: .closed, ribbon: false, pal: tired)[6, 13] == closed[6, 13] && closed[6, 13] == calm["c"], "book: the brown cover does not change with the state")
        check(calm["k"] == parseColor("#bd7f55") && tired["k"] == calm["k"] && calm["C"] == parseColor("#a8683f") && calm["o"] == parseColor("#5f3b27") && calm["l"] == parseColor("#f0c862") && calm["W"] == parseColor("#fff3d6"), "book: brown cover #a8683f, border #5f3b27, label #f0c862, pages #fff3d6 in every state")
        for s in states {
            let p = bookPalette(bridge.palette(s))
            for k in "cCWgGrRyolLsk" { check(p[k] != nil, "book palette \(s): key \(k)") }
        }
        let mask = HitMask(grid: closed)
        check(mask.hit(x: 10 * 3 + 1.5, yTop: 18 * 3 + 1.5, flipped: false), "book hit mask: the cover hits")
        check(mask.hit(x: 3 * 3 + 1.5, yTop: 15 * 3 + 1.5, flipped: false) && !mask.hit(x: 2 * 3 + 1.5, yTop: 15 * 3 + 1.5, flipped: false) && !mask.hit(x: 1.5, yTop: 1.5, flipped: false), "book hit mask: 1 art px of margin, then click-through")
        check(!HitMask(grid: closed, margin: 0).hit(x: 10 * 3 + 1.5, yTop: 26 * 3 + 1.5, flipped: false), "book hit mask: the shadow never hits on its own")
        let omask = HitMask(grid: open)
        check(omask.hit(x: 1 * 3 + 1.5, yTop: 20 * 3 + 1.5, flipped: false) && !mask.hit(x: 1 * 3 + 1.5, yTop: 20 * 3 + 1.5, flipped: false), "book hit mask: rebuilt per state (the open book is 18 wide)")
        for (name, rows) in icon.panel { check(rows.count == 8 && rows.allSatisfy { $0.count == 8 }, "panel icon \(name): 8 x 8") }
        check(Set(icon.panel.keys) == ["doing", "next", "done", "later"] && icon.barW == 62 && icon.barH == 5, "panel art: four icons and the 62 x 5 bar")
    }
    do { // the shim on the real sprites.js (B7, B13)
        let ctx = bridge.ctx
        let g = PetGlue(bridge: bridge)
        g.setData(state: "contento", h5: 0, stale: 3)
        var pts = 0, bad = 0, withBook = 0
        for _ in 0..<450 {
            g.push(); g.tick()
            guard let f = bridge.frameInfo() else { bad += 1; continue }
            if f.pointing { pts += 1 }
            if ctx.evaluateScript("__S().ex.some(function(e){return e[0]==='book'})")?.toBool() == true { withBook += 1 }
            if f.grid.w != 34 || f.grid.h != 28 { bad += 1 }
        }
        check(bad == 0, "shim: __frame decodes as {g, pt} with a 34 x 28 grid")
        check(pts >= 105 && pts <= 120, "shim: pt is true only inside the pointing window, 40 of 150 ticks (\(pts) of 450)")
        check(withBook > 0, "shim: without bookOn the small book is still drawn in the window (\(withBook))")
        g.bookOn = true
        var pts2 = 0, withBook2 = 0
        for _ in 0..<450 {
            g.push(); g.tick()
            if bridge.frameInfo()?.pointing == true { pts2 += 1 }
            if ctx.evaluateScript("__S().ex.some(function(e){return e[0]==='book'})")?.toBool() == true { withBook2 += 1 }
        }
        check(pts2 >= 105 && pts2 <= 120 && withBook2 == 0, "shim: with bookOn the pose stays and the small book goes (\(pts2), \(withBook2))")
        let hgOn = bridge.hitGrid()!
        g.bookOn = false; g.push()
        check(hgOn == bridge.hitGrid()!, "shim: the hit grid never held extras, with or without bookOn")
        g.panelOpen = true; g.bookOn = true; g.push()
        var pts3 = 0
        for _ in 0..<300 { g.push(); g.tick(); if bridge.frameInfo()?.pointing == true { pts3 += 1 } }
        check(pts3 == 0, "shim: with the panel open the sprite engine gets stale 0 and does not point")
        g.panelOpen = false; g.push()
        let calm0 = PetGlue(bridge: bridge); calm0.setData(state: "contento", h5: 0, stale: 0)
        var pts4 = 0
        for _ in 0..<300 { calm0.push(); calm0.tick(); if bridge.frameInfo()?.pointing == true { pts4 += 1 } }
        check(pts4 == 0, "shim: no stale item, no pointing")
        let sl = PetGlue(bridge: bridge); sl.setData(state: "dormido", h5: 0, stale: 3)
        var pts5 = 0
        for _ in 0..<300 { sl.push(); sl.tick(); if bridge.frameInfo()?.pointing == true { pts5 += 1 } }
        check(pts5 == 0, "shim: asleep he does not point")
        let hd = PetGlue(bridge: bridge); hd.setData(state: "contento", h5: 0, stale: 3); hd.setHeld(true)
        var pts6 = 0
        for _ in 0..<300 { hd.push(); hd.tick(); if bridge.frameInfo()?.pointing == true { pts6 += 1 } }
        check(pts6 == 0, "shim: held he does not point")
        let ac = PetGlue(bridge: bridge); ac.setData(state: "contento", h5: 0, stale: 3)
        var pts7 = 0
        for _ in 0..<300 { bridge.act("poke"); ac.push(); ac.tick(); if bridge.frameInfo()?.pointing == true { pts7 += 1 } }
        check(pts7 == 0, "shim: acting he does not point")
        for s in states {
            let p = bridge.palette(s)
            check(["c", "C", "l", "L", "s", "o"].allSatisfy { p[$0] != nil }, "shim: __pal(\(s)) has c C l L s o")
        }
        check(bridge.palette("raro") == bridge.palette("contento"), "shim: an invented state gets contento's palette")
        check(PetBridge.shim.components(separatedBy: "spec()").count - 1 == 1, "shim: spec() is called once, in one helper that every consumer uses")
        check(!PetBridge.shim.contains("__p.frame("), "shim: it never copies or calls the pet's frame()")
    }

    // the Libreta: where it opens, what it lists, the bar
    do { // libretaRect (L1, L12, L13)
        let c = Rc(200, 100, 900, 700)
        check(libretaRect(claude: Rc(100, 100, 800, 600), visible: vis, bookCentre: nil) == Rc(912, 100, 380, 600), "libretaRect: 12 pt right of Claude, as tall as Claude")
        check(libretaRect(claude: c, visible: vis, bookCentre: nil) == Rc(720, 100, 380, 700), "libretaRect: no room on the right, it sits inside Claude's right edge")
        check(libretaRect(claude: Rc(0, -50, 400, 1200), visible: vis, bookCentre: nil) == Rc(412, 0, 380, 875), "libretaRect: never taller than the visible frame, never below it")
        check(libretaRect(claude: Rc(100, 700, 400, 600), visible: vis, bookCentre: nil).maxY == 875, "libretaRect: kept under the top of the visible frame")
        check(libretaRect(claude: Rc(-300, 100, 500, 600), visible: vis, bookCentre: nil).x == 212, "libretaRect: a window half off the left edge still puts it beside the window")
        check(libretaRect(claude: Rc(1200, 100, 400, 600), visible: vis, bookCentre: nil) == Rc(1060, 100, 380, 600), "libretaRect: Claude half past the right edge: the panel stays inside the visible frame")
        check(libretaRect(claude: Rc(-300, 100, 200, 600), visible: vis, bookCentre: nil).x >= 0, "libretaRect: Claude past the left edge: still inside")
        check(libretaRect(claude: nil, visible: vis, bookCentre: Pt(100, 300)) == Rc(12, 0, 380, 875), "libretaRect: no Claude, book on the left half: 12 pt in from the left edge, full height")
        check(libretaRect(claude: nil, visible: vis, bookCentre: Pt(1000, 300)) == Rc(1440 - 380 - 12, 0, 380, 875), "libretaRect: no Claude, book on the right half: 12 pt in from the right edge")
        check(libretaRect(claude: nil, visible: vis, bookCentre: nil).x == 1440 - 380 - 12, "libretaRect: nothing to go by: the right edge")
        check(libretaOpaque(Pt(190, 350), size: Pt(380, 700)) && !libretaOpaque(Pt(0, 0), size: Pt(380, 700)) && !libretaOpaque(Pt(3, 3), size: Pt(380, 700)) && libretaOpaque(Pt(5, 5), size: Pt(380, 700)) && libretaOpaque(Pt(190, 0), size: Pt(380, 700)), "libretaOpaque: the rounded rectangle, transparent corners")
        check(!libretaOpaque(Pt(-1, 5), size: Pt(380, 700)) && !libretaOpaque(Pt(380, 5), size: Pt(380, 700)) && !libretaOpaque(Pt(5, 700), size: Pt(380, 700)) && !libretaOpaque(Pt(379.5, 699.5), size: Pt(380, 700)), "libretaOpaque: outside and the far corner click through")
    }
    do { // barFill (L4)
        check(barFill(done: 5, total: 20) == 15 && barFill(done: 0, total: 20) == 0 && barFill(done: 20, total: 20) == 60 && barFill(done: 30, total: 20) == 60, "barFill: round(60 * done / total), never more than 60")
        check(barFill(done: 1, total: 7) == 9 && barFill(done: 1, total: 0) == 0 && barFill(done: -3, total: 5) == 0, "barFill: rounds, and a zero total or a negative count is an empty bar")
    }
    do { // titles
        check(capTitle("Rehacer el panel") == "Rehacer el panel" && capTitle(String(repeating: "a", count: 46)).count == 46, "capTitle: up to 46 characters is left alone")
        let long = capTitle("Una tarea con un nombre larguísimo que no cabe en una sola línea del panel")
        check(long.count <= 46 && long.hasSuffix("…") && !long.dropLast().hasSuffix(" "), "capTitle: cut at a word with … (\(long))")
        check(capTitle(String(repeating: "x", count: 80)).count == 46, "capTitle: no space to cut at: 45 characters and …")
        check(autoTitle("rehacer el panel (con notas y más)") == "Rehacer el panel" && autoTitle("Panel: lo que sea. Y más") == "Panel" && autoTitle("hola") == "Hola", "autoTitle: the first separator ends it, capitalised")
        check(autoTitle("a: lo que importa de verdad aquí") == "Lo que importa de verdad aquí", "autoTitle: a head under 3 characters takes the text after the colon")
    }
    do { // libretaModel (L2, L3, L5, L6, L14, L16)
        func it(_ id: String, _ title: String?, state: String = "active") -> Dict {
            var d: Dict = ["id": id, "text": "texto de " + id + " (largo)", "state": state]
            if let t = title { d["title"] = t }
            return d
        }
        let feed: Dict = ["version": 2, "t": es, "total": 20, "done_total": 5, "stale": ["R-3"], "idle": ["R-4"],
                          "counts": ["doing": 3, "next": 11, "later": 4, "done": 5],
                          "doing": [it("R-2", "Dos"), it("R-3", "Tres"), it("R-4", "Cuatro", state: "idle")],
                          "queue_recent": (0..<8).map { it("R-\(30 - $0)", "Próxima \($0)") }, "done_recent": [it("R-9", "Nueve"), it("R-8", "Ocho"), it("R-7", "Siete")],
                          "queue": [it("R-1", "solo v1")]]
        let m = libretaModel(feed: feed, old: false)
        check(m.title == "Libreta" && m.sections.map { $0.key } == ["doing", "next", "done"], "model: Libreta, then En curso, Por empezar, Hecho")
        check(m.sections[0].heading == "En curso" && m.sections[1].heading == "Por empezar" && m.sections[2].heading == "Hecho", "model: the headings come from the catalog")
        check(m.sections[0].rows.map { $0.id } == ["R-3", "R-2", "R-4"], "model: the stale row comes first, the rest keep their order")
        check(m.sections[0].rows.map { $0.stale } == [true, false, false] && m.sections[0].rows.map { $0.paused } == [false, false, true], "model: stale and paused flags")
        check(m.sections[0].rows[0].caption == "estancada · más de 7 días" && m.sections[0].rows[2].caption == "en pausa" && m.sections[0].rows[1].caption == nil, "model: the stale caption and the paused caption")
        check(m.sections[0].count == 3 && m.sections[1].count == 11 && m.sections[2].count == 5, "model: counts per section")
        check(m.sections[1].rows.count == 8 && m.sections[1].rows[0].id == "R-30" && m.sections[1].rows[7].id == "R-23", "model: Por empezar lists the 8 newest first")
        check(m.sections[1].more == "y 3 más, de nuevas a antiguas" && m.sections[2].more == "y 2 más" && m.sections[0].more == nil, "model: the 'and N more' lines")
        check(m.sections[2].rows.map { $0.id } == ["R-9", "R-8", "R-7"], "model: Hecho lists the 3 most recent")
        check(m.progress?.fill == 15 && m.progress?.caption == "5 de 20 hechas · 25 %", "model: the progress bar and its caption")
        check(m.head == "Esta lleva más de 7 días sin moverse. ¿La miramos?" && m.old == nil, "model: one stale row in the header's voice, no 'old' line")
        check(m.parked == "4 ideas aparcadas", "model: parked ideas as a count")
        var two = feed; two["stale"] = ["R-3", "R-2"]
        check(libretaModel(feed: two, old: false).head == "Llevan más de 7 días sin moverse. ¿Las miramos?", "model: stale, plural")
        var calm = feed; calm["stale"] = []
        check(libretaModel(feed: calm, old: false).head == "Hay 3 tareas en marcha.", "model: no stale rows: how many are in progress")
        var one = calm; one["counts"] = ["doing": 1, "next": 11, "later": 1, "done": 5]; one["doing"] = [it("R-2", "Dos")]
        check(libretaModel(feed: one, old: false).head == "Hay una tarea en marcha." && libretaModel(feed: one, old: false).parked == "1 idea aparcada", "model: singular forms")
        var quiet = calm; quiet["counts"] = ["doing": 0, "next": 7, "later": 0, "done": 5]; quiet["doing"] = []
        let qm = libretaModel(feed: quiet, old: false)
        check(qm.head == "Todo quieto. Tienes 7 por empezar." && qm.parked == nil && qm.sections[0].rows.isEmpty && qm.sections[0].empty == "Nada en marcha.", "model: nothing in progress: the quiet header, the empty line, no parked line")
        var none = feed; none["done_total"] = 0; none["done_recent"] = []; none["counts"] = ["doing": 3, "next": 11, "later": 4, "done": 0]
        let nm = libretaModel(feed: none, old: false)
        check(nm.progress?.fill == 0 && nm.progress?.caption == "0 de 20 hechas · 0 %", "model: tasks but none done: an empty bar and '0 de 20 hechas · 0 %' (only a zero total hides the bar)")
        let empty = libretaModel(feed: ["version": 2, "t": es, "total": 0, "done_total": 0, "counts": ["doing": 0, "next": 0, "later": 0, "done": 0], "doing": [], "queue_recent": [], "done_recent": []], old: false)
        check(empty.progress?.fill == nil && empty.progress?.caption == "Todavía nada hecho." && empty.head == "Todo quieto. Nada por empezar.", "model: nothing at all: no bar, 'Todavía nada hecho.'")
        check(empty.sections.map { $0.empty ?? "" } == ["Nada en marcha.", "Nada por empezar.", "Todavía nada hecho."], "model: every empty section says so")
        check(libretaModel(feed: feed, old: true).old == "Datos de hace un rato", "model: the dim line when the last call failed")
        var longTitle = feed; longTitle["doing"] = [it("R-2", String(repeating: "palabra ", count: 12))]
        let lt = libretaModel(feed: longTitle, old: false).sections[0].rows[0].title
        check(lt.count <= 46 && lt.hasSuffix("…"), "model: a title over 46 characters is cut with … (a defence; the source rejects it)")
        let v1: Dict = ["t": es, "counts": ["doing": 1, "next": 14, "later": 2, "done": 70], "doing": [it("R-2", nil)], "queue": (0..<12).map { it("R-\($0 + 1)", nil) }, "stale": [], "idle": []]
        let m1 = libretaModel(feed: v1, old: false)
        check(m1.sections.map { $0.key } == ["doing", "next"] && m1.progress == nil, "model: a version 1 feed has no Hecho and no bar")
        check(m1.sections[0].rows[0].title == "Texto de R-2" && m1.sections[1].rows.count == 12 && m1.sections[1].more == "y 2 más", "model: version 1 rows use the automatic title and the oldest-first queue")
        check(m1.head == "Hay una tarea en marcha." && m1.parked == "2 ideas aparcadas", "model: a version 1 feed still gets its header and parked count")
        let oldCatalog: Dict = ["book.now": "Ahora", "book.queue": "En cola", "book.nothing": "nada", "book.more": "y {n} más"]
        let m0 = libretaModel(feed: ["counts": ["doing": 0, "next": 1], "doing": [], "queue": [it("R-1", nil)], "t": oldCatalog], old: false)
        check(m0.sections[0].heading == "Ahora" && m0.sections[1].heading == "En cola" && m0.head == "", "model: an old plugin's catalog still gives headings, and no header line rather than a wrong one")
        check(libretaModel(feed: ["counts": [:], "doing": [], "queue": []], old: false).sections.count == 2, "model: a feed with no words at all does not crash")
        for k in m.sections.flatMap({ $0.rows }).map({ $0.title }) { check(!k.unicodeScalars.contains { $0.value >= 0x1F300 && $0.value <= 0x1FAFF }, "model: no emoji in \(k)") }
    }
    if let icon = iconOpt {
        let p = bookPalette(bridge.palette("contento"))
        check(["o", "P", "l", "L"].allSatisfy { p[Character($0)] != nil }, "bar: the palette has o, P, l and L")
        let b0 = icon.barGrid(fill: 0, pal: p), b30 = icon.barGrid(fill: 30, pal: p), b60 = icon.barGrid(fill: 60, pal: p)
        check(b0.w == 62 && b0.h == 5, "bar: 62 x 5 art px")
        check(b0[0, 0] == p["o"] && b0[61, 4] == p["o"] && b0[0, 2] == p["o"] && b0[61, 2] == p["o"] && b0[30, 0] == p["o"] && b0[30, 4] == p["o"], "bar: the outline on rows 0 and 4 and columns 0 and 61")
        check(b0[1, 1] == p["P"] && b0[1, 3] == p["P"] && b0[60, 2] == p["P"], "bar: empty is the P key")
        check(b30[30, 1] == p["l"] && b30[30, 2] == p["l"] && b30[30, 3] == p["L"] && b30[31, 1] == p["P"] && b30[1, 1] == p["l"], "bar: filled up to the count, l on rows 1-2 and L on row 3")
        check(b60[60, 1] == p["l"] && b60[61, 1] == p["o"], "bar: full is 60 art px, the outline stays")
        for (name, rows) in icon.panel.sorted(by: { $0.key < $1.key }) { check(icon.iconGrid(name, pal: p)?.w == 8 && rows.count == 8, "panel icon \(name) as a grid") }
        check(icon.iconGrid("nope", pal: p) == nil, "panel icon: an unknown name is nil")
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

/// The book's window content: the picture at 3 pt per art px and the mouse, handed to the controller.
final class BookView: NSView {
    let imageLayer = CALayer()
    weak var owner: Brote?
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        imageLayer.frame = CGRect(x: 0, y: 0, width: K.bookW, height: K.bookH)
        imageLayer.magnificationFilter = .nearest; imageLayer.minificationFilter = .nearest; imageLayer.contentsGravity = .resize
        layer?.addSublayer(imageLayer)
    }
    required init?(coder: NSCoder) { fatalError("not used") }
    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { owner?.bookPressed() }
    override func mouseDragged(with event: NSEvent) { owner?.bookDragged() }
    override func mouseUp(with event: NSEvent) { owner?.bookReleased() }
    override func rightMouseDown(with event: NSEvent) { owner?.showMenu(event, in: self) }
    func setImage(_ img: CGImage?, dropped: Bool) {
        CATransaction.begin(); CATransaction.setDisableActions(true)
        imageLayer.contents = img
        imageLayer.frame.origin.y = dropped ? -K.scale : 0         // a press drops it 1 art px for 100 ms
        CATransaction.commit()
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

/// The Libreta's page: laid out from the model and drawn straight into the view (no subviews). Colours are semantic, so it follows the system appearance.
final class LibretaContent: NSView {
    enum Item { case text(NSAttributedString, CGRect), image(NSImage, CGRect), fill(NSColor, CGRect) }
    var items: [Item] = []
    override var isFlipped: Bool { true }
    override var isOpaque: Bool { false }

    private func str(_ s: String, _ size: CGFloat, _ weight: NSFont.Weight = .regular, _ color: NSColor, mono: Bool = false, right: Bool = false, wrap: Bool = false) -> NSAttributedString {
        let p = NSMutableParagraphStyle()
        p.lineBreakMode = wrap ? .byWordWrapping : .byTruncatingTail
        if right { p.alignment = .right }
        let font = mono ? NSFont.monospacedSystemFont(ofSize: size, weight: weight) : NSFont.systemFont(ofSize: size, weight: weight)
        return NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color, .paragraphStyle: p])
    }
    private func height(_ a: NSAttributedString, width: CGFloat) -> CGFloat {
        ceil(a.boundingRect(with: NSSize(width: width, height: 10_000), options: [.usesLineFragmentOrigin]).height)
    }

    /// Lays the page out for `width` and returns its height.
    func build(model m: LibretaModel, portrait: CGImage?, bar: CGImage?, icons: [String: CGImage], width W: CGFloat) -> CGFloat {
        items = []
        let pad: CGFloat = 20, px = CGFloat(K.scale)
        func img(_ c: CGImage?, _ r: CGRect) { if let c = c { items.append(.image(NSImage(cgImage: c, size: NSSize(width: c.width, height: c.height)), r)) } }
        var y = pad
        // the header: his portrait, the title, what the notebook has to say
        let pw = 34 * px, ph = 28 * px, tx = pad + pw + 14
        img(portrait, CGRect(x: pad, y: y, width: pw, height: ph))
        items.append(.text(str(m.title, 20, .semibold, .labelColor), CGRect(x: tx, y: y + 2, width: W - tx - pad, height: 26)))
        var hy = y + 32
        if !m.head.isEmpty {
            let a = str(m.head, 13, .regular, .secondaryLabelColor, wrap: true), h = height(a, width: W - tx - pad)
            items.append(.text(a, CGRect(x: tx, y: hy, width: W - tx - pad, height: h))); hy += h + 2
        }
        if let o = m.old { items.append(.text(str(o, 11, .regular, .tertiaryLabelColor), CGRect(x: tx, y: hy, width: W - tx - pad, height: 15))); hy += 15 }
        y = max(y + ph, hy) + 18
        // progress
        if let pr = m.progress {
            if pr.fill != nil, let bar = bar { img(bar, CGRect(x: pad, y: y, width: CGFloat(bar.width) * px, height: CGFloat(bar.height) * px)); y += CGFloat(bar.height) * px + 6 }
            items.append(.text(str(pr.caption, 12, .regular, .secondaryLabelColor), CGRect(x: pad, y: y, width: W - 2 * pad, height: 16)))
            y += 16 + 22
        }
        for s in m.sections {
            img(icons[s.key], CGRect(x: pad, y: y, width: 8 * px, height: 8 * px))
            items.append(.text(str(s.heading, 12, .semibold, .labelColor), CGRect(x: pad + 8 * px + 8, y: y + 4, width: W - 2 * pad - 8 * px - 8 - 46, height: 16)))
            items.append(.text(str(String(s.count), 12, .regular, .secondaryLabelColor, right: true), CGRect(x: W - pad - 40, y: y + 4, width: 40, height: 16)))
            y += 8 * px + 6
            for r in s.rows {
                let rh: CGFloat = r.caption == nil ? 26 : 42
                if r.stale { items.append(.fill(.systemOrange, CGRect(x: pad - 8, y: y + 2, width: 3, height: rh - 6))) }
                items.append(.text(str(r.id, 11, .regular, .tertiaryLabelColor, mono: true), CGRect(x: pad, y: y + 3, width: 46, height: 16)))
                items.append(.text(str(r.title, 13, .regular, .labelColor), CGRect(x: pad + 48, y: y + 1, width: W - 2 * pad - 48, height: 18)))
                if let c = r.caption { items.append(.text(str(c, 11, .regular, r.stale ? .systemOrange : .secondaryLabelColor), CGRect(x: pad + 48, y: y + 21, width: W - 2 * pad - 48, height: 15))) }
                y += rh
            }
            if let e = s.empty { items.append(.text(str(e, 12, .regular, .tertiaryLabelColor), CGRect(x: pad + 48, y: y, width: W - 2 * pad - 48, height: 16))); y += 22 }
            if let mo = s.more { items.append(.text(str(mo, 11, .regular, .tertiaryLabelColor), CGRect(x: pad + 48, y: y, width: W - 2 * pad - 48, height: 15))); y += 21 }
            y += 12
        }
        if let pk = m.parked {
            img(icons["later"], CGRect(x: pad, y: y, width: 8 * px, height: 8 * px))
            items.append(.text(str(pk, 12, .regular, .secondaryLabelColor), CGRect(x: pad + 8 * px + 8, y: y + 4, width: W - 2 * pad - 8 * px - 8, height: 16)))
            y += 8 * px + 8
        }
        frame.size = NSSize(width: W, height: y + pad - 8)
        setAccessibilityLabel(m.title)
        needsDisplay = true
        return y + pad - 8
    }

    override func draw(_ dirtyRect: NSRect) {
        NSGraphicsContext.current?.imageInterpolation = .none
        for it in items {
            switch it {
            case .text(let a, let r): if r.intersects(dirtyRect) { a.draw(with: r, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine]) }
            case .image(let i, let r): if r.intersects(dirtyRect) { i.draw(in: r, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: [.interpolation: NSImageInterpolation.none.rawValue]) }
            case .fill(let c, let r): c.setFill(); NSBezierPath(roundedRect: r, xRadius: 1.5, yRadius: 1.5).fill()
            }
        }
    }
}

/// The rounded page the panel shows: the system's window colour, a hairline edge, and the content under a scroll view that never takes the key.
final class LibretaFrame: NSView {
    let scroll = NSScrollView()
    let content = LibretaContent(frame: .zero)
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = K.libretaRadius; layer?.masksToBounds = true; layer?.borderWidth = 0.5
        scroll.frame = bounds; scroll.autoresizingMask = [.width, .height]
        scroll.drawsBackground = false; scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.scrollerStyle = .overlay
        scroll.borderType = .noBorder; scroll.documentView = content
        addSubview(scroll)
    }
    required init?(coder: NSCoder) { fatalError("not used") }
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
            layer?.borderColor = NSColor.separatorColor.cgColor
        }
    }
    override func viewDidChangeEffectiveAppearance() { needsDisplay = true; content.needsDisplay = true }
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

final class Brote: NSObject {
    let store = DefaultsStore()
    let probe: Probe
    let glue: PetGlue
    let layout: Layout
    let panel = HostPanel(size: NSSize(width: K.winW, height: K.winH))
    let view: SpriteView
    var hubURL: URL?
    let dataOn: Bool
    let eventsOn: Bool                                   // read the plugin's event file (--no-events turns it off)
    var eventsOffset: UInt64 = 0
    var lastFront: Bool?, lastReduce: Bool?
    static let eventsPath = NSHomeDirectory() + "/Library/Application Support/BroteWindow/events.log"
    var dragger = Dragger()
    var stroll = Stroll()
    var pressedDown = false
    var mask: HitMask?
    var cachedGrid: Grid?, cachedFlip = false, cachedKey: String?, cachedPointing = false
    // the book: a second body with his physics, shown once a feed has been read in this run
    let bookPanel = HostPanel(size: NSSize(width: K.bookW, height: K.bookH))
    let bookView: BookView
    let place: BookPlace
    var icon: BookIcon?
    var bookDrag = Dragger()
    var bookMask: HitMask?
    var bookKey: String?, bookPal: [Character: RGBA] = [:], bookPalState = ""
    var bookHover = false, bookPressedDown = false, bookPressOnMask = false, bookStart = Pt(0, 0), bookDropped = false
    var bookFlightTimer: Timer?, lastBookFlight = 0.0, lastBookOrigin: NSPoint?
    var sideLeft: Bool?
    // the Libreta: a panel made on the first press, below him and the book
    var libretaPanel: HostPanel?, libretaFrameView: LibretaFrame?
    var libretaVisible = false, libretaClosing = false, libretaAskedAt: Double?, libretaTarget: Rc?
    var libretaModelShown: LibretaModel?, libretaPalKey = "", feedOld = false, feedFresh = false, panelTouched = false
    var status: Dict?
    var statusBusy = false, docsProbed = false
    var hitTimer: Timer?, flightTimer: Timer?
    var lastFlight = 0.0, lastOrigin: NSPoint?
    var cpuMark = (wall: uptime(), cpu: 0.0), flightSeconds = 0.0, cpuFirst = true, touched = false, threadMark: [ThreadCPU] = []

    init(args: [String], policyAccepted: Bool) {
        probe = Probe(path: NSHomeDirectory() + "/Library/Logs/BroteWindow/probe.log")
        probe.reset()
        dataOn = !args.contains("--no-data")
        eventsOn = !args.contains("--no-events")
        let (bridge, why) = PetBridge.load(dir: Bundle.main.resourcePath ?? ".")
        guard let b = bridge else { fputs("brote: " + why + "\n", stderr); probe.plain("start failed: " + why); exit(1) }
        glue = PetGlue(bridge: b)
        let screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        layout = Layout(store: store, screens: screens, claude: nil)
        view = SpriteView(frame: NSRect(x: 0, y: 0, width: K.winW, height: K.winH))
        bookView = BookView(frame: NSRect(x: 0, y: 0, width: K.bookW, height: K.bookH))
        place = BookPlace(store: store)
        icon = BookIcon(dir: Bundle.main.resourcePath ?? ".")
        super.init()
        view.owner = self
        panel.contentView = view
        bookView.owner = self
        bookPanel.contentView = bookView
        bookPanel.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)      // above him; the Libreta panel sits below both
        if icon == nil { probe.plain("book-icon.json missing or unreadable: no book") }

        // hub: named on the command line, only with the data line on (stage 1 never touches ~/Documents), and only if the script is mine and not writable by others
        if dataOn {
            if let i = args.firstIndex(of: "--hub"), i + 1 < args.count {
                let h = URL(fileURLWithPath: args[i + 1]).resolvingSymlinksInPath()
                if hubTrusted(h.path) { hubURL = h } else { probe.plain("hub refused (plugin/bin/budgit, plugin/bin, plugin/scripts or a .py there is missing, not mine, or writable by others): " + h.path) }
            } else { probe.plain("no --hub: the data line stays off") }
        }

        probe.log("P4", [("policyAccepted", String(policyAccepted)), ("activationPolicy", String(NSApp.activationPolicy().rawValue))])
        probe.log("START", [("src", buildStamp(try? String(contentsOfFile: (Bundle.main.resourcePath ?? ".") + "/build.txt", encoding: .utf8))), ("dataOn", String(dataOn)), ("hub", hubURL?.path ?? "none"), ("pid", String(getpid()))])
        probeStartup(b)
        claudePoll()
        if dataOn { loadSaved() }
        rebuildFrame(key: nil)
        positionWindows()
        panel.orderFrontRegardless()
        probe.log("P1", [("opaque", String(panel.isOpaque)), ("bgAlpha", String(Double(panel.backgroundColor?.alphaComponent ?? -1))), ("shadow", String(panel.hasShadow)),
                         ("level", String(panel.level.rawValue)), ("panels", String(visiblePanels())), ("ignoring", String(panel.ignoresMouseEvents))])
        installMonitors()
        if let i = args.firstIndex(of: "--ambient"), i + 1 < args.count { glue.bridge.setAmbient(args[i + 1]) }
        startEvents()
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
        if dragger.isRest {
            let r = layout.strollRange()
            var x = layout.pos.x
            stroll.tick(state: glue.bridge.state, busy: glue.busy || pressedDown, x: &x, home: r.home, lo: r.lo, hi: r.hi, rng: { Double.random(in: 0..<1) })
            if x != layout.pos.x { layout.pos.x = x; positionWindows() }
            glue.walking = stroll.walking
        } else { glue.walking = false }
        if place.on, let bp = place.pos { sideLeft = bookOnLeft(bookOn: true, prevLeft: sideLeft, bookCx: bp.x + K.bookW / 2, broteCx: layout.pos.x + K.spriteW / 2) }
        var key: String?
        timed(3) { key = glue.step() }
        rebuildFrame(key: key)
        refreshBook()
    }

    /// He mirrors toward a book on his left while he points; otherwise the walking-facing rule.
    func currentFlip(pointing: Bool) -> Bool {
        if glue.bridge.calling, let c = layout.claude { return c.x + c.w / 2 > layout.pos.x + K.spriteW / 2 }     // a call faces the Claude window
        return faceFlip(pointing: pointing && place.on, bookOnLeft: place.on && sideLeft == true, facingLeft: stroll.facingLeft, walking: stroll.walking)
    }

    func rebuildFrame(key: String?) {
        var flip = currentFlip(pointing: cachedPointing)
        if frameUnchanged(key: key, cachedKey: cachedKey, flip: flip, cachedFlip: cachedFlip) { return }
        var info: (grid: Grid, pointing: Bool)?
        timed(3) { info = glue.bridge.frameInfo() }
        guard let (g, pointing) = info else { return }
        cachedKey = key; cachedPointing = pointing
        flip = currentFlip(pointing: pointing)
        if g == cachedGrid && flip == cachedFlip { return }
        cachedGrid = g; cachedFlip = flip
        var img: CGImage?
        timed(5) { img = makeImage(g, flipped: flip) }
        timed(6) { view.setImage(img) }
        timed(4) { if let hg = glue.bridge.hitGrid() { mask = HitMask(grid: hg) } }
    }

    func positionWindows() {
        let o = NSPoint(x: layout.pos.x - K.margin, y: layout.pos.y - K.margin)
        if lastOrigin != o { timed(7) { panel.setFrameOrigin(o) }; lastOrigin = o }
    }

    // MARK: the book
    var bookRect: Rc? { place.pos.map { Rc($0.x, $0.y, K.bookW, K.bookH) } }
    var feedStale: Int { (status?["stale"] as? [Any])?.count ?? 0 }
    func visiblePanels() -> Int { 1 + (bookPanel.isVisible ? 1 : 0) + (libretaPanel?.isVisible == true ? 1 : 0) }

    func positionBook() {
        guard let p = place.pos else { return }
        let o = NSPoint(x: p.x, y: p.y)
        if lastBookOrigin != o { bookPanel.setFrameOrigin(o); lastBookOrigin = o }
    }

    /// The first feed of this run puts the book on screen (stored place, else on his right) and tells the sprite its own small book is no longer needed.
    func showBookIfNeeded() {
        guard icon != nil, !place.on else { return }
        place.firstFeed(sprite: layout.pos, screens: layout.screens)
        glue.bookOn = true
        refreshBook()
        positionBook()
        bookPanel.orderFrontRegardless()
        probe.log("BOOK", [("kind", "shown"), ("x", String(Int(place.pos?.x ?? -1))), ("y", String(Int(place.pos?.y ?? -1)))], cap: 200)
        probe.log("P1", [("level", String(bookPanel.level.rawValue)), ("panels", String(visiblePanels())), ("ignoring", String(bookPanel.ignoresMouseEvents))])
    }

    /// Draws the book when its look changed (state, stale ribbon, hover, press drop, his palette); a key keeps the 10 Hz tick cheap.
    func refreshBook() {
        guard let icon = icon, place.on else { return }
        let look = bookLook(open: libretaVisible, hover: bookHover, held: bookDrag.isHeld, stale: feedStale)
        let ribbon = bookRibbon(look: look, stale: feedStale)
        let key = "\(look.rawValue)|\(ribbon)|\(glue.state)|\(bookDropped)"
        if key == bookKey { return }
        if bookPalState != glue.state { bookPal = bookPalette(glue.bridge.palette(glue.state)); bookPalState = glue.state }
        let g = icon.grid(look: look, ribbon: ribbon, pal: bookPal)
        bookKey = key
        bookMask = HitMask(grid: g)
        bookView.setImage(makeImage(g, flipped: false), dropped: bookDropped)
    }

    func bookPressed() {
        touched = true
        guard let bp = place.pos else { return }
        let m = mousePt()
        bookPressOnMask = bookMask?.hit(x: m.x - bp.x, yTop: K.bookH - (m.y - bp.y), flipped: false) ?? false
        stopBookFlight()
        bookDrag.press(at: m, origin: bp, now: uptime())
        bookStart = m
        bookPressedDown = true
        bookPanel.ignoresMouseEvents = false
        refreshBook()
    }

    func bookDragged() {
        switch bookDrag.drag(to: mousePt(), now: uptime()) {
        case .idle: return
        case .started(let p), .moving(let p): place.move(to: bookClamp(p, screens: layout.screens))
        }
        positionBook()
    }

    func bookReleased() {
        bookPressedDown = false
        let up = mousePt()
        libretaAskedAt = uptime()
        switch bookDrag.release(now: uptime()) {
        case .notHeld: break
        case .click:
            if isBookPress(from: bookStart, to: up, overBook: bookPressOnMask) { bookPress() }
        case .moved(let v):
            guard let p = place.pos else { break }
            let b = bookBounds(visibleFrame(for: p))
            let r = bookRelease(speed: v.speed, pos: p, bounds: b)
            switch r.action {
            case .stay:
                place.land(at: r.pos, screens: layout.screens); positionBook()
                probe.log("BOOK", [("kind", "drop"), ("speed", String(Int(v.speed))), ("x", String(Int(r.pos.x))), ("y", String(Int(r.pos.y)))], cap: 200)
            case .fly:
                bookDrag.fly(Body(p: r.pos, v: v), in: b, dizzy: false)
                probe.log("BOOK", [("kind", "throw"), ("speed", String(Int(v.speed)))], cap: 200)
                startBookFlight()
            }
        }
        refreshBook()
    }

    func visibleFrame(for book: Pt) -> Rc {
        let c = Pt(book.x + K.bookW / 2, book.y + K.bookH / 2)
        return layout.screens.isEmpty ? Rc(0, 0, 1440, 900) : layout.screens[screenIndex(for: c, in: layout.screens)]
    }

    /// A press on the book: it drops 1 art px for 100 ms (only the fade stays with "reduce motion"), then the Libreta toggles.
    func bookPress() {
        if !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            bookDropped = true; refreshBook()
            Timer.scheduledTimer(withTimeInterval: 0.1, repeats: false) { [unowned self] _ in self.bookDropped = false; self.refreshBook() }
        }
        probe.log("BOOK", [("kind", "press")], cap: 200)
        toggleLibreta()
    }
    // MARK: the Libreta panel (below him and the book, above Claude)
    func libretaScreen() -> Rc {
        let at = layout.claude?.center ?? place.pos.map { Pt($0.x + K.bookW / 2, $0.y + K.bookH / 2) } ?? layout.pos
        return layout.screens.isEmpty ? Rc(0, 0, 1440, 900) : layout.screens[screenIndex(for: at, in: layout.screens)]
    }
    func libretaFrame() -> Rc {
        libretaRect(claude: layout.claude, visible: libretaScreen(), bookCentre: place.pos.map { Pt($0.x + K.bookW / 2, $0.y + K.bookH / 2) })
    }
    func libretaIcons() -> [String: CGImage] {
        guard let icon = icon else { return [:] }
        var out: [String: CGImage] = [:]
        for k in icon.panel.keys { if let g = icon.iconGrid(k, pal: bookPal), let i = makeImage(g, flipped: false) { out[k] = i } }
        return out
    }
    /// Builds the page from the last feed. Returns false when nothing changed (same words, same pose, same colours).
    @discardableResult func fillLibreta(force: Bool) -> Bool {
        guard let st = status, let frame = libretaFrameView else { return false }
        let model = libretaModel(feed: st, old: feedOld)
        let key = glue.state
        if !force, model == libretaModelShown, key == libretaPalKey { return false }
        if bookPalState != glue.state { bookPal = bookPalette(glue.bridge.palette(glue.state)); bookPalState = glue.state }
        let top = frame.scroll.contentView.bounds.origin.y
        let bar = icon.flatMap { ic -> CGImage? in
            guard case let f? = model.progress?.fill else { return nil }
            return makeImage(ic.barGrid(fill: f, pal: bookPal), flipped: false)
        }
        let portrait = glue.bridge.hitGrid().flatMap { makeImage($0, flipped: false) }
        let h = frame.content.build(model: model, portrait: portrait, bar: bar, icons: libretaIcons(), width: CGFloat(K.libretaW))
        frame.content.frame.size.height = max(h, frame.scroll.bounds.height)
        frame.scroll.contentView.scroll(to: NSPoint(x: 0, y: min(top, max(0, h - frame.scroll.bounds.height))))
        libretaModelShown = model; libretaPalKey = key
        return true
    }
    func ensureLibretaPanel() -> HostPanel {
        if let p = libretaPanel { return p }
        let p = HostPanel(size: NSSize(width: K.libretaW, height: 400))
        p.level = NSWindow.Level(rawValue: 2)                       // above Claude's windows, below him (3) and the book (4)
        let v = LibretaFrame(frame: NSRect(x: 0, y: 0, width: K.libretaW, height: 400))
        p.contentView = v
        libretaPanel = p; libretaFrameView = v
        return p
    }
    var reduceMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
    func toggleLibreta() { libretaVisible ? closeLibreta() : openLibreta() }

    func openLibreta() {
        guard status != nil, icon != nil else { return }
        let created = libretaPanel == nil
        let p = ensureLibretaPanel()
        let r = libretaFrame()
        let end = NSRect(x: r.x, y: r.y, width: r.w, height: r.h)
        libretaTarget = r
        libretaVisible = true; libretaClosing = false
        glue.panelOpen = true; glue.push(); refreshBook()
        let slide: CGFloat = reduceMotion ? 0 : 14 * ((layout.claude.map { $0.center.x < r.x + r.w / 2 } ?? true) ? -1 : 1)
        p.setFrame(end.offsetBy(dx: slide, dy: 0), display: false)      // size first: the page is laid out against the scroll view's height
        fillLibreta(force: true)
        p.alphaValue = 0
        p.orderFrontRegardless()
        if let up = libretaAskedAt { probe.log("P17", [("ms", String(Int((uptime() - up) * 1000)))], cap: 60) }
        if created { probe.log("P1", [("level", String(p.level.rawValue)), ("panels", String(visiblePanels())), ("ignoring", String(p.ignoresMouseEvents))]) }
        probe.log("BOOK", [("kind", "libreta"), ("open", "true")], cap: 200)
        NSAnimationContext.runAnimationGroup { c in
            c.duration = reduceMotion ? 0.12 : 0.2
            c.timingFunction = CAMediaTimingFunction(controlPoints: 0.23, 1, 0.32, 1)
            p.animator().alphaValue = 1
            if slide != 0 { p.animator().setFrame(end, display: true) }
        }
    }

    func closeLibreta() {
        guard libretaVisible, let p = libretaPanel else { return }
        libretaVisible = false; libretaClosing = true
        glue.panelOpen = false; glue.push(); refreshBook()
        probe.log("BOOK", [("kind", "libreta"), ("open", "false")], cap: 200)
        NSAnimationContext.runAnimationGroup({ c in
            c.duration = reduceMotion ? 0.1 : 0.14
            c.timingFunction = CAMediaTimingFunction(name: .easeOut)
            p.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self = self, self.libretaClosing else { return }
            p.orderOut(nil); self.libretaClosing = false
        })
    }

    /// Claude moved or a display changed: the open panel follows without a slide.
    func repositionLibreta() {
        guard libretaVisible, let p = libretaPanel else { return }
        // with no Claude window the place was computed at open: the book moving does not move the panel, only a lost display does
        if layout.claude == nil, layout.screens.contains(where: { $0.contains(Pt(Double(p.frame.midX), Double(p.frame.midY))) }) { return }
        let r = libretaFrame()
        guard r != libretaTarget else { return }                  // against the last target, not the frame: an open slide in flight is not cut
        libretaTarget = r
        p.setFrame(NSRect(x: r.x, y: r.y, width: r.w, height: r.h), display: true); fillLibreta(force: true)
    }

    /// The panel's own click-through: only its rounded rectangle takes the pointer.
    func libretaOverPointer(_ m: Pt) -> Bool {
        guard let p = libretaPanel, p.isVisible else { return false }
        let f = p.frame
        return libretaOpaque(Pt(m.x - Double(f.origin.x), m.y - Double(f.origin.y)), size: Pt(Double(f.width), Double(f.height)))
    }

    func startBookFlight() {
        lastBookFlight = uptime()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [unowned self] _ in self.bookFlightStep() }
        RunLoop.main.add(t, forMode: .common); bookFlightTimer = t
    }
    func stopBookFlight() { bookFlightTimer?.invalidate(); bookFlightTimer = nil }
    func bookFlightStep() {
        let now = uptime(), dt = min(now - lastBookFlight, 1.0 / 30)
        lastBookFlight = now; flightSeconds += dt
        guard let t = bookDrag.step(dt: dt) else { stopBookFlight(); return }
        place.move(to: t.body.p); positionBook()
        if t.landed {
            stopBookFlight()
            place.land(at: t.body.p, screens: layout.screens); positionBook()
            probe.log("BOOK", [("kind", "land"), ("x", String(Int(t.body.p.x))), ("y", String(Int(t.body.p.y)))], cap: 200)
        }
    }

    // MARK: click-through: an adaptive poll of the pointer, never an event tap
    func hitPoll() {
        let m = mousePt(), p = layout.pos
        let overBrote = mask?.hit(x: m.x - p.x, yTop: K.spriteH - (m.y - p.y), flipped: cachedFlip) ?? false
        var overBook = false
        if let bp = place.pos { overBook = bookMask?.hit(x: m.x - bp.x, yTop: K.bookH - (m.y - bp.y), flipped: false) ?? false }
        let target = pressTarget(overBook: overBook, overBrote: overBrote)
        let ignore = ignoreMouse(overBody: target == .brote, pressed: pressedDown, dragging: dragger.isDragging)
        if panel.ignoresMouseEvents != ignore { panel.ignoresMouseEvents = ignore }
        if place.on {
            let bignore = ignoreMouse(overBody: target == .book, pressed: bookPressedDown, dragging: bookDrag.isDragging)
            if bookPanel.ignoresMouseEvents != bignore { bookPanel.ignoresMouseEvents = bignore }
            let hov = target == .book && !bookPressedDown && !bookDrag.isFlying
            if hov != bookHover { bookHover = hov; refreshBook() }
        }
        if let lp = libretaPanel, lp.isVisible {
            let want = !libretaOverPointer(m)
            if lp.ignoresMouseEvents != want { lp.ignoresMouseEvents = want }
        }
        if dragger.isRest, !pressedDown { glue.setHover(target == .brote) }
        var near = Rc(p.x, p.y, K.spriteW, K.spriteH).inflated(40).contains(m)
        if let br = bookRect, br.inflated(40).contains(m) { near = true }               // the fast poll covers both bodies
        if let lp = libretaPanel, lp.isVisible, lp.frame.insetBy(dx: -20, dy: -20).contains(NSPoint(x: m.x, y: m.y)) { near = true; panelTouched = true }       // and the open panel
        if near { touched = true }
        scheduleHit(near || pressedDown || bookPressedDown || dragger.isFlying || bookDrag.isFlying || NSEvent.pressedMouseButtons != 0 ? 1.0 / 60 : 1.0 / 20)
    }

    // MARK: the mouse
    func pressed() {
        touched = true
        stopFlight()
        dragger.press(at: mousePt(), origin: layout.pos, now: uptime())
        pressedDown = true
        panel.ignoresMouseEvents = false
    }

    func dragged() {
        switch dragger.drag(to: mousePt(), now: uptime()) {
        case .idle: return
        case .started(let p): glue.setHeld(true); stroll.target = nil; stroll.walking = false; layout.pos = layout.clamped(p)
        case .moving(let p): layout.pos = layout.clamped(p)
        }
        positionWindows()
    }

    func released() {
        pressedDown = false
        let rel = dragger.release(now: uptime())
        guard rel != .notHeld else { return }
        glue.setHeld(false)
        guard case .moved(let v) = rel else {
            let m = mousePt(), away = m.x < layout.pos.x + K.spriteW / 2 ? 1 : -1       // agotado runs away from the pointer
            let c = glue.click(now: uptime(), away: away)
            probe.log("CLICK", [("result", String(describing: c))], cap: 200)
            return
        }
        if isThrow(speed: v.speed) {
            let bounds = layout.beginThrow(from: layout.pos)
            dragger.fly(Body(p: layout.pos, v: v), in: bounds, dizzy: dizzyAfter(speed: v.speed))
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
        let now = uptime(), dt = min(now - lastFlight, 1.0 / 30)
        lastFlight = now; flightSeconds += dt
        guard let t = dragger.step(dt: dt) else { stopFlight(); return }
        layout.pos = t.body.p
        positionWindows()
        if t.landed {
            stopFlight()
            glue.bridge.dizzyNext(t.dizzy); glue.bridge.act("land")
            probe.log("LAND", [("x", String(Int(layout.pos.x))), ("y", String(Int(layout.pos.y))), ("dizzy", String(t.dizzy))], cap: 200)
        }
    }

    func showMenu(_ event: NSEvent, in v: NSView) {
        let menu = NSMenu()
        let item = NSMenuItem(title: menuTitle(prefs: Locale.preferredLanguages, table: loadMenuTable(dir: Bundle.main.resourcePath ?? ".")), action: #selector(quit), keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        NSMenu.popUpContextMenu(menu, with: event, for: v)
    }
    @objc func quit() { probe.plain("quit from the menu"); try? FileManager.default.removeItem(atPath: Brote.eventsPath); NSApp.terminate(nil) }

    // MARK: what Claude is doing: the plugin's hooks append one short line per event to a file that only this app creates
    func startEvents() {
        guard eventsOn else { return }
        let dir = (Brote.eventsPath as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        FileManager.default.createFile(atPath: Brote.eventsPath, contents: Data(), attributes: [.posixPermissions: 0o600])     // empty: no backlog
        eventsOffset = 0
        probe.plain("events: " + Brote.eventsPath)
        // killed from outside (pkill, a closed Terminal tab): the file goes too, or the hooks would keep appending to it. DispatchSource
        // handlers run on the main queue like any other code here, so nothing unsafe happens inside a raw signal handler.
        for sig in [SIGTERM, SIGHUP, SIGINT] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in self?.probe.plain("signal \(sig): cleaning up"); try? FileManager.default.removeItem(atPath: Brote.eventsPath); NSApp.terminate(nil) }
            src.resume()
            signalSources.append(src)
        }
    }
    var signalSources: [DispatchSourceSignal] = []
    func pollEvents() {
        if eventsOn, let r = eventsRead(path: Brote.eventsPath, offset: eventsOffset) {
            eventsOffset = r.offset
            if !r.text.isEmpty { glue.bridge.feed(r.text) }
        }
        let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == K.claudeBundle      // free: no permission
        if front != lastFront { lastFront = front; glue.bridge.setFront(front) }
        let rm = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        if rm != lastReduce { lastReduce = rm; glue.bridge.setReduceMotion(rm) }
    }

    // MARK: Claude's window
    func claudePoll() {
        pollEvents()
        let pids = Set(NSRunningApplication.runningApplications(withBundleIdentifier: K.claudeBundle).map { Int($0.processIdentifier) })
        let primary = Double(NSScreen.screens.first?.frame.height ?? 0)
        let c = pickClaude(windowList(), pids: pids, primaryHeight: primary)
        layout.screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        if bookDrag.isRest, place.screensChanged(layout.screens) { positionBook() }         // a display went away: the book is back on screen
        defer { repositionLibreta() }
        guard c != layout.claude else { return }
        let before = layout.pos, old = layout.claude
        if dragger.isRest { layout.claudeChanged(to: c) } else { layout.claude = c }
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
        refreshBook()
        if libretaVisible { fillLibreta(force: false) }
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
                    self.feedFresh = true; self.feedOld = false
                    self.apply(merged)
                    self.showBookIfNeeded()
                } else if self.feedFresh {
                    self.feedOld = true
                    if self.libretaVisible { self.fillLibreta(force: false) }
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
        let nowThreads = threadCPU()
        let state = cpuFirst ? "startup" : flightSeconds > 0.5 ? "flight" : panelTouched || libretaVisible ? "panel" : touched ? "touched" : "idle"
        let other = pct - (cpuBuckets[0] + cpuBuckets[1] + cpuBuckets[2]) / span * 100      // everything the three timers do not account for: the run loop, the data child, the window server
        probe.log("P7", [("state", state), ("cpu", String(format: "%.2f", pct))] + cpuParts(cpuBuckets, span: span) + [("book", place.on ? "on" : "off"), ("other", String(format: "%.2f", other)), ("threads", threadShares(before: threadMark, after: nowThreads, span: span, mainID: mainThreadID, top: 4)), ("rssMB", String(Int(ru.ru_maxrss) / 1_048_576))], cap: 120)
        cpuMark = (wall, cpu); threadMark = nowThreads; flightSeconds = 0; cpuFirst = false; touched = false; panelTouched = false; cpuBuckets = [Double](repeating: 0, count: cpuBucketNames.count)
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
if cli.contains("--bench") {                                  // GUI-free cost of one tick's work, in CPU microseconds
    var assets = Bundle.main.resourcePath ?? "."
    if let i = cli.firstIndex(of: "--assets"), i + 1 < cli.count { assets = cli[i + 1] }
    runBench(assets: assets, pacedSeconds: cli.firstIndex(of: "--paced").flatMap { i in i + 1 < cli.count ? Double(cli[i + 1]) : nil })
}
#if CORE_ONLY
fputs("brote core build: only --selftest and --bench are available\n", stderr)
exit(2)
#else
launchApp()
#endif
