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
import ImageIO
import IOKit.ps
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
    static let pokeGap = 1.0, pokesForTickle = 4, pokesForGrumpy = 10        // quick clicks: a streak while each is under 1 s after the last
    static let strollMin = 60.0, strollRest = (30, 80)       // a walk is 60 pt at least; then he stands 3-8 s
    // walking on windows: what counts as a window to stand on, and how he gets up there (climb, ladder or fly by height)
    static let surfMinW = 120.0, surfMinH = 60.0, surfMinAlpha = 0.05
    static let platMin = 54.0                               // the drawn body is 18 art px wide: a narrower ledge cannot hold him
    static let hopUp = 40.0, climbMax = 126.0, ladderMax = 360.0, flyFar = 500.0   // climbMax = 1.5 x his height
    static let longHover = 5.0                              // the pointer resting on him this long: he looks up at it, curious
    static let glideFrom = 300.0                             // a drop at least this high, in a good mood, is a glide
    static let landSnap = 6.0                               // at the end of a trip, a top this close to his feet still catches him (a new window settles by a point)
    static let walkSpeed = 40.0, climbSpeed = 60.0, ladderSpeed = 70.0, flySpeed = 220.0
    static let fallPause = 0.5                              // he notices there is nothing under him before he drops
    static let hug = 20.0                                   // how far into the window's side he holds on while climbing
    static let tripEvery = 90.0, tripAway = 60.0            // one trip at most every 90 s, back to Claude after 60 s
    static let statusTimeout = 5.0, statusEvery = 30.0
    static let hitMargin = 1                                // art px
    static let bookW = 60.0, bookH = 84.0                   // 20 x 28 art px, the same height as him
    static let bookGap = 8.0, bookRoom = 68.0, sideDeadband = 24.0
    static let bodyL = 24.0, bodyR = 78.0, bookArtL = 12.0, bookArtR = 51.0   // opaque art: his columns 8 to 25, the closed book's 4 to 16 (pt from each panel's left)
    static let bookMinGap = 9.0, bookDodge = 24.0, scootStep = 12.0          // art never closer than 9 pt; he moves aside from 24 pt, 120 pt/s
    static let bookVisit = 0.35                                              // share of his walks that end beside the book (on its floor)
    static let libretaW = 380.0, libretaGap = 12.0, libretaRadius = 14.0, titleCap = 46
    static let logCap = 200_000
    static let bundleID = "es.budgit.brote.float"
    static let claudeBundle = "com.anthropic.claudefordesktop"
    static let chatOpensByLink = true                       // the hand test showed `open "$(budgit chat-link <prefix>)"` lands on that chat; false would only bring Claude to the front
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
    var glyphs: [[Int]] = []                                // boxes [x, y, w, h] that hold a sign (the "?" of a call): never mirrored
    subscript(x: Int, y: Int) -> RGBA? { cells[y * w + x] }
    /// The picture facing the other way. A sign moves with its bubble but keeps reading the right way round.
    func mirrored() -> Grid {
        var out = cells
        for y in 0..<h { for x in 0..<w { out[y * w + x] = cells[y * w + (w - 1 - x)] } }
        for b in glyphs where b.count == 4 {
            let (bx, by, bw, bh) = (b[0], b[1], b[2], b[3]), mx = w - bx - bw
            for y in max(0, by)..<max(max(0, by), min(h, by + bh)) {
                for j in 0..<max(0, bw) where bx + j >= 0 && bx + j < w && mx + j >= 0 && mx + j < w { out[y * w + mx + j] = cells[y * w + bx + j] }
            }
        }
        var m = Grid(w: w, h: h, cells: out); m.glyphs = glyphs.map { $0.count == 4 ? [w - $0[0] - $0[2], $0[1], $0[2], $0[3]] : $0 }
        return m
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

/// The same step with window tops as floors: the floor under him is the highest top below his feet that holds his centre, so he lands on a
/// window from above, passes a top on the way up and drops when he slides off its end. `hit`: this step touched a floor while going down.
/// `bump`: this step hit anything (a floor going down, a screen side or the top), where the dizzy stars start.
func stepFlyOver(_ b: inout Body, dt: Double, in bd: Bounds, plats: [Plat]) -> (flight: Flight, hit: Bool, bump: Bool) {
    var floor = bd
    if let p = supportUnder(cx: b.p.x + K.spriteW / 2, feet: b.p.y, plats: plats), p.y > bd.floorY { floor.floorY = p.y }
    let falling = b.v.y < -60 || (b.v.y < 0 && b.p.y > floor.floorY + 0.001)
    let v0 = b.v
    let f = stepFly(&b, dt: dt, in: floor)
    let hit = falling && b.p.y == floor.floorY
    let side = v0.x != 0 && (b.p.x == bd.minX || b.p.x == bd.maxX) && (b.v.x == 0 || (b.v.x > 0) != (v0.x > 0))
    let top = v0.y > 0 && b.p.y == bd.ceilY
    return (f, hit, hit || side || top)
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

enum Click { case poke, countedOnly, tickle, tickling, grumpy }

struct PokeCounter {
    var streak = 0, last = -Double.infinity
    /// Own timestamps: `NSEvent.clickCount` is the double-click counter, not this. A click less than 1 s after the last one adds to the streak:
    /// the fourth tickles him, the next ones keep him laughing, the tenth makes him grumpy and starts the count over.
    mutating func click(now: Double, dormido: Bool) -> Click {
        streak = now - last < K.pokeGap ? streak + 1 : 1
        last = now
        if streak >= K.pokesForGrumpy { streak = 0; return .grumpy }
        if dormido { return .countedOnly }
        return streak == K.pokesForTickle ? .tickle : streak > K.pokesForTickle ? .tickling : .poke
    }
}

struct Stroll {
    var target: Double? = nil
    var walking = false
    var facingLeft = false
    var rest = K.strollRest.0                       // ticks he stands still before the next walk
    static func lively(_ state: String) -> Bool { state == "radiante" || state == "contento" }
    /// How far he walks per 10 fps tick (faster radiante, slower cansado or agotado); nil: enfermo and dormido stay put.
    static func pace(_ state: String) -> Double? {
        switch state { case "radiante": return 4.5; case "contento", "justo": return 3; case "cansado", "agotado": return 1.5; default: return nil }
    }
    /// One 10 fps tick: he walks to a spot anywhere on `lo...hi` (the window top he stands on, or the floor), stands a few seconds, and goes on.
    /// `busy` (a gesture, a clip, the calm after one, hover) pauses the walk; it goes on afterwards.
    /// `visit`: a spot he likes (beside the book); now and then a new walk goes there.
    mutating func tick(state: String, busy: Bool, x: inout Double, lo: Double, hi: Double, rng: () -> Double, visit: Double? = nil) {
        guard let step = Stroll.pace(state) else { walking = false; target = nil; return }
        if busy { walking = false; return }
        guard let t = target else {
            walking = false
            if rest > 0 { rest -= 1; return }
            guard hi - lo >= K.strollMin else { return }
            if let v = visit, v >= lo, v <= hi, abs(v - x) > 1, rng() < K.bookVisit { target = v; return }
            let to = lo + rng() * (hi - lo)
            target = abs(to - x) >= K.strollMin ? to : (x - lo > hi - x ? lo : hi)      // too short a walk: to the farther end instead
            return
        }
        let d = t - x
        if abs(d) <= step {
            x = t; target = nil; walking = false; facingLeft = false
            rest = K.strollRest.0 + Int(rng() * Double(K.strollRest.1 - K.strollRest.0))
        } else { x += d > 0 ? step : -step; walking = true; facingLeft = d < 0 }
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

struct WinInfo { var pid: Int; var layer: Int; var bounds: Rc; var id = 0; var alpha = 1.0 }       // bounds as the window list gives them: origin top-left

func flipY(_ r: Rc, primaryHeight: Double) -> Rc { Rc(r.x, primaryHeight - (r.y + r.h), r.w, r.h) }

/// Claude's chat window: the largest layer-0 window of its processes. Needs no permission (bounds, pid and layer are not protected).
func pickClaude(_ list: [WinInfo], pids: Set<Int>, primaryHeight: Double) -> Rc? { pickClaudeWin(list, pids: pids, primaryHeight: primaryHeight)?.r }
func pickClaudeWin(_ list: [WinInfo], pids: Set<Int>, primaryHeight: Double) -> (id: Int, r: Rc)? {
    let ws = list.filter { pids.contains($0.pid) && $0.layer == 0 && $0.bounds.w >= 100 && $0.bounds.h >= 100 }
    guard let best = ws.max(by: { $0.bounds.area < $1.bounds.area }) else { return nil }
    return (best.id, flipY(best.bounds, primaryHeight: primaryHeight))
}

// MARK: - walking on windows: window tops he can stand on, falls, and the way up

/// A window he may stand on, flipped to AppKit coordinates. The list keeps the window list's order: front to back.
struct SWin: Equatable { var id: Int; var r: Rc }
/// The open part of a window's top edge. He stands on it while his centre is within x0...x1; `y` is his feet.
struct Plat: Equatable { var win: Int; var x0: Double; var x1: Double; var y: Double }

/// Normal app windows only (layer 0, visible, not tiny, not ours), front to back.
func surfaceWindows(_ list: [WinInfo], ownPID: Int, primaryHeight: Double) -> [SWin] {
    list.filter { $0.layer == 0 && $0.pid != ownPID && $0.alpha > K.surfMinAlpha && $0.bounds.w >= K.surfMinW && $0.bounds.h >= K.surfMinH }
        .map { SWin(id: $0.id, r: flipY($0.bounds, primaryHeight: primaryHeight)) }
}

/// A window that fills a whole display (a full-screen app): window tops make no sense then, he keeps to the floor.
func fullScreenApp(_ wins: [SWin], displays: [Rc]) -> Bool {
    wins.contains { w in displays.contains { d in abs(w.r.x - d.x) < 1 && abs(w.r.y - d.y) < 1 && abs(w.r.w - d.w) < 1 && abs(w.r.h - d.h) < 1 } }
}

/// Does a window in front of `i` hide its top edge at x? (Its body spans the edge line; a window whose top is level with it does not.)
func edgeCovered(_ wins: [SWin], _ i: Int, x: Double) -> Int? {
    let top = wins[i].r.maxY
    return wins[..<i].first { $0.r.minY < top && $0.r.maxY > top && x >= $0.r.minX && x <= $0.r.maxX }?.id
}

/// Every window's top edge minus the stretches that windows in front of it hide, minus ledges too short for him or with no room above.
func exposedTops(_ wins: [SWin], screens: [Rc]) -> [Plat] {
    var out: [Plat] = []
    for (i, w) in wins.enumerated() {
        let top = w.r.maxY
        guard let scr = screens.first(where: { top >= $0.minY && top + K.spriteH <= $0.maxY && w.r.maxX > $0.minX && w.r.minX < $0.maxX }) else { continue }
        var segs = [(max(w.r.minX, scr.minX), min(w.r.maxX, scr.maxX))]
        for f in wins[..<i] where f.r.minY < top && f.r.maxY > top {
            segs = segs.flatMap { s -> [(Double, Double)] in
                if f.r.maxX <= s.0 || f.r.minX >= s.1 { return [s] }
                return [(s.0, f.r.minX), (f.r.maxX, s.1)].filter { $0.1 > $0.0 }
            }
        }
        out += segs.filter { $0.1 - $0.0 >= K.platMin }.map { Plat(win: w.id, x0: $0.0, x1: $0.1, y: top) }
    }
    return out
}

/// The highest window top at or below his feet that holds his centre (nil: the screen floor).
func supportUnder(cx: Double, feet: Double, plats: [Plat]) -> Plat? {
    plats.filter { cx >= $0.x0 && cx <= $0.x1 && $0.y <= feet + 0.5 }.max { $0.y < $1.y }
}

/// Where a trip ends: the target window's top if it still holds his centre within `K.landSnap` of his feet, else the nearest such top (nil: none, he falls).
func landingTop(cx: Double, feet: Double, plats: [Plat], target: Int?) -> Plat? {
    let near = plats.filter { cx >= $0.x0 && cx <= $0.x1 && abs($0.y - feet) <= K.landSnap }
    return near.first { $0.win == target } ?? near.min { abs($0.y - feet) < abs($1.y - feet) }
}

/// What happened to the window he stands on since the last look. `centre` is his centre x after riding along.
enum Support: Equatable { case stay, gone, slid, covered(by: Int) }
func supportCheck(host: Int, centre: Double, wins: [SWin]) -> Support {
    guard let i = wins.firstIndex(where: { $0.id == host }) else { return .gone }
    let r = wins[i].r
    if centre < r.minX || centre > r.maxX { return .slid }
    if let f = edgeCovered(wins, i, x: centre) { return .covered(by: f) }
    return .stay
}

/// A window opened over his spot. Never dice: the same mood and height always give the same answer.
/// radiante gets onto it (a hop if it is close, else he flies), contento hops on only when it is close, the other moods fall.
enum CoverMove: Equatable { case hop, fly, fall }
func coverMove(state: String, feet: Double, coverTop: Double) -> CoverMove {
    let close = coverTop - feet <= K.climbMax
    switch state {
    case "radiante": return close ? .hop : .fly
    case "contento": return close ? .hop : .fall
    default: return .fall
    }
}

/// One stretch of a trip. Climb and ladder go straight up at `x`; hop and fly are arcs.
enum Leg: Equatable { case walk(x: Double), climb(y: Double, left: Bool), ladder(y: Double, left: Bool), hop(to: Pt), fly(to: Pt) }

/// The way from where he stands to a window top. `lo...hi` = where his sprite's x may go on what he stands on now.
/// Higher by up to 40 pt: a hop. Up to 126 pt with the window's side within reach and reaching down to him: he climbs it.
/// Up to 360 pt with a side within reach: a ladder against it. Higher, farther than 500 pt, or no side within reach: he flies.
func planRoute(from p: Pt, lo: Double, hi: Double, to t: Plat, win: Rc, wantX: Double) -> [Leg] {
    let arrive = Pt(clamp(wantX, t.x0 - K.spriteW / 2 + 1, t.x1 - K.spriteW / 2 - 1), t.y)
    let h = t.y - p.y
    if abs(arrive.x - p.x) > K.flyFar { return [.fly(to: arrive)] }
    if h <= K.hopUp {
        let edge = clamp(arrive.x, lo, hi)                                  // walk to the end of his ledge nearest the target, then hop
        return (abs(edge - p.x) > 1 ? [.walk(x: edge)] : []) + [.hop(to: arrive)]
    }
    let left = win.minX - K.spriteW + K.hug, right = win.maxX - K.hug
    let sides = [(left, true), (right, false)].filter { $0.0 >= lo && $0.0 <= hi }
    guard let side = sides.min(by: { abs($0.0 - p.x) < abs($1.0 - p.x) }), h <= K.ladderMax else { return [.fly(to: arrive)] }
    let walk: [Leg] = abs(side.0 - p.x) > 1 ? [.walk(x: side.0)] : []
    let up: Leg = h <= K.climbMax && win.minY <= p.y + 4 ? .climb(y: t.y, left: side.1) : .ladder(y: t.y, left: side.1)
    return walk + [up, .hop(to: arrive)]
}

/// Plays a route at fixed speeds. Each step moves `p` and says which clip goes with it (nil: his own walk or a hop) and where he faces.
struct Route {
    var legs: [Leg]
    private(set) var i = 0
    private var from: Pt?, t = 0.0
    init(_ legs: [Leg]) { self.legs = legs }
    var done: Bool { i >= legs.count }
    var leg: Leg? { done ? nil : legs[i] }
    static func clip(_ l: Leg) -> String? {
        switch l { case .climb: return "sf_climb"; case .ladder: return "sf_ladder"; case .fly: return "sf_fly"; default: return nil }
    }
    /// The stretch of a body clip that repeats while the move lasts (ticks), after its start.
    static func loop(_ clip: String) -> (Int, Int)? {
        ["sf_climb": (0, 40), "sf_ladder": (6, 50), "sf_fly": (6, 30), "sf_ride": (0, 30)][clip]
    }
    /// The arc's length in seconds: hops are quick, flights follow the fly speed.
    static func arcTime(_ a: Pt, _ b: Pt, fly: Bool) -> Double { fly ? max(0.8, a.dist(b) / K.flySpeed) : 0.45 + a.dist(b) / 900 }
    mutating func step(_ p: inout Pt, dt: Double) {
        guard let l = leg else { return }
        let start = from ?? p
        if from == nil { from = p; t = 0 }
        t += dt
        func next() { i += 1; from = nil; t = 0 }
        switch l {
        case .walk(let x):
            let d = x - p.x, s = K.walkSpeed * dt
            if abs(d) <= s { p.x = x; next() } else { p.x += d > 0 ? s : -s }
        case .climb(let y, _), .ladder(let y, _):
            let s = (Route.clip(l) == "sf_climb" ? K.climbSpeed : K.ladderSpeed) * dt
            if y - p.y <= s { p.y = y; next() } else { p.y += s }
        case .hop(let to), .fly(let to):
            let fly: Bool; if case .fly = l { fly = true } else { fly = false }
            let k = min(1, t / Route.arcTime(start, to, fly: fly))
            let lift = fly ? 60.0 : 24.0 + max(0, to.y - start.y) * 0.25
            let top = max(start.y, to.y) + lift
            p = Pt(start.x + (to.x - start.x) * k, (1 - k) * (1 - k) * start.y + 2 * (1 - k) * k * top + k * k * to.y)
            if k >= 1 { p = to; next() }
        }
    }
    /// The target top moved while he is on the way (a window still opening, or dragged): the last leg ends at its new height.
    mutating func retarget(y: Double) {
        guard !done, let l = legs.last else { return }
        let n = legs.count - 1
        switch l {
        case .hop(let to): legs[n] = .hop(to: Pt(to.x, y))
        case .fly(let to): legs[n] = .fly(to: Pt(to.x, y))
        case .climb(_, let left): legs[n] = .climb(y: y, left: left)
        case .ladder(_, let left): legs[n] = .ladder(y: y, left: left)
        case .walk: return
        }
    }
    /// Whether his picture is mirrored: walking, hopping and flying art faces right, so it mirrors going left;
    /// the climb and ladder art has the wall on his left, so it mirrors on a window's left side (the wall is then on his right).
    func mirrored(at p: Pt) -> Bool {
        guard let l = leg else { return false }
        switch l {
        case .walk(let x): return x < p.x
        case .climb(_, let left), .ladder(_, let left): return left
        case .hop(let to), .fly(let to): return to.x < p.x
        }
    }
}

/// Trips only when he is in a good mood, idle and nobody works; one every 90 s at most; back home when work starts or after 60 s.
struct Explorer {
    var lastTrip = -1e9, awaySince: Double?
    func mayLeave(state: String, idle: Bool, working: Bool, now: Double) -> Bool {
        awaySince == nil && Stroll.lively(state) && idle && !working && now - lastTrip >= K.tripEvery
    }
    func shouldReturn(working: Bool, now: Double) -> Bool { awaySince.map { working || now - $0 >= K.tripAway } ?? false }
}
/// Where a trip goes: the widest top of another window that is not Claude's (he lives there already).
func pickTrip(_ plats: [Plat], host: Int?, claude: Int?) -> Plat? {
    plats.filter { $0.win != host && $0.win != claude && $0.x1 - $0.x0 >= K.spriteW }.max { ($0.x1 - $0.x0) < ($1.x1 - $1.x0) }
}

func screenIndex(for p: Pt, in screens: [Rc]) -> Int {
    if let i = screens.firstIndex(where: { $0.contains(p) }) { return i }
    func gap(_ r: Rc) -> Double { hypot(max(r.minX - p.x, 0, p.x - r.maxX), max(r.minY - p.y, 0, p.y - r.maxY)) }
    return screens.indices.min(by: { gap(screens[$0]) < gap(screens[$1]) }) ?? 0
}

struct Anchor: Equatable {
    var relX: Double, perchY: Double                        // relX from the left edge of the window he stands on; perchY = its height (kept for the store)
    func apply(to c: Rc) -> Pt { Pt(c.x + relX, c.maxY) }   // he stands on the top edge, so a resize from the bottom leaves him where he is
    static func span(_ c: Rc) -> Double { max(c.w, K.spriteW) }
}

func spriteCentre(_ p: Pt) -> Pt { Pt(p.x + K.spriteW / 2, p.y + K.spriteH / 2) }

final class Layout {
    /// Where he is. `intended` is where he would be with no screen edge in the way: it keeps following his window while `pos` is clamped, so he comes back with it.
    var pos: Pt { didSet { if !following { intended = pos } } }
    private(set) var intended: Pt
    private var following = false
    var anchor: Anchor?                                     // standing on `host`'s top edge
    var host: SWin?                                         // the window he stands on; nil: the floor, the air, or Claude not seen yet
    var claude: Rc?
    var claudeID: Int?
    var plats: [Plat] = []                                  // the open window tops, from the last look at the window list
    var screens: [Rc]                                       // visible frames, the main screen first
    let store: Store

    init(store: Store, screens: [Rc], claude: Rc?, claudeID: Int? = nil) {
        self.store = store; self.screens = screens; self.claude = claude; self.claudeID = claudeID
        anchor = store.number("offsetX").map { Anchor(relX: $0, perchY: store.number("perchY") ?? 0) }
        let main = screens.first ?? Rc(0, 0, 1440, 900)
        pos = Pt(main.maxX - K.spriteW - 16, main.minY)
        intended = pos
        if let a = anchor, let c = claude { host = SWin(id: claudeID ?? 0, r: c); pos = a.apply(to: c) }
        else if let lx = store.number("lastX"), let ly = store.number("lastY") { pos = Pt(lx, ly) }
        pos = clamped(pos)
        intended = pos
    }

    func visible(for p: Pt) -> Rc { screens.isEmpty ? Rc(0, 0, 1440, 900) : screens[screenIndex(for: spriteCentre(p), in: screens)] }

    /// Always inside the visible frame of its screen; this wins over "moves with his window".
    func clamped(_ p: Pt) -> Pt {
        let v = visible(for: p)
        return Pt(clamp(p.x, v.minX, max(v.minX, v.maxX - K.spriteW)), clamp(p.y, v.minY, max(v.minY, v.maxY - K.spriteH)))
    }

    /// He came to rest at `p`, on a window top (`plat`) or on the floor. Claude's top saves the offset; anywhere else saves the spot.
    func land(at p: Pt, on plat: Plat?, wins: [SWin]) {
        pos = clamped(p)
        if let pl = plat, let w = wins.first(where: { $0.id == pl.win }) {
            anchor = Anchor(relX: pos.x - w.r.x, perchY: w.r.h); host = w
            if claudeID != nil && w.id == claudeID {
                store.setNumber("offsetX", anchor!.relX); store.setNumber("perchY", anchor!.perchY)
                return
            }
        } else { anchor = nil; host = nil }
        store.setNumber("offsetX", nil); store.setNumber("perchY", nil)
        store.setNumber("lastX", pos.x); store.setNumber("lastY", pos.y)
    }

    /// Picked up, thrown, falling or walking off: he stands on nothing (memory only; the next landing writes the store).
    func leave() { anchor = nil; host = nil }

    /// Where a flight may go: the screen. Window tops are floors on the way (`stepFlyOver`).
    func flightBounds() -> Bounds {
        let v = visible(for: pos)
        return Bounds(minX: v.minX, maxX: max(v.minX, v.maxX - K.spriteW), floorY: v.minY, ceilY: v.maxY - K.spriteH)
    }

    /// A throw leaves the saved values alone.
    func beginThrow(from p: Pt) -> Bounds {
        pos = clamped(p)
        leave()
        return flightBounds()
    }

    /// The window he stands on moved or resized: he moves by the same delta of its top-left corner, kept on screen.
    func hostChanged(to new: Rc) {
        guard let h = host, let a = anchor else { return }
        let old = h.r
        host = SWin(id: h.id, r: new)
        following = true; defer { following = false }
        if old.w > 0 { intended = Pt(intended.x + (new.x - old.x), intended.y + (new.maxY - old.maxY)) } else { intended = a.apply(to: new) }
        pos = clamped(intended)
    }

    /// Claude's window, from the window list. A saved offset waiting for Claude snaps him onto it when it appears.
    func claudeChanged(to new: Rc?, id: Int? = nil) {
        claude = new; claudeID = id
        guard let n = new, anchor != nil else { return }
        if host == nil { host = SWin(id: id ?? 0, r: Rc(0, 0, 0, 0)) }
        if let h = host, h.id == (id ?? 0) || h.r.w == 0 { host = SWin(id: id ?? 0, r: h.r); hostChanged(to: n) }
    }

    /// The ledge he may walk on now (his centre stays on it), or the screen floor.
    func ledge() -> (lo: Double, hi: Double) {
        let v = visible(for: pos), lo = v.minX, hi = max(v.minX, v.maxX - K.spriteW)
        guard let h = host, let pl = plats.first(where: { $0.win == h.id && spriteCentre(pos).x >= $0.x0 - 1 && spriteCentre(pos).x <= $0.x1 + 1 }) else { return (lo, hi) }
        return (max(lo, pl.x0 - K.spriteW / 2), max(max(lo, pl.x0 - K.spriteW / 2), min(hi, pl.x1 - K.spriteW / 2)))
    }

}

/// Every sign box reads the right way in the picture as drawn (mirrored when he faces left): a "?" must never read backwards.
/// False when the picture is mirrored and has no sign box at all, so a sign that lost its box shows up.
func glyphsReadRight(_ g: Grid, flipped: Bool) -> Bool {
    guard flipped else { return true }
    guard !g.glyphs.isEmpty else { return false }
    let m = g.mirrored()
    return g.glyphs.allSatisfy { b in
        b.count == 4 && (0..<max(0, b[3])).allSatisfy { dy in (0..<max(0, b[2])).allSatisfy { dx in
            let y = b[1] + dy, x = b[0] + dx, mx = g.w - b[0] - b[2] + dx
            guard y >= 0, y < g.h, x >= 0, x < g.w, mx >= 0, mx < g.w else { return true }
            return m[mx, y] == g[x, y]
        } }
    }
}

/// The hub is named on the command line (`--hub`) and only used when plugin/bin/budgit is a regular file owned by the current user
/// that no other user can write: the app runs that script every 30 s, so it must not be one somebody else could swap.
func hubTrusted(_ hub: String, fm: FileManager = .default) -> Bool { layoutTrusted(PluginLayout.hub(hub), fm: fm) }

// MARK: - one HOME, one instance, live sessions, the plugin root (he lives "with Claude", prebuilt inside the plugin)

/// Where budgit lives: a dev clone (`--hub <clone>`: plugin/bin/budgit, plugin/scripts) or the installed plugin version folder
/// (`--plugin-root <root>`: bin/budgit, scripts, no `plugin/` in the path).
struct PluginLayout: Equatable {
    let root: String, budgit: String, binDir: String, scriptsDir: String
    let isRoot: Bool
    var pycache: String { scriptsDir + "/__pycache__" }
    static func hub(_ h: String) -> PluginLayout {
        PluginLayout(root: h, budgit: h + "/" + K.budgitRel, binDir: h + "/" + K.binRel, scriptsDir: h + "/" + K.scriptsRel, isRoot: false)
    }
    static func pluginRoot(_ r: String) -> PluginLayout {
        PluginLayout(root: r, budgit: r + "/bin/budgit", binDir: r + "/bin", scriptsDir: r + "/scripts", isRoot: true)
    }
}

/// budgit, its bin and scripts folders and every .py in scripts (the code `budgit` really runs) are mine and not group/other-writable; for a plugin root
/// the version folder itself too. attributesOfItem does not follow a link, so a link is never trusted.
func layoutTrusted(_ l: PluginLayout, fm: FileManager = .default) -> Bool {
    func sound(_ path: String, _ kind: FileAttributeType) -> Bool {
        guard let at = try? fm.attributesOfItem(atPath: path), (at[.type] as? FileAttributeType) == kind,
              (at[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), let perm = (at[.posixPermissions] as? NSNumber)?.intValue else { return false }
        return perm & 0o022 == 0
    }
    guard sound(l.budgit, .typeRegular), sound(l.binDir, .typeDirectory), sound(l.scriptsDir, .typeDirectory), !l.isRoot || sound(l.root, .typeDirectory),
          let names = try? fm.contentsOfDirectory(atPath: l.scriptsDir) else { return false }
    return names.filter { $0.hasSuffix(".py") }.allSatisfy { sound(l.scriptsDir + "/" + $0, .typeRegular) }
}

/// The real path (every link resolved), nil when it does not exist.
func realPath(_ p: String) -> String? {
    var buf = [CChar](repeating: 0, count: Int(PATH_MAX) + 1)
    guard realpath(p, &buf) != nil else { return nil }
    return String(cString: buf)
}

/// A small JSON object file of mine (a regular file, not a link, owned by me, at most `limit` bytes), or nil.
func smallJSONObject(_ path: String, limit: Int64 = 1_048_576) -> Dict? {
    let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard fd >= 0 else { return nil }
    var st = stat()
    guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, st.st_uid == getuid(), Int64(st.st_size) <= limit else { close(fd); return nil }
    let d = FileHandle(fileDescriptor: fd, closeOnDealloc: true).readDataToEndOfFile()
    return (try? JSONSerialization.jsonObject(with: d)) as? Dict
}

/// A folder that is mine and that no other user can write (a link is not a folder here).
func soundFolder(_ path: String) -> Bool {
    guard let st = lstatInfo(path) else { return false }
    return (st.st_mode & S_IFMT) == S_IFDIR && st.st_uid == getuid() && st.st_mode & 0o022 == 0
}

/// A "directory" marketplace's live plugin folder: <config>/plugins/known_marketplaces.json has `market` -> {source: {source: "directory", path}, installLocation}
/// with both naming the same real folder, and that folder's .claude-plugin/marketplace.json (same name) lists `name` with a relative source ("./plugin")
/// that resolves exactly to `real`, strictly inside the folder. Claude Code reads such a plugin from there while the registry keeps a stale cache path.
func directoryMarketplaceRoot(_ real: String, name: String, market: String, configDir: String) -> Bool {
    guard let known = smallJSONObject(configDir + "/plugins/known_marketplaces.json"), let m = known[market] as? Dict, let src = m["source"] as? Dict,
          (src["source"] as? String) == "directory", let path = src["path"] as? String, let install = m["installLocation"] as? String,
          install.hasPrefix("/"), path.hasPrefix("/"), let loc = realPath(install), realPath(path) == loc, soundFolder(loc),
          let mj = smallJSONObject(loc + "/.claude-plugin/marketplace.json"), (mj["name"] as? String) == market, let list = mj["plugins"] as? [Any] else { return false }
    for case let e as Dict in list {
        guard (e["name"] as? String) == name, let rel = e["source"] as? String, rel.hasPrefix("./"), !rel.split(separator: "/").contains("..") else { continue }
        if let cand = realPath(loc + "/" + rel), cand == real, cand.hasPrefix(loc + "/") { return true }
    }
    return false
}

/// The folder Claude Code itself registered for this plugin, outside the cache: installed_plugins.json lists it as an installPath (key name budgit or studio),
/// or it is the live folder of a directory marketplace the plugin is installed from and not switched off in settings.json. Never a marketplaces/ copy.
func registeredPluginRoot(_ real: String, configDir: String) -> Bool {
    if let mk = realPath(configDir + "/plugins/marketplaces"), real.hasPrefix(mk + "/") { return false }
    guard let reg = smallJSONObject(configDir + "/plugins/installed_plugins.json"), let plugins = reg["plugins"] as? Dict else { return false }
    for (key, value) in plugins {
        let parts = key.split(separator: "@", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        guard let name = parts.first, name == "budgit" || name == "studio", let entries = value as? [Any] else { continue }
        for case let e as Dict in entries {
            if let ip = e["installPath"] as? String, realPath(ip) == real { return true }
        }
        if !entries.isEmpty, parts.count == 2, directoryMarketplaceRoot(real, name: name, market: parts[1], configDir: configDir) {
            let sp = configDir + "/settings.json"
            let settings = smallJSONObject(sp)
            if settings == nil && lstatInfo(sp) != nil { continue }          // present but unreadable: not guessed to be switched on
            if ((settings?["enabledPlugins"] as? Dict)?[key] as? Bool) != false { return true }
        }
    }
    return false
}

/// `--plugin-root`: the real path must lie strictly under <config>/plugins/cache (the installed copies) or be a folder Claude Code itself registered for this
/// plugin (`registeredPluginRoot`; never marketplaces/ or a folder somebody names), and the layout must pass `layoutTrusted`.
/// Returns the layout built on the real path, or nil.
func trustedPluginRoot(_ root: String, configDir: String, fm: FileManager = .default) -> PluginLayout? {
    guard root.hasPrefix("/"), let real = realPath(root) else { return nil }
    let inCache = (realPath(configDir + "/plugins/cache")).map { real.hasPrefix($0 + "/") } ?? false
    guard inCache || registeredPluginRoot(real, configDir: configDir) else { return nil }
    let l = PluginLayout.pluginRoot(real)
    return layoutTrusted(l, fm: fm) ? l : nil
}
func pluginRootTrusted(_ root: String, configDir: String, fm: FileManager = .default) -> Bool { trustedPluginRoot(root, configDir: configDir, fm: fm) != nil }

/// `--project <dir>` next to `--plugin-root`: an absolute folder that exists and is mine; its real path. Nothing else is ever passed to budgit as a project.
func projectArgument(_ p: String, uid: uid_t = getuid()) -> String? {
    guard p.hasPrefix("/"), let real = realPath(p), let st = lstatInfo(real), (st.st_mode & S_IFMT) == S_IFDIR, st.st_uid == uid else { return nil }
    return real
}

/// The value after `key` on the command line, nil when the key is last or absent.
func argValue(_ args: [String], _ key: String) -> String? {
    guard let i = args.firstIndex(of: key), i + 1 < args.count else { return nil }
    return args[i + 1]
}

func trimSlashes(_ s: String) -> String { var t = s; while t.count > 1 && t.hasSuffix("/") { t.removeLast() }; return t }

/// The home folder from the process environment (the launcher passes HOME): NSHomeDirectory() answers with the account's folder whatever HOME says,
/// so a test with a temp HOME would touch the real files. Only a HOME that is unset, empty, relative or "/" falls back to it.
func brHome(env: [String: String] = ProcessInfo.processInfo.environment) -> String {
    if let h = env["HOME"], h.hasPrefix("/"), trimSlashes(h).count > 1 { return trimSlashes(h) }
    return NSHomeDirectory()
}
/// A CLAUDE_CONFIG_DIR that is set and absolute (what the launcher passes on to budgit and `claude`), else nil.
func brConfigEnv(env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
    guard let c = env["CLAUDE_CONFIG_DIR"], c.hasPrefix("/"), trimSlashes(c).count > 1 else { return nil }
    return trimSlashes(c)
}
func brConfigDir(home: String, env: [String: String] = ProcessInfo.processInfo.environment) -> String { brConfigEnv(env: env) ?? home + "/.claude" }

/// Every file the app owns, hung from one HOME.
struct BroteDirs: Equatable {
    let home: String, support: String, events: String, lock: String, sessions: String, app: String, logs: String, probe: String
    init(home: String) {
        self.home = home
        support = home + "/Library/Application Support/BroteWindow"
        events = support + "/events.log"; lock = support + "/brote.lock"; sessions = support + "/sessions"; app = support + "/Brote.app"
        logs = home + "/Library/Logs/BroteWindow"; probe = logs + "/probe.log"
    }
}

func lstatInfo(_ p: String) -> stat? { var st = stat(); return lstat(p, &st) == 0 ? st : nil }

/// mkdir -p; the last folder gets `leaf`, the missing parents 0755. No link is created and none is followed for the leaf (an existing part is left alone).
func makeDirs(_ path: String, leaf: mode_t) -> Bool {
    let parts = path.split(separator: "/").map(String.init)
    var cur = ""
    for (i, c) in parts.enumerated() {
        cur += "/" + c
        if lstatInfo(cur) != nil { continue }
        if mkdir(cur, i == parts.count - 1 ? leaf : 0o755) != 0 && errno != EEXIST { return false }
    }
    return true
}

enum LockOutcome: Equatable { case held, busy, refused(String), failed(String) }

/// The first thing the app does, before the probe log, events.log or any window: BroteWindow (0700, not a link) and Brote.app (not a link) are checked,
/// brote.lock is opened O_NOFOLLOW 0600, flock(LOCK_EX|LOCK_NB). A second start gets `.busy` and has touched nothing. The descriptor stays open for the life
/// of the process (close-on-exec: a child never keeps the lock alive); the file holds the pid and a newline, for the stop command.
func takeInstanceLock(dirs: BroteDirs) -> (outcome: LockOutcome, fd: Int32) {
    let me = getuid()
    if let st = lstatInfo(dirs.support) {
        if (st.st_mode & S_IFMT) == S_IFLNK { return (.refused("BroteWindow is a symlink"), -1) }
        guard (st.st_mode & S_IFMT) == S_IFDIR else { return (.refused("BroteWindow is not a folder"), -1) }
        guard st.st_uid == me else { return (.refused("BroteWindow is not mine"), -1) }
        if st.st_mode & 0o077 != 0 && chmod(dirs.support, 0o700) != 0 { return (.failed("cannot make BroteWindow private"), -1) }
    } else if !makeDirs(dirs.support, leaf: 0o700) { return (.failed("cannot create BroteWindow"), -1) }
    if let a = lstatInfo(dirs.app), (a.st_mode & S_IFMT) == S_IFLNK { return (.refused("Brote.app is a symlink"), -1) }
    let fd = open(dirs.lock, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600)
    guard fd >= 0 else { return (.failed("cannot open brote.lock"), -1) }
    var st = stat()
    guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, st.st_uid == me else { close(fd); return (.refused("brote.lock is not a plain file of mine"), -1) }
    if st.st_mode & 0o077 != 0 { fchmod(fd, 0o600) }
    if flock(fd, LOCK_EX | LOCK_NB) != 0 {
        let e = errno
        close(fd)
        return e == EWOULDBLOCK ? (.busy, -1) : (.failed("flock failed"), -1)
    }
    _ = ftruncate(fd, 0)
    let line = Array("\(getpid())\n".utf8)
    _ = pwrite(fd, line, line.count, 0)
    return (.held, fd)
}

// the sessions: the plugin writes BroteWindow/sessions/<pid>.json = {"pid", "start", "sid"} for every live Claude session
/// `project`: the optional absolute folder of the Budgit project the session works in (the plugin writes it only when one exists); a bad value is dropped, not the session.
struct SessionEntry: Equatable { var pid: Int32; var start: Int; var sid: String; var project: String? = nil }
/// `mtimes`: the modification time (seconds since the epoch) of each live session's file, by pid.
struct SessionScan { var live: [SessionEntry] = []; var dead: [String] = []; var mtimes: [Int32: Double] = [:] }

/// The project the data command asks about, chosen at every refresh: among live sessions whose project is usable (`projectArgument`: exists, a folder, mine) the one
/// whose file is newest; else the launch argument; else nil (the data line is off). Returns the real path.
func chooseProject(live: [SessionEntry], mtimes: [Int32: Double], launch: String?, valid: (String) -> String? = { projectArgument($0) }) -> String? {
    for e in live.sorted(by: { (mtimes[$0.pid] ?? 0, $0.pid) > (mtimes[$1.pid] ?? 0, $1.pid) }) {
        if let p = e.project, let ok = valid(p) { return ok }
    }
    return launch.flatMap { valid($0) }
}

func processAlive(_ pid: Int32) -> Bool { kill(pid, 0) == 0 || errno == EPERM }
/// The process start time in seconds since the epoch, as the kernel has it (the same number the plugin writes); nil when there is no such process.
func processStart(_ pid: Int32) -> Int? {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
    return Int(info.pbi_start_tvsec)
}

func parseSessionFile(_ d: Data) -> SessionEntry? {
    guard let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict else { return nil }
    func int(_ v: Any?) -> Int? {
        guard let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue == Double(n.int64Value) else { return nil }
        return Int(n.int64Value)
    }
    guard let pid = int(o["pid"]), pid > 1, pid <= Int(Int32.max), let start = int(o["start"]), start > 0 else { return nil }
    var sid = ""
    if let raw = o["sid"] {
        guard let s = raw as? String, s.range(of: "^[A-Za-z0-9_-]{0,12}$", options: .regularExpression) != nil else { return nil }
        sid = s
    }
    var project: String?
    if let p = o["project"] as? String, p.hasPrefix("/"), p.utf8.count <= 1024, !p.utf8.contains(0) { project = p }
    return SessionEntry(pid: Int32(pid), start: start, sid: sid, project: project)
}

/// A session is live iff its file parses, pid > 1, the file is a regular file owned by `uid` (lstat; never a link or a pipe), the folder is a real folder
/// of `uid` that no one else can write, `kill(pid, 0)` says the process is there and its start time is the one written (a reused pid is dead).
/// Dead entries are deleted when `reap` is on; anything else that is not a valid session file (garbage, links, other owners) is ignored and left alone.
func scanSessions(dir: String, uid: uid_t = getuid(), reap: Bool = true, alive: (Int32) -> Bool = processAlive, startOf: (Int32) -> Int? = processStart) -> SessionScan {
    var out = SessionScan()
    guard let d = lstatInfo(dir), (d.st_mode & S_IFMT) == S_IFDIR, d.st_uid == uid, d.st_mode & 0o022 == 0 else { return out }
    for name in ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).sorted() {
        guard name.hasSuffix(".json") else { continue }
        let stem = name.dropLast(5)
        guard !stem.isEmpty, stem.count <= 10, stem.allSatisfy({ $0 >= "0" && $0 <= "9" }), let n = Int(stem), n > 1, n <= Int(Int32.max) else { continue }
        let path = dir + "/" + name
        guard let st = lstatInfo(path), (st.st_mode & S_IFMT) == S_IFREG, st.st_uid == uid, st.st_size <= 4096 else { continue }
        let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { continue }
        var buf = [UInt8](repeating: 0, count: 4097)
        let got = read(fd, &buf, buf.count)
        close(fd)
        guard got > 0, got <= 4096, let e = parseSessionFile(Data(buf.prefix(got))), e.pid == Int32(n) else { continue }
        if alive(e.pid), startOf(e.pid) == e.start { out.live.append(e); out.mtimes[e.pid] = Double(st.st_mtimespec.tv_sec) + Double(st.st_mtimespec.tv_nsec) / 1e9 }
        else { out.dead.append(path); if reap { unlink(path) } }
    }
    return out
}

// when he leaves
enum LifecycleK { static let grace = 120.0, sessionPoll = 5.0, pluginPoll = 60.0 }
enum LifecycleVerdict: Equatable { case stay, quit(String) }

/// Never in the first 120 s after the start. After that: the plugin is gone, or no live session for 120 s in a row (`lastLiveAt` = the last time one was seen,
/// nil = never since the start). `live` is the number of live sessions now. The clock is injected.
func lifecycleDecision(now: Double, lastLiveAt: Double?, startedAt: Double, live: Int, pluginPresent: Bool) -> LifecycleVerdict {
    guard now - startedAt >= LifecycleK.grace else { return .stay }
    if !pluginPresent { return .quit("plugin removed") }
    if live > 0 { return .stay }
    return now - max(lastLiveAt ?? startedAt, startedAt) >= LifecycleK.grace ? .quit("no live session for 120 s") : .stay
}

/// The memory between two polls: when a session was last seen, and what the plugin file said at its last look (once a minute, only with --plugin-root).
struct LifecycleState {
    let startedAt: Double, watchPlugin: Bool
    var lastLiveAt: Double?, pluginPresent = true, lastPluginCheck: Double?
    init(startedAt: Double, watchPlugin: Bool) { self.startedAt = startedAt; self.watchPlugin = watchPlugin }
    mutating func step(now: Double, live: Int, pluginProbe: () -> Bool?) -> LifecycleVerdict {
        if live > 0 { lastLiveAt = now }
        if watchPlugin, lastPluginCheck.map({ now - $0 >= LifecycleK.pluginPoll }) ?? true {
            lastPluginCheck = now
            if let p = pluginProbe() { pluginPresent = p }          // unknown (missing, unreadable, odd) changes nothing
        }
        return lifecycleDecision(now: now, lastLiveAt: lastLiveAt, startedAt: startedAt, live: live, pluginPresent: pluginPresent)
    }
}

/// <config>/plugins/installed_plugins.json = {"plugins": {"name@market": [...]}}. true when a key whose name part (before @) is budgit or studio exists, false when the
/// file is well formed and has none, nil when it cannot be told (missing, a link, not mine, over 1 MB, not that shape): then he stays.
func pluginInstalled(configDir: String) -> Bool? {
    let path = configDir + "/plugins/installed_plugins.json"
    guard let st = lstatInfo(path), (st.st_mode & S_IFMT) == S_IFREG, st.st_uid == getuid(), st.st_size <= 1_048_576,
          let d = FileManager.default.contents(atPath: path), let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict, let plugins = o["plugins"] as? Dict else { return nil }
    return plugins.keys.contains { k in
        let name = k.split(separator: "@", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? ""
        return name == "budgit" || name == "studio"
    }
}

/// Every way out ends here: events.log goes (the hooks would keep appending to it), brote.lock is left for the OS to release with the process.
enum ExitCause {
    case menu, signal(Int32), lifecycle(String)
    var note: String {
        switch self {
        case .menu: return "quit from the menu"
        case .signal(let s): return "signal \(s): cleaning up"
        case .lifecycle(let why): return why
        }
    }
}
func cleanupOnExit(dirs: BroteDirs) { unlink(dirs.events) }

/// events.log, emptied at start so there is no backlog: 0600, never through a link. Called only after the instance lock is held.
func resetEventsFile(dirs: BroteDirs) {
    let fd = open(dirs.events, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0o600)
    if fd >= 0 { fchmod(fd, 0o600); close(fd) }
}

/// The menu item that removes Brote (the plugin's own `floatctl.py uninstall`, run only after a yes). Words: the catalog has none yet, so two fixed titles.
func removeMenuWords(prefs: [String]) -> (title: String, ask: String, body: String, yes: String, no: String) {
    let es = (prefs.first { ["es", "en"].contains(String($0.lowercased().prefix(2))) }).map { $0.lowercased().hasPrefix("es") } ?? false
    return es ? ("Quitar Brote…", "¿Quitar Brote del todo?", "Se borran su app, sus ajustes, sus registros y sus ficheros de sesión. El plugin de Budgit no se toca.", "Quitar", "Cancelar")
              : ("Remove Brote…", "Remove Brote completely?", "His app, settings, logs and session files are deleted. The Budgit plugin is not touched.", "Remove", "Cancel")
}
func uninstallArgv(layout l: PluginLayout) -> [String]? { l.isRoot ? ["/usr/bin/python3", "-B", l.scriptsDir + "/floatctl.py", "uninstall"] : nil }


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
// MARK: - Brote and the book: two bodies that never overlap
/// The x range his panel may not take while the book is at `book`: inside it, his art (columns 8 to 25 of 34) comes closer than
/// K.bookMinGap to the book's cover (columns 4 to 16 of its 20). nil when the book is not at his height (floating above or below him).
func bookBlock(book: Pt, broteY: Double) -> (lo: Double, hi: Double)? {
    guard abs(book.y - broteY) < K.spriteH * 0.7 else { return nil }
    return (book.x + K.bookArtL - K.bookMinGap - K.bodyR, book.x + K.bookArtR + K.bookMinGap - K.bodyL)
}
/// His ledge with the book in it: he keeps to his own side of the book.
func ledgeAroundBook(lo: Double, hi: Double, x: Double, block b: (lo: Double, hi: Double)?) -> (lo: Double, hi: Double) {
    guard let b = b, b.hi > lo, b.lo < hi else { return (lo, hi) }
    return x <= (b.lo + b.hi) / 2 ? (lo, max(lo, min(hi, b.lo))) : (min(hi, max(lo, b.hi)), hi)
}
/// Where he goes when the book comes within K.bookDodge of him while it is dragged, or closer than the gap otherwise (dropped on him, he
/// landed on it): out on his own side, or over to the other side when he is cornered.
/// nil: he is clear. `crossing`: the way out passes the book (he flies over it).
func clearOfBook(x: Double, block b: (lo: Double, hi: Double)?, lo: Double, hi: Double, dragging: Bool) -> (x: Double, crossing: Bool)? {
    guard let b = b else { return nil }
    let near = dragging ? K.bookDodge - K.bookMinGap : 0                  // dragged at him: from 24 pt; else only when the gap is gone
    guard x > b.lo - near, x < b.hi + near else { return nil }
    let left = x <= (b.lo + b.hi) / 2
    let mine = left ? b.lo - near : b.hi + near, other = left ? b.hi + near : b.lo - near
    if mine >= lo - 0.5, mine <= hi + 0.5 { return (mine, false) }
    if other >= lo - 0.5, other <= hi + 0.5 { return (other, true) }
    return nil
}
/// He stands right beside the book (a gap of 9 to 40 pt between the two drawings): close enough to pat it, sit by it or pick it up.
func besideBook(x: Double, block b: (lo: Double, hi: Double)?) -> Bool {
    guard let b = b else { return false }
    return (x <= b.lo && x >= b.lo - 31) || (x >= b.hi && x <= b.hi + 31)
}

/// A call (question, permission, update) faces Claude's window. The call art calls to the right (bubble top right), so he mirrors when it is on his left.
func callFlip(claude c: Rc, broteCx: Double) -> Bool { c.x + c.w / 2 < broteCx }

/// Where he stands, for the director's idle animations: the floor, Claude's top or another window's top; and whether, on the floor,
/// he stands right next to a side of Claude's window (within 40 pt, the window reaching down past his head) so he can lean on it.
func perchOf(host: Int?, claudeID: Int?, pos: Pt, claude: Rc?) -> (on: String, claude: Bool, nearClaude: Bool) {
    let on = host == nil ? "floor" : (host == claudeID ? "claude" : "other")
    guard on == "floor", let c = claude, c.minY < pos.y + K.spriteH else { return (on, claude != nil, false) }
    return (on, true, abs(pos.x + K.spriteW - c.minX) < 40)          // its left side, on his right: ag_lean draws the wall on his right
}

/// What Claude's window did since the last look, as one of his clips: it went away (minimised or hidden), came back, or jumped while he
/// was not riding on it (he chases it). nil: nothing worth a clip.
func claudeWindowClip(old: Rc?, new: Rc?, ridingIt: Bool) -> String? {
    switch (old, new) {
    case (.some, nil): return "w_minimise"
    case (nil, .some): return "w_restore"
    case let (.some(a), .some(b)): return !ridingIt && hypot(b.x - a.x, b.y - a.y) >= 30 ? "w_chase" : nil
    default: return nil
    }
}
/// The Libreta lists the stale rows itself: while it is open the sprite engine is told there are none.
func spriteStale(real: Int, panelOpen: Bool) -> Int { panelOpen ? 0 : real }

/// Drag, throw and rest, the state both bodies share: the 3 pt threshold, the 0.1 s pointer samples, the fly step. No window, no timer.
struct Dragger {
    enum Mode { case rest, held(grab: Pt, start: Pt, moved: Bool), flying(body: Body, bounds: Bounds, dizzy: Bool) }
    enum Drag: Equatable { case idle, started(Pt), moving(Pt) }          // the origin the body should take
    enum Release: Equatable { case notHeld, click, moved(velocity: Pt) }
    struct FlightTick { var body: Body; var landed: Bool; var dizzy: Bool; var firstHit = false }
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
    private(set) var plats: [Plat] = [], hits = 0                         // the window tops this flight may land on; floors touched so far
    mutating func fly(_ body: Body, in bounds: Bounds, dizzy: Bool, plats: [Plat] = []) { mode = .flying(body: body, bounds: bounds, dizzy: dizzy); self.plats = plats; hits = 0 }
    mutating func step(dt: Double) -> FlightTick? {
        guard case .flying(var body, let bounds, let dizzy) = mode else { return nil }
        let r = stepFlyOver(&body, dt: dt, in: bounds, plats: plats)
        let landed = r.flight == .landed
        if r.bump { hits += 1 }
        mode = landed ? .rest : .flying(body: body, bounds: bounds, dizzy: dizzy)
        return FlightTick(body: body, landed: landed, dizzy: dizzy, firstHit: r.bump && hits == 1)
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

// MARK: - the round-3 Libreta model (feed version 3: Foundation only, so it self-tests with no window server)

// Words come ONLY from the feed's `t` catalog; a missing key prints nothing. A feed without version 3 (a plugin older than the app) keeps the old page.
func libretaIsV3(_ feed: Dict) -> Bool { intOf(feed["version"]) >= 3 }

struct L3Badge: Equatable { enum Kind { case wait, work, rest }; var kind: Kind; var n: Int; var label: String }
struct L3Pot: Equatable { var n: Int; var future: Bool }
struct L3Shelf: Equatable {
    var heading: String, total: String
    var pots: [L3Pot], letters: [String], today: Int
    var fill: Int?, caption: String
}
struct L3Wait: Equatable { var title: String, project: String, lead: String, why: String, tail: String, ago: String }
struct L3Work: Equatable { var time: String, title: String, project: String, itemId: String?, itemText: String }
struct L3Group: Equatable { var project: String, header: String, items: [(id: String, title: String)]
    static func == (a: L3Group, b: L3Group) -> Bool { a.project == b.project && a.header == b.header && a.items.map { $0.id } == b.items.map { $0.id } } }
struct L3Fold: Equatable { var project: String, text: String }
struct L3Tray: Equatable { var heading: String, count: Int, groups: [L3Group], folds: [L3Fold], more: String? }
struct L3DoneRow: Equatable { var title: String, project: String, time: String? }
struct L3DoneDay: Equatable { var label: String, rows: [L3DoneRow] }
struct Libreta3Model: Equatable {
    var title: String, chip: String
    var badges: [L3Badge]
    var shelf: L3Shelf
    var waitHeading: String, waits: [L3Wait]
    var workHeading: String, works: [L3Work], rest: String?
    var tray: L3Tray
    var doneHeading: String, doneCount: Int, days: [L3DoneDay]
    var parked: String?
    var emptyWork: String? = nil, emptyDone: String? = nil      // the lines under an empty "Working" or "Done" layer
}

private func upFirst(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }
func joinList(_ names: [String], and: String) -> String {
    if names.count <= 1 { return names.first ?? "" }
    return names.dropLast().joined(separator: ", ") + " " + and + " " + names.last!
}
func potLevel(_ n: Int) -> Int { n == 0 ? 0 : n < 5 ? 1 : n < 15 ? 2 : n < 30 ? 3 : 4 }

func libretaModel3(feed: Dict, now: Date, tz: TimeZone = .current, old: Bool = false) -> Libreta3Model {
    let t = (feed["t"] as? Dict ?? [:]).compactMapValues { $0 as? String }
    let dayNames = (feed["t"] as? Dict)?["days"] as? [String] ?? []     // Sunday first
    var cal = Calendar(identifier: .gregorian); cal.timeZone = tz
    let hmF = DateFormatter(); hmF.locale = Locale(identifier: "en_US_POSIX"); hmF.timeZone = tz; hmF.dateFormat = "HH:mm"
    func hm(_ d: Date) -> String { hmF.string(from: d) }
    func daysAgo(_ d: Date) -> Int { cal.dateComponents([.day], from: cal.startOfDay(for: d), to: cal.startOfDay(for: now)).day ?? 0 }
    func dayName(_ d: Date) -> String { let i = cal.component(.weekday, from: d) - 1; return i < dayNames.count ? dayNames[i] : "" }
    func ago(_ d: Date) -> String {
        let s = max(0, Int(now.timeIntervalSince(d)))
        if s < 60 { return fillT(t["book.ago_s"], ["n": String(s)]) }
        if s < 3600 { return fillT(t["book.ago_min"], ["n": String(s / 60)]) }
        if s < 6 * 3600 { return fillT(t["book.ago_h"], ["n": String(s / 3600)]) }
        let dd = daysAgo(d)
        if dd == 0 { return fillT(t["book.since_at"], ["hm": hm(d)]) }
        if dd == 1 { return t["book.since_yesterday"] ?? "" }
        return fillT(t["book.since_day"], ["day": dayName(d)])
    }
    let other = t["book.project_other"] ?? ""
    func proj(_ p: Any?) -> String { let s = (p as? String) ?? ""; return s.isEmpty ? other : s }
    func plural(_ n: Int, _ one: String, _ many: String, _ vars: [String: String] = [:]) -> String { fillT(t[n == 1 ? one : many] ?? t[many], vars) }

    // sessions and claims
    let working = (feed["working"] as? [Dict] ?? []).filter { ($0["kind"] as? String) == "session" || ($0["kind"] as? String) == "claim" }
    func since(_ r: Dict) -> Date { Date(timeIntervalSince1970: (r["since"] as? NSNumber)?.doubleValue ?? 0) }
    func rowTitle(_ r: Dict) -> String {
        for k in ["session_name", "title"] { if let s = (r[k] as? String)?.trimmingCharacters(in: .whitespaces), !s.isEmpty { return s } }
        return proj(r["project"])
    }
    let waiting = working.filter { ($0["session_status"] as? String) == "waiting" }.sorted { since($0) < since($1) }
    let busy = working.filter { ($0["session_status"] as? String) != "waiting" }.sorted { since($0) < since($1) }
    let quiet = intOf(feed["quiet_sessions"])
    let paused = (feed["paused"] as? [Dict] ?? []).count

    // badges: nothing for "waits for you" when nobody waits
    var badges: [L3Badge] = []
    if !waiting.isEmpty { badges.append(L3Badge(kind: .wait, n: waiting.count, label: t[waiting.count == 1 ? "book.badge_wait_one" : "book.badge_wait_many"] ?? "")) }
    badges.append(L3Badge(kind: .work, n: busy.count, label: t["book.badge_working"] ?? ""))
    badges.append(L3Badge(kind: .rest, n: quiet, label: t["book.badge_resting"] ?? ""))

    let waits: [L3Wait] = waiting.map { r in
        let wf = (r["waiting_for"] as? String) ?? ""
        let what = t[wf == "permission" ? "book.needs_permission" : wf == "question" ? "book.needs_question" : "book.needs_other"] ?? ""
        let parts = (t["book.needs"] ?? "{what}").components(separatedBy: "{what}")
        return L3Wait(title: rowTitle(r), project: proj(r["project"]), lead: parts.first ?? "", why: what, tail: parts.count > 1 ? parts[1] : "", ago: ago(since(r)))
    }
    let works: [L3Work] = busy.map { r in
        let item = (r["item"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let text = item != nil ? ((r["title"] as? String) ?? "") : (t["book.no_item"] ?? "")
        return L3Work(time: hm(since(r)), title: rowTitle(r), project: proj(r["project"]), itemId: item, itemText: text)
    }
    var rest: String?
    if quiet > 0 || paused > 0 {
        var parts: [String] = []
        if quiet > 0 { parts.append(plural(quiet, "book.rest_one", "book.quiet_sessions", ["n": String(quiet)])) }
        if paused > 0 { parts.append(plural(paused, "book.rest_paused_one", "book.rest_paused_many", ["n": String(paused)])) }
        rest = parts.joined(separator: " \u{00B7} ")
    }
    // the session source could not be read: say so instead of showing counts that mean nothing (a feed without `sources` is read as sound)
    if let sess = (feed["sources"] as? Dict)?["sessions"] as? String, sess != "ok" { rest = t["book.no_live_data"] ?? rest }

    // projects: counts are summed; a project with an error has no counts and adds nothing
    let projects = feed["projects"] as? [Dict] ?? []
    func counts(_ p: Dict) -> Dict? { p["counts"] as? Dict }
    func sum(_ k: String) -> Int { projects.reduce(0) { $0 + intOf(counts($1)?[k]) } }
    let nDoing = sum("doing"), nNext = sum("next"), nLater = sum("later"), nDone = sum("done")
    let total = nDoing + nNext + nLater + nDone
    let activeNames = Set(working.filter { ["busy", "waiting"].contains(($0["session_status"] as? String) ?? "") || ($0["kind"] as? String) == "claim" }
        .compactMap { $0["project"] as? String })

    // shelf
    let weekArr = (feed["week"] as? [Any] ?? []).map { intOf($0) }
    let week = weekArr + Array(repeating: 0, count: max(0, 7 - weekArr.count))
    let today = (cal.component(.weekday, from: now) + 5) % 7      // Monday first
    let letters = Array(t["book.week_letters"] ?? "").map { String($0) }
    let pct = total > 0 ? Int((100.0 * Double(nDone) / Double(total)).rounded()) : 0
    let shelf = L3Shelf(
        heading: t["book.h_week"] ?? "", total: fillT(t["book.week_done"], ["n": String(week.prefix(7).reduce(0, +))]),
        pots: week.prefix(7).enumerated().map { L3Pot(n: $0.element, future: $0.offset > today) }, letters: letters, today: today,
        fill: total > 0 ? Int((60.0 * Double(nDone) / Double(total)).rounded()) : nil,
        caption: total > 0 ? fillT(t["book.progress"], ["done": String(nDone), "total": String(total), "pct": String(pct)]) : (t["book.progress_none"] ?? ""))

    // tray: active projects first (newest change first), quiet ones folded at the bottom
    let sorted = projects.sorted { intOf($0["last_change"]) > intOf($1["last_change"]) }
    func isActive(_ p: Dict) -> Bool { activeNames.contains((p["name"] as? String) ?? "\u{0}") || intOf(counts(p)?["doing"]) > 0 }
    var groups: [L3Group] = [], folds: [L3Fold] = [], shown = 0, activeNext = 0, left: [String] = []
    for p in sorted where counts(p) != nil && intOf(counts(p)?["next"]) > 0 {
        let name = proj(p["name"]), n = intOf(counts(p)?["next"])
        if isActive(p) {
            let items = (p["queue_recent"] as? [Dict] ?? []).prefix(2).map { (id: ($0["id"] as? String) ?? "", title: ($0["title"] as? String) ?? "") }
            groups.append(L3Group(project: name, header: fillT(t["book.group_next"], ["n": String(n)]), items: Array(items)))
            shown += items.count; activeNext += n
            if n > items.count { left.append(name) }
        } else {
            folds.append(L3Fold(project: name, text: fillT(t["book.fold_quiet"], ["n": String(n)])))
        }
    }
    let moreN = activeNext - shown
    let tray = L3Tray(heading: t["book.h_next"] ?? "", count: nNext, groups: groups, folds: folds,
                      more: moreN > 0 ? fillT(t["book.more_in"], ["n": String(moreN), "projects": joinList(left, and: t["book.join_and"] ?? "")]) : nil)

    // done: newest first, 4 rows at most, grouped by day; the time bubble only when it changes inside a day
    let iso = ISO8601DateFormatter(); iso.formatOptions = [.withInternetDateTime]
    let isoF = ISO8601DateFormatter(); isoF.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let recent: [(Date, Dict)] = (feed["recent_done"] as? [Dict] ?? []).compactMap { r in
        guard let s = r["done_at"] as? String, let d = iso.date(from: s) ?? isoF.date(from: s) else { return nil }
        return (d, r)
    }.sorted { $0.0 > $1.0 }
    var days: [L3DoneDay] = [], lastLabel = "", lastTime = ""
    for (d, r) in recent.prefix(4) {
        let dd = daysAgo(d)
        let label = dd == 0 ? (t["book.today"] ?? "") : dd == 1 ? (t["book.yesterday"] ?? "") : upFirst(dayName(d))
        if label != lastLabel || days.isEmpty { days.append(L3DoneDay(label: label, rows: [])); lastLabel = label; lastTime = "" }
        let time = hm(d)
        days[days.count - 1].rows.append(L3DoneRow(title: (r["title"] as? String) ?? "", project: proj(r["project"]), time: time == lastTime ? nil : time))
        lastTime = time
    }

    // the live chip: the newest change we know of
    let newest = max(working.map { since($0).timeIntervalSince1970 }.max() ?? 0, Double(projects.map { intOf($0["last_change"]) }.max() ?? 0))
    // a failed refresh must not leave "live" over data that is no longer fresh: the chip says so, as the old page did
    let chip = old ? (t["book.old"] ?? "") : newest > 0 ? fillT(t["book.live_chip"], ["ago": ago(Date(timeIntervalSince1970: newest))]) : ""

    let parkedNames = sorted.filter { intOf(counts($0)?["later"]) > 0 }.map { proj($0["name"]) + " " + String(intOf(counts($0)?["later"])) }
    let parked = nLater > 0 ? plural(nLater, "book.parked_one", "book.parked_many", ["n": String(nLater)]) + " \u{00B7} " + parkedNames.joined(separator: ", ") : nil

    return Libreta3Model(
        title: t["book.button"] ?? "", chip: chip, badges: badges, shelf: shelf,
        waitHeading: t[waits.count == 1 ? "book.h_wait_one" : "book.h_wait_many"] ?? "", waits: waits,
        workHeading: t["book.h_working"] ?? "", works: works, rest: rest, tray: tray,
        doneHeading: t["book.h_done"] ?? "", doneCount: nDone, days: days, parked: parked,
        emptyWork: works.isEmpty && rest == nil ? t["book.empty_doing"] : nil, emptyDone: days.isEmpty ? t["book.empty_done"] : nil)
}

// MARK: - watching the files the feed names

/// The feed's `watch.files`, checked with stat() only (no file is opened, no kqueue, nothing runs while the panel is closed).
/// A path outside the home folder, or one past the 64th, is ignored, so a feed can never make the app watch the whole disk.
struct WatchSet {
    private(set) var paths: [String] = []
    private var seen: [String: [Int]] = [:]

    static func stamp(_ path: String) -> [Int] {
        var st = stat()
        guard stat(path, &st) == 0 else { return [-1] }               // gone is a state too: it changes when the file comes back
        return [Int(st.st_mtimespec.tv_sec), Int(st.st_mtimespec.tv_nsec), Int(st.st_size), Int(st.st_ino)]
    }
    /// Takes the feed's list. A path already watched keeps its last stamp, so a write that landed while the feed was running is still
    /// seen on the next check; only a new path is stamped now. Returns true when the list itself changed.
    @discardableResult mutating func reset(_ files: [String], home: String) -> Bool {
        let h = home.hasSuffix("/") ? home : home + "/"
        var next: [String] = []
        for f in files where f.hasPrefix(h) && !f.contains("/../") && !next.contains(f) && next.count < 64 { next.append(f) }
        let moved = next != paths
        paths = next
        var keep: [String: [Int]] = [:]
        for p in next { keep[p] = seen[p] ?? WatchSet.stamp(p) }
        seen = keep
        return moved
    }
    /// True when any watched path changed since the baseline (and takes the new stamps).
    mutating func changed() -> Bool {
        var any = false
        for p in paths { let s = WatchSet.stamp(p); if seen[p] != s { seen[p] = s; any = true } }
        return any
    }
}

/// When to run the feed again: a change waits 0.3 s for the writer to finish, and runs are at least 3 s apart.
struct WatchPace {
    static let every = 2.0, settle = 0.3, gap = 3.0
    var lastRun = -Double.infinity
    /// Seconds from `now` until the run a change seen at `now` should get.
    func delay(now: Double) -> Double { max(WatchPace.settle, lastRun + WatchPace.gap - now) }
}

/// The open panel's two timers: the 2 s look at the watched files and the one pending feed run. They exist only between `start` and
/// `stop`; a closed panel has neither, and a run cannot be scheduled without the look: while closed, both timers are off.
final class WatchTimers {
    private(set) var tick: Timer?, run: Timer?
    var active: Bool { (tick?.isValid ?? false) || (run?.isValid ?? false) }
    var pending: Bool { run?.isValid ?? false }
    func start(every: Double, _ fire: @escaping () -> Void) {
        stop()
        let t = Timer(timeInterval: every, repeats: true) { _ in fire() }
        t.tolerance = min(0.4, every / 2)
        RunLoop.main.add(t, forMode: .common); tick = t
    }
    func schedule(after delay: Double, _ fire: @escaping () -> Void) {
        run?.invalidate(); run = nil
        guard tick != nil else { return }
        let r = Timer(timeInterval: delay, repeats: false) { [unowned self] _ in self.run = nil; fire() }
        RunLoop.main.add(r, forMode: .common); run = r
    }
    func stop() {
        tick?.invalidate(); tick = nil
        run?.invalidate(); run = nil
    }
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

func statusArgv(plugin hub: String) -> [String] { statusArgv(layout: PluginLayout.hub(hub), project: hub) }
/// A hub clone is its own project (`--project <hub>`). The installed plugin's cache folder is not a project, so no project is passed unless one was given:
/// from "/" with no project `budgit status --brote --live` prints nothing (exit 0) and the data line stays off.
func statusArgv(layout l: PluginLayout, project: String?) -> [String] {
    ["/usr/bin/python3", "-B", l.budgit, "status", "--brote", "--live"] + (project.map { ["--project", $0] } ?? [])
}

/// Explicit and small: a GUI app has none of the shell's variables, and Python must not write bytecode into the plugin.
/// `configDir` (the launcher's CLAUDE_CONFIG_DIR, when it set one) goes through so budgit reads the right Claude folder.
func statusEnv(home: String, configDir: String? = nil) -> [String: String] {
    var e = ["PATH": "/usr/bin:/bin", "HOME": home, "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"]
    if let c = configDir { e["CLAUDE_CONFIG_DIR"] = c }
    return e
}

// MARK: - the update offer and the chat link (pure; they mirror the plugin's update and chat-link scripts)

/// A plugin or marketplace name as Claude Code writes it: nothing else is ever put into a command.
func safeName(_ v: Any?) -> String? {
    guard let t = v as? String, t.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", options: .regularExpression) != nil else { return nil }
    return t
}
struct UpdateOffer: Equatable { var version: String, market: String, plugin: String }
/// The status object's `update`, only when a newer release waits in the marketplace clone (a stale chat is the bar's business, not his).
func updateOffer(_ st: Dict?) -> UpdateOffer? {
    guard let u = st?["update"] as? Dict, (u["kind"] as? String) == "available",
          let v = u["available"] as? String, v.range(of: "^[0-9]{1,5}(\\.[0-9]{1,5}){0,3}$", options: .regularExpression) != nil,
          let m = safeName(u["market"]), let p = safeName(u["plugin"]) else { return nil }
    return UpdateOffer(version: v, market: m, plugin: p)
}
/// The two commands `budgit update` prints, as argument lists: never a shell line.
func updateArgvs(claude: String, offer o: UpdateOffer) -> [[String]] {
    [[claude, "plugin", "marketplace", "update", o.market], [claude, "plugin", "update", o.plugin + "@" + o.market]]
}
func updateText(offer o: UpdateOffer) -> String { updateArgvs(claude: "claude", offer: o).map { $0.joined(separator: " ") }.joined(separator: "\n") }
/// A GUI app has no shell PATH: the places Claude Code's own installers put `claude`.
func findClaude(home: String, exists: (String) -> Bool) -> String? {
    [home + "/.local/bin/claude", home + "/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"].first(where: exists)
}
func updateEnv(home: String, configDir: String? = nil) -> [String: String] {
    var e = ["PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:" + home + "/.local/bin", "HOME": home, "LANG": "en_US.UTF-8"]
    if let c = configDir { e["CLAUDE_CONFIG_DIR"] = c }
    return e
}

let chatLinkRoot = "claude://claude.ai/epitaxy/"
/// The link of the one live chat whose CLI session id starts with `prefix`; nil for no match, several, or a bad prefix. `records` are the app's session
/// records, only the three keys read here; the link is built from a record id of a strict shape, never from file text.
func chatLink(prefix: String, records: [Dict]) -> String? {
    guard prefix.range(of: "^[A-Za-z0-9-]{8,36}$", options: .regularExpression) != nil else { return nil }
    var ids = Set<String>()
    for r in records {
        guard let cli = r["cliSessionId"] as? String, cli.hasPrefix(prefix), let id = r["sessionId"] as? String,
              id.range(of: "^local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", options: .regularExpression) != nil else { continue }
        let archived = r["isArchived"]
        if let b = archived as? NSNumber, b.intValue != 0 { continue }
        if let t = archived as? String, !t.isEmpty { continue }
        ids.insert(id)
    }
    return ids.count == 1 ? chatLinkRoot + ids.first! : nil
}
/// The desktop app's own session records, read fresh on every call: `.../claude-code-sessions/*/*/local_*.json`, 64 KB at most each, JSON objects only.
/// Called ONLY from the click that opens a chat (`openChat`), never at start or on a poll. Of each record only the three keys the link needs are kept
/// (sessionId, cliSessionId, isArchived, and only with the right type): no title, folder or message ever leaves this function, and nothing here is logged.
func readChatRecords(home: String, fm: FileManager = .default) -> [Dict] {
    let root = home + "/Library/Application Support/Claude/claude-code-sessions"
    var out: [Dict] = []
    for a in ((try? fm.contentsOfDirectory(atPath: root)) ?? []).sorted() {
        for b in ((try? fm.contentsOfDirectory(atPath: root + "/" + a)) ?? []).sorted() {
            let dir = root + "/" + a + "/" + b
            for f in ((try? fm.contentsOfDirectory(atPath: dir)) ?? []).sorted() where f.hasPrefix("local_") && f.hasSuffix(".json") {
                let path = dir + "/" + f
                guard let size = (try? fm.attributesOfItem(atPath: path))?[.size] as? NSNumber, size.intValue <= 65536,
                      let d = fm.contents(atPath: path), let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict else { continue }
                var slim: Dict = [:]
                if let v = o["sessionId"] as? String { slim["sessionId"] = v }
                if let v = o["cliSessionId"] as? String { slim["cliSessionId"] = v }
                if let v = o["isArchived"], v is NSNumber || v is String { slim["isArchived"] = v }
                out.append(slim)
            }
        }
    }
    return out
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
    let privateFolder: Bool
    private var counts: [String: Int] = [:]
    private let f: DateFormatter
    /// `privateFolder`: the app's own log folder (~/Library/Logs/BroteWindow), kept 0700. A scenario run's folder is somebody else's and keeps its mode.
    init(path: String, privateFolder: Bool = false) {
        self.path = path
        self.privateFolder = privateFolder
        f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS"
    }
    /// Truncates the log: one start, one file, 0600, never through a link. A new own folder is 0700 and an older looser one of mine is set back to 0700. Never throws.
    func reset() {
        let dir = (path as NSString).deletingLastPathComponent
        if let st = lstatInfo(dir) {
            if privateFolder, (st.st_mode & S_IFMT) == S_IFDIR, st.st_uid == getuid(), st.st_mode & 0o077 != 0 { chmod(dir, 0o700) }
        } else { _ = makeDirs(dir, leaf: privateFolder ? 0o700 : 0o755) }
        let fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0o600)
        if fd >= 0 { fchmod(fd, 0o600); close(fd) }
    }
    /// `PROBE Pn k=v ...`. `cap` limits how many lines a name may write. A failing write is swallowed: the logger never kills the app.
    @discardableResult
    func log(_ name: String, _ fields: [(String, String)], cap: Int = Int.max) -> Bool {
        let n = (counts[name] ?? 0) + 1
        if n > cap { return false }
        counts[name] = n
        let line = f.string(from: Date()) + " PROBE " + name + " " + fields.map { $0.0 + "=" + $0.1.replacingOccurrences(of: "\n", with: " ") }.joined(separator: " ") + "\n"
        let fd = open(path, O_WRONLY | O_APPEND | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var st = stat()
        let bytes = Array(line.utf8)
        guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, Int(st.st_size) + bytes.count <= K.logCap else { return false }
        return write(fd, bytes, bytes.count) == bytes.count
    }
    func plain(_ text: String) { _ = log("NOTE", [("text", text)]) }
}

// MARK: - what changed on the data line (the book's items and the week), for his reactions
/// The ids in one list of the data line: items are {id, ...}; stale is a list of ids.
func feedIDs(_ v: Any?) -> Set<String> {
    Set((v as? [Any] ?? []).compactMap { ($0 as? String) ?? (($0 as? Dict)?["id"] as? String) })
}
/// Two data lines in a row -> the clips they call for (t_new, t_start, t_unstale, tr_week) and how many items were newly done.
/// A list the older line lacks (the saved line at launch keeps only the numbers) says nothing: no reaction to what was always there.
func feedChanges(old: Dict?, new: Dict) -> (clips: [String], done: Int) {
    guard let old = old else { return ([], 0) }
    var clips: [String] = []
    let has = { (d: Dict, k: String) in d[k] is [Any] }
    if has(old, "queue_recent"), has(new, "queue_recent"),
       !feedIDs(new["queue_recent"]).subtracting(feedIDs(old["queue_recent"])).subtracting(feedIDs(old["doing"])).isEmpty { clips.append("t_new") }
    if has(old, "doing"), has(new, "doing"), !feedIDs(new["doing"]).subtracting(feedIDs(old["doing"])).isEmpty { clips.append("t_start") }
    if has(old, "stale"), has(new, "stale"), !feedIDs(old["stale"]).subtracting(feedIDs(new["stale"])).intersection(feedIDs(new["doing"])).isEmpty { clips.append("t_unstale") }
    if let a = (old["resets_at"] as? NSNumber)?.doubleValue, let b = (new["resets_at"] as? NSNumber)?.doubleValue, b - a > 3 * 86400 { clips.append("tr_week") }
    var done = 0
    if let a = old["done_total"] as? NSNumber, let b = new["done_total"] as? NSNumber { done = max(0, b.intValue - a.intValue) }
    return (clips, min(done, 10))
}
/// Nothing in progress and nothing next (t_all), and the week runs out before it resets at today's rate (q_runout).
func feedQuiet(_ st: Dict) -> Bool {
    guard let c = st["counts"] as? Dict, c["doing"] != nil, c["next"] != nil else { return false }
    return intOf(c["doing"]) == 0 && intOf(c["next"]) == 0
}
func feedRunsOut(_ st: Dict) -> Bool { st["runs_out"] is NSNumber }
/// On battery below 20 % (no battery, or on power: never).
func batteryLow(percent: Int?, onPower: Bool) -> Bool { guard let p = percent else { return false }; return !onPower && p < 20 }

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
    function __S(){ var S = __p.spec(), D = __d ? __d.frameSpec() : null; if (D && S.armR !== 'point') S = D; if (__bookOn) S.ex = S.ex.filter(function(e){ return e[0] !== 'book' && e[0] !== 'bkF'; }); return S; }
    function __cfg(j){ var c = JSON.parse(j);
      var want = Object.prototype.hasOwnProperty.call(PALS, c.state) ? c.state : 'contento';
      if (__d) __d.setState(want);
      if (want !== __p.state) { var was = __p.state; __p.setState(want); if (was === 'dormido') __p.do('wake'); }
      __p.h5 = c.h5|0; if (__d && __d.setH5) __d.setH5(c.h5|0); __p.stale = c.stale|0; __p.held = !!c.held; __p.hover = !!c.hover; __p.walking = !!c.walking; __bookOn = !!c.bookOn; }
    function __tick(){ __p.tick(); if (__d) __d.tick(); }
    function __now(){ return JSON.stringify([__d && __d.clip ? __d.clip.id : null, __p.act || null, __p.poke || null]); }
    function __do(a){ __p.do(a); }
    function __frame(){ var S = __S(); return JSON.stringify({g: compose(S, __p.pal()), pt: S.armR === 'point', gl: __glyphs(S)}); }
    // the sign boxes of this frame's extras, in grid cells, placed the way compose() places an extra
    function __glyphs(S){ var dy = S.dy + (S.sit ? 1 : 0), out = [];
      S.ex.forEach(function(e){ var sp = SPR[e[0]]; if (!sp || !sp.glyph) return;
        var by = e[3] ? OY + e[1] : OY + dy + e[1], bx = e[3] ? OX + e[2] : OX + S.dx + e[2];
        out.push([bx + sp.glyph[0], by + sp.glyph[1], sp.glyph[2], sp.glyph[3]]); });
      return out; }
    function __pal(s){ return JSON.stringify(Object.prototype.hasOwnProperty.call(PALS, s) ? PALS[s] : PALS.contento); }
    function __flags(){ return '|' + (__d && __d.holding() ? 1 : 0) + (__d && __d.bookClip() ? 1 : 0) + (__d && __d.reading ? 1 : 0) + (__d && __d.directing() ? 1 : 0) + (__d && __d.calling() ? 1 : 0); }
    function __read(){ if (__d) __d.startReading(); }
    function __dropBook(){ return !!(__d && __d.dropBook()); }
    function __key(){ return JSON.stringify([__S(), __p.pal()]) + __flags(); }
    function __step(){ __p.tick(); if (__d) __d.tick(); return JSON.stringify([__S(), __p.pal()]) + __flags(); }
    function __ev(t){ if (__d) __d.feed(t); }
    function __front(b){ if (__d) __d.setFront(b); }
    function __rm(b){ if (__d) __d.setReduceMotion(b); }
    function __touch(){ if (__d) __d.touch(); }
    function __walkDx(){ var w = __d && __d.walkDx ? __d.walkDx() : null; return w === null ? -999 : w; }
    function __perch(j){ if (__d && __d.setPerch) __d.setPerch(JSON.parse(j)); }
    function __play(id, a, b){ return __d ? __d.play(id, {loop: a >= 0 ? [a, b] : null}) : false; }
    function __stopClip(){ if (__d) __d.stop(); }
    function __tickle(op){ if (!__d) return false; var on = !!(__d.forced && __d.forced.id === 'w_tickle');
      if (op === 1) { __d.cool.w_tickle = 0; if (!__d.react('w_tickle')) return false; __p.do(null); return true; }
      if (op === 0 && on) __d.stop(); return on; }
    function __fetch(sit){ return !!(__d && __d.react('bk_fetch', sit ? {from: 34} : {to: 10, go: true})); }
    function __working(){ return !!(__d && __d.working()); }
    function __calm(){ return !!(__d && __d.calm()); }
    function __poke(dir){ return __d ? __d.poke(dir) : false; }
    function __amb(l){ if (__d) __d.setAmbient(l); }
    function __hitGrid(){ var S = __S(); S.ex = []; S.bubble = -1; S.fallen = false; return JSON.stringify(compose(S, __p.pal())); }
    function __act(){ return __p.act === null ? '' : __p.act; }
    function __fid(){ return !!__p.fid; }
    function __state(){ return __p.state; }
    function __upd(v){ if (__d) __d.setUpdate(v); }
    function __feedInfo(j){ return !!(__d && __d.setFeed && __d.setFeed(JSON.parse(j))); }
    function __idle(s){ if (__d && __d.setIdle) __d.setIdle(s); }
    function __battery(b){ if (__d && __d.setBattery) __d.setBattery(!!b); }
    function __updDone(){ if (__d) __d.dismissUpdate(); }
    function __call(){ var c = __d ? __d.pendingCall() : null; return c ? c.kind + ' ' + c.sid : ''; }
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
    var currentAct: String? { let a = call("__act")?.toString() ?? ""; return a.isEmpty ? nil : a }
    var fidgeting: Bool { call("__fid")?.toBool() ?? false }
    var state: String { call("__state")?.toString() ?? "" }
    /// A text that is the same whenever the picture is the same (the picture is a pure function of the spec and the palette); far cheaper than building and parsing it.
    func frameKey() -> String? { call("__key")?.toString() }
    /// advance one tick and return the key, in one JS call
    func step() -> String? {
        guard let r = call("__step")?.toString() else { return nil }
        let u = Array(r.utf8.suffix(5)), ok = u.count == 5
        holding = ok && u[0] == 49; bookClip = ok && u[1] == 49; reading = ok && u[2] == 49; directing = ok && u[3] == 49; calling = ok && u[4] == 49
        return r
    }
    /// the director (what Claude is doing, shown on his face). `directing`: a clip of the director is on screen; `calling`: it is a call (turn finished, question, permission).
    private(set) var directing = false, calling = false
    /// the book: he holds it (the real one is hidden), a book clip is on show (drawn with the book on his right), a reading session is on
    private(set) var holding = false, bookClip = false, reading = false
    func dropBook() -> Bool { call("__dropBook")?.toBool() ?? false }
    func startReading() { _ = call("__read") }
    func feed(_ lines: String) { _ = call("__ev", [lines]) }
    /// What is on show: the director's clip, the pet's act, the last click reaction (for the scenario trace).
    func now() -> (clip: String?, act: String?, poke: String?)? {
        guard let s = call("__now")?.toString(), let d = s.data(using: .utf8), let a = try? JSONSerialization.jsonObject(with: d) as? [Any], a.count == 3 else { return nil }
        return (a[0] as? String, a[1] as? String, a[2] as? String)
    }
    func setFront(_ on: Bool) { _ = call("__front", [on]) }
    func setReduceMotion(_ on: Bool) { _ = call("__rm", [on]) }
    func setAmbient(_ level: String) { _ = call("__amb", [level]) }
    func touch() { _ = call("__touch") }
    /// A walking clip's body shift this tick, in art px (nil: no walking clip): the app moves him by its change.
    func walkDx() -> Int? { guard directing, let v = call("__walkDx")?.toInt32(), v != -999 else { return nil }; return Int(v) }
    func setPerch(_ p: (on: String, claude: Bool, nearClaude: Bool), book: Bool = false, libreta: Bool = false) {
        _ = call("__perch", ["{\"on\":\"\(p.on)\",\"claude\":\(p.claude),\"nearClaude\":\(p.nearClaude),\"book\":\(book),\"libreta\":\(libreta)}"])
    }
    /// A newer Budgit is waiting (its version) or none is (nil): he calls once per version; a click on the call answers it.
    func setUpdate(_ v: String?) { _ = call("__upd", [v.map { $0 as Any } ?? NSNull()]) }
    /// The data line changed: his reactions to the book's items and the week. true: one plays.
    @discardableResult
    func setFeed(clips: [String], done: Int, quiet: Bool, runsOut: Bool) -> Bool {
        let o: Dict = ["clips": clips, "done": done, "quiet": quiet, "runsOut": runsOut]
        guard let d = try? JSONSerialization.data(withJSONObject: o), let j = String(data: d, encoding: .utf8) else { return false }
        return call("__feedInfo", [j])?.toBool() ?? false
    }
    func setIdle(seconds: Int) { _ = call("__idle", [seconds]) }
    func setBattery(low: Bool) { _ = call("__battery", [low]) }
    func dismissUpdate() { _ = call("__updDone") }
    /// The call on show, for the click: its kind (`done`, `ask`, `perm`, `update`) and session prefix.
    func pendingCall() -> (kind: String, sid: String)? {
        guard let t = call("__call")?.toString(), let sp = t.firstIndex(of: " ") else { return nil }
        return (String(t[..<sp]), String(t[t.index(after: sp)...]))
    }
    /// A body clip the app plays while it moves him (a fall, a climb, a flight, riding a window). It wins over the director's own clips; `loop` repeats a stretch of it until `stopClip`.
    @discardableResult
    func play(_ id: String, loop: (Int, Int)? = nil) -> Bool { call("__play", [id, loop?.0 ?? -1, loop?.1 ?? -1])?.toBool() ?? false }
    func stopClip() { _ = call("__stopClip") }
    /// Quick clicks: he laughs (true: the tickle clip started, the act on show gives way to it); `untickle` ends it for the grumpy act.
    @discardableResult
    func tickle() -> Bool { call("__tickle", [1])?.toBool() ?? false }
    func untickle() { _ = call("__tickle", [0]) }
    var tickling: Bool { call("__tickle", [-1])?.toBool() ?? false }
    /// The book was thrown far: `sit` false is the startled "!" before he sets off (no calm after it), true is sitting down beside it, happy.
    @discardableResult
    func fetch(sit: Bool) -> Bool { call("__fetch", [sit])?.toBool() ?? false }
    /// A turn of Claude's is open.
    var working: Bool { call("__working")?.toBool() ?? false }
    /// The 6 s after a clip, an act or a fidget ends: he only breathes and blinks, so he does not set off walking either.
    var calm: Bool { call("__calm")?.toBool() ?? false }
    /// A click on an agotado Brote: true when the director answers it (1 neutral, 2 annoyed, 3+ angry and away from the pointer); `away` is -1 or 1.
    func pokeAgotado(away: Int) -> Bool { call("__poke", [away])?.toBool() ?? false }
    /// The picture and whether the arm is the pointing one, from one call: `{"g": grid, "pt": bool}`.
    func frameInfo() -> (grid: Grid, pointing: Bool)? {
        guard let t = call("__frame")?.toString(), let d = t.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: d)) as? Dict,
              let rows = o["g"] as? [[Any]], var g = Grid(rows: rows) else { return nil }
        g.glyphs = ((o["gl"] as? [[Any]]) ?? []).map { $0.compactMap { ($0 as? NSNumber)?.intValue } }
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
        if on && bridge.currentAct == nil && !held && bridge.state != "dormido" && !bridge.reading { bridge.act("hello") }
    }
    var busy: Bool { bridge.currentAct != nil || bridge.fidgeting || bridge.directing || hover || held }
    @discardableResult
    func click(now: Double, away: Int = -1) -> Click {
        let c = pokes.click(now: now, dormido: bridge.state == "dormido")
        bridge.touch()
        if bridge.dropBook() { return c }                                                        // he was reading: he drops the book, startled
        if c != .countedOnly, bridge.state == "agotado", bridge.pokeAgotado(away: away) { return c }      // his own answer to pokes when worn out
        switch c {
        case .grumpy: bridge.untickle(); bridge.act("grumpy")
        case .tickle: if !bridge.tickle() { bridge.act("poke") }
        case .tickling: if !bridge.tickling { bridge.act("poke") }                                // he could not laugh (a call shows): each click pokes
        case .poke: bridge.act("poke")
        case .countedOnly: break
        }
        return c
    }
    /// The Libreta opened: he shows the book, proud (not while he reads: he puts it back instead, nor held or asleep).
    @discardableResult
    func libretaOpened() -> Bool {
        guard !bridge.reading, !held, bridge.state != "dormido" else { return false }
        return bridge.play("bk_present")
    }
    func tick() { bridge.tick() }
    /// One 10 Hz step: the settings if they changed, then one tick and the picture's key in a single JS call.
    func step() -> String? { push(); return bridge.step() }
}

// MARK: - the README picture: a scripted scene, recorded off screen
/// The scene of `--record`: a flat drawn mock with two generic windows, no real window, no screen capture. He walks on the chat window's top,
/// a second window opens over his spot (contento, too high to hop: he falls), he bounces on the floor and sits at his laptop when a turn
/// starts, and the reply grows in the chat while he types. Real physics (stepFlyOver), the real sprite runtime and the real director;
/// only the clock and the window list are scripted.
enum Rec {
    static let size = Pt(480, 300), zoom = 2.0                                         // a close crop of the desktop, drawn at 2x: 960 x 600 px
    static let floorY = 16.0, frameDelay = 0.07, frames = 85                           // he lands on a floor strip; 85 x 0.07 s = 5.95 s, 14.3 fps
    static let blend = 6                                                               // the last 6 frames fade into the first, so the loop has no seam
    static let claude = SWin(id: 1, r: Rc(92, 50, 236, 96))                            // a chat window, its top at 146
    static let cover = SWin(id: 2, r: Rc(196, 48, 268, 226))                           // a browser window opens over him; its top is 128 pt above his feet
    static let start = Pt(110, 146), coverAt = 1.6, coverGrow = 0.2                    // the window zooms open in 0.2 s, then he notices
    /// The opening window at time t: it grows from its centre with an ease-out; nil before it opens.
    static func coverRect(at t: Double) -> Rc? {
        guard t >= coverAt else { return nil }
        let k = 1 - pow(1 - min(1, (t - coverAt) / coverGrow), 3), r = cover.r, s = 0.6 + 0.4 * k
        return Rc(r.x + r.w * (1 - s) / 2, r.y + r.h * (1 - s) / 2, r.w * s, r.h * s)
    }
}
struct RecFrame { var grid: Grid; var flip: Bool; var pos: Pt; var cover: Rc?; var key: String; var t: Double; var work: Double? }   // work: seconds since he sat to type

func recordScene(bridge: PetBridge) -> [RecFrame] {
    _ = bridge.ctx.evaluateScript("Math.random = (function(){ var s = 7; return function(){ s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; }; })()")   // the same GIF every run
    let glue = PetGlue(bridge: bridge)
    glue.setData(state: "contento", h5: 0, stale: 0)
    bridge.setAmbient("quieto")
    let dt = 1.0 / 60, bounds = Bounds(minX: 0, maxX: Rec.size.x - K.spriteW, floorY: Rec.floorY, ceilY: Rec.size.y - K.spriteH)
    let screens = [Rc(0, 0, Rec.size.x, Rec.size.y)]
    var pos = Rec.start, body: Body?, fallAt: Double?, coverOpen = false, workAt: Double?
    var out: [RecFrame] = [], key = "", t = 0.0, nextTick = 0.0, nextFrame = 0.0
    while out.count < Rec.frames {
        if t >= Rec.coverAt { glue.walking = false }
        if !coverOpen && t >= Rec.coverAt + Rec.coverGrow {
            coverOpen = true
            if case .fall = coverMove(state: "contento", feet: pos.y, coverTop: Rec.cover.r.maxY) { _ = bridge.play("sf_fall_cover"); fallAt = t + K.fallPause }
        }
        if let f = fallAt, t >= f { fallAt = nil; body = Body(p: pos, v: Pt(0, 0)) }
        if var b = body {
            let plats = exposedTops(coverOpen ? [Rec.cover, Rec.claude] : [Rec.claude], screens: screens)
            let r = stepFlyOver(&b, dt: dt, in: bounds, plats: plats)
            pos = b.p; body = b
            if r.flight == .landed {
                body = nil
                bridge.stopClip(); bridge.act("land")
                bridge.feed("prompt rec\npre rec Edit\n")                                 // a turn starts: he sits at his laptop
                workAt = t
            }
        }
        if t >= nextTick - 1e-9 {
            nextTick += 0.1
            if t < Rec.coverAt { glue.walking = true; pos.x += Stroll.pace("contento") ?? 0 }
            key = glue.step() ?? key
        }
        if t >= nextFrame - 1e-9 {
            nextFrame += Rec.frameDelay
            if let g = bridge.frame() { out.append(RecFrame(grid: g, flip: false, pos: pos, cover: Rec.coverRect(at: t), key: key, t: t, work: workAt.map { t - $0 })) }
        }
        t += dt
    }
    return out
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
        let streak = (0..<10).map { p.click(now: Double($0) * 0.5, dormido: false) }
        check(streak == [.poke, .poke, .poke, .tickle, .tickling, .tickling, .tickling, .tickling, .tickling, .grumpy], "quick clicks: three pokes, tickles at four, grumpy at ten (\(streak))")
        check(p.click(now: 5.2, dormido: false) == .poke, "the streak starts over after grumpy")
        var q = PokeCounter()
        _ = q.click(now: 0.0, dormido: false); _ = q.click(now: 0.5, dormido: false); _ = q.click(now: 1.0, dormido: false)
        check(q.click(now: 2.0, dormido: false) == .poke, "a click 1 s after the last starts a new streak")
        var d = PokeCounter()
        check(d.click(now: 0, dormido: true) == .countedOnly, "dormido: no poke act")
        for i in 1..<9 { check(d.click(now: Double(i) * 0.2, dormido: true) == .countedOnly, "dormido: no tickle either") }
        check(d.click(now: 1.8, dormido: true) == .grumpy, "dormido: ten quick clicks still make him grumpy")
    }
    do { // through the real pet
        let g = PetGlue(bridge: bridge)
        g.setData(state: "contento", h5: 0, stale: 0)
        check(g.click(now: 10) == .poke && bridge.currentAct == "poke", "click: the pet pokes")
        do { // a click answers with one of five reactions, never the same twice in a row
            let kinds = (0..<60).compactMap { _ -> String? in bridge.act("poke"); return bridge.ctx.evaluateScript("__p.poke")?.toString() }
            check(kinds.count == 60 && zip(kinds, kinds.dropFirst()).allSatisfy { $0 != $1 }, "pokes: never the same reaction twice in a row")
            check(Set(kinds) == Set(["hearts", "hop", "wave", "sparkle", "spin"]), "pokes: all five reactions show up (\(Set(kinds).sorted()))")
            let cheeky = bridge.ctx.evaluateScript("(function(){ var S = new Pet('contento').spec(); S.eyes = 'heart'; S.cheeks = 'C'; var a = JSON.stringify(compose(S, PALS.contento)); S.cheeks = null; return a === JSON.stringify(compose(S, PALS.contento)); })()")?.toBool() ?? false
            check(cheeky, "heart eyes never draw cheeks (they ran together)")
        }
        _ = g.click(now: 10.1); _ = g.click(now: 10.2)
        check(g.click(now: 10.3) == .tickle && bridge.currentAct == nil && bridge.tickling, "four quick clicks: the poke gives way to the tickle clip")
        for i in 4..<9 { check(g.click(now: 10 + Double(i) * 0.1) == .tickling && bridge.tickling && bridge.currentAct == nil, "more clicks: he keeps laughing") }
        check(g.click(now: 10.9) == .grumpy && bridge.currentAct == "grumpy" && !bridge.tickling, "the tenth: the tickle ends and the pet is grumpy")
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

    // strolling (the whole surface, a rest between walks, the pace by mood)
    do {
        var seq: [Double] = [0.9], i = 0
        let rng = { () -> Double in defer { i += 1 }; return i < seq.count ? seq[i] : 0.5 }
        var s = Stroll(); s.rest = 0; var x = 500.0
        s.tick(state: "contento", busy: false, x: &x, lo: 0, hi: 1000, rng: rng)
        check(s.target == 900 && !s.walking, "stroll: a target anywhere on the surface")
        s.tick(state: "contento", busy: false, x: &x, lo: 0, hi: 1000, rng: rng)
        check(near(x, 503) && s.walking && !s.facingLeft, "contento walks 3 pt per tick")
        seq = [0.62]; i = 0
        var short = Stroll(); short.rest = 0; var xs2 = 600.0
        short.tick(state: "contento", busy: false, x: &xs2, lo: 0, hi: 1000, rng: rng)
        check(short.target == 0, "a walk shorter than 60 pt goes to the farther end instead (\(short.target ?? -1))")
        for (st, v) in [("radiante", 4.5), ("justo", 3.0), ("cansado", 1.5), ("agotado", 1.5)] {
            var r = Stroll(); r.target = 900; var xr = 500.0
            r.tick(state: st, busy: false, x: &xr, lo: 0, hi: 1000, rng: rng)
            check(near(xr, 500 + v), "\(st) walks \(v) pt per tick")
        }
        for st in ["enfermo", "dormido", "unknown"] {
            var t = Stroll(); t.target = 900; var xt = 500.0
            t.tick(state: st, busy: false, x: &xt, lo: 0, hi: 1000, rng: rng)
            check(xt == 500 && !t.walking && t.target == nil, "\(st): never walks")
        }
        var arrive = Stroll(); arrive.target = 502; var xa = 500.0
        seq = [0.5]; i = 0
        arrive.tick(state: "contento", busy: false, x: &xa, lo: 0, hi: 1000, rng: rng)
        check(xa == 502 && arrive.target == nil && !arrive.walking && !arrive.facingLeft && arrive.rest == 55, "arrives exactly, faces front and rests 3-8 s (\(arrive.rest))")
        var left = Stroll(); left.target = 400; var xl = 500.0
        left.tick(state: "contento", busy: false, x: &xl, lo: 0, hi: 1000, rng: rng)
        check(xl < 500 && left.facingLeft, "walking left faces left")
        var busy = Stroll(); busy.target = 900; var xb = 500.0
        busy.tick(state: "contento", busy: true, x: &xb, lo: 0, hi: 1000, rng: rng)
        check(xb == 500 && !busy.walking && busy.target == 900, "a gesture pauses the walk and keeps where he was going")
        var narrow = Stroll(); narrow.rest = 0; var xn = 10.0
        narrow.tick(state: "contento", busy: false, x: &xn, lo: 0, hi: 40, rng: rng)
        check(narrow.target == nil, "a ledge shorter than one walk: he stays")
        // a long walk covers the surface, never leaves it, and rests between walks
        var n = 0, lo = 1e9, hi = -1e9, still = 0
        var seed: UInt64 = 7
        let lcg = { () -> Double in seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Double(seed >> 11) / Double(1 << 53) }
        var wk = Stroll(); var xk = 500.0
        while n < 20000 { wk.tick(state: "contento", busy: false, x: &xk, lo: 200, hi: 800, rng: lcg); lo = min(lo, xk); hi = max(hi, xk); if !wk.walking { still += 1 }; n += 1 }
        check(lo >= 200 && hi <= 800 && lo < 260 && hi > 740, "20000 ticks reach both ends of 200...800 and never leave it (\(lo)...\(hi))")
        check(still > 2000 && still < 12000, "he rests between walks (\(still) still ticks of 20000)")
    }

    // placement
    let main = Rc(0, 0, 1440, 875), side = Rc(1440, -100, 1920, 1055)
    let claude = Rc(200, 100, 900, 600)
    let cw = SWin(id: 7, r: claude), cplat = Plat(win: 7, x0: 200, x1: 1100, y: 700)
    do {
        let st = MemStore()
        let l = Layout(store: st, screens: [main], claude: claude, claudeID: 7)
        check(l.pos == Pt(1440 - 102 - 16, 0), "nothing saved: bottom-right of the main screen")
        l.land(at: Pt(300, 700), on: cplat, wins: [cw])
        check(l.anchor == Anchor(relX: 100, perchY: 600) && l.host?.id == 7 && l.pos == Pt(300, 700), "landing on Claude's top anchors there")
        check(st.number("offsetX") == 100 && st.number("perchY") == 600, "landing on Claude's top saves offsetX / perchY")
        // Claude moves: he rides along by the delta of its top-left corner
        l.claudeChanged(to: Rc(260, 140, 900, 600), id: 7)
        check(l.pos == Pt(360, 740), "Claude moved by (60, 40): he moves by (60, 40) (\(l.pos))")
        l.claudeChanged(to: Rc(260, 190, 1000, 550), id: 7)
        check(l.pos == Pt(360, 740), "a width change or a resize from the bottom keeps him on the top edge")
        l.claudeChanged(to: Rc(260, 170, 1000, 550), id: 7)
        check(l.pos == Pt(360, 720), "the top edge moving down takes him down with it")
        l.claudeChanged(to: Rc(1400, 120, 900, 650), id: 7)
        check(l.pos.x <= 1440 - 102 && l.pos.x >= 0, "Claude half off-screen: he stays on screen (\(l.pos.x))")
        l.claudeChanged(to: Rc(260, 170, 1000, 550), id: 7)
        check(l.pos == Pt(360, 720), "Claude off-screen and back: he returns to his spot, not to the clamped one (\(l.pos))")
        // the saved offset is only written by a landing
        let w0 = st.writes
        _ = l.beginThrow(from: Pt(400, 300))
        var sg = Stroll(); sg.target = 450; var xs = l.pos.x
        sg.tick(state: "contento", busy: false, x: &xs, lo: l.ledge().lo, hi: l.ledge().hi, rng: { 0.9 })
        check(st.writes == w0, "a throw and a stroll step never write the store")
        check(l.anchor == nil && l.host == nil, "a throw leaves the window he stood on")
        let fb = l.flightBounds()
        check(fb.floorY == 0 && fb.minX == 0 && fb.maxX == 1440 - 102 && fb.ceilY == 875 - 84, "flight bounds: the screen (window tops are floors on the way)")
        l.land(at: Pt(1300, 0), on: nil, wins: [cw])
        check(l.anchor == nil && st.number("offsetX") == nil && st.number("perchY") == nil, "landing on the floor clears the saved offset")
        check(st.number("lastX") == 1300 && st.number("lastY") == 0, "landing on the floor saves lastX / lastY")
        l.claudeChanged(to: Rc(0, 0, 900, 700), id: 7)
        check(l.pos == Pt(1300, 0), "on the floor: Claude's moves do not move him")
        let web = SWin(id: 9, r: Rc(1000, 100, 400, 500))
        l.land(at: Pt(1100, 600), on: Plat(win: 9, x0: 1000, x1: 1400, y: 600), wins: [web, cw])
        check(l.host?.id == 9 && st.number("offsetX") == nil && st.number("lastX") == 1100, "landing on another window: it is his host, the spot is saved, not Claude's offset")
        l.hostChanged(to: Rc(900, 150, 400, 500))
        check(l.pos == Pt(1000, 650), "he rides any window he stands on")
    }
    do { // restore
        let st = MemStore(); st.setNumber("offsetX", 120); st.setNumber("perchY", 50)
        let l = Layout(store: st, screens: [main], claude: claude, claudeID: 7)
        check(l.pos == Pt(320, 700) && l.host?.id == 7, "restart with a saved offset puts him on Claude's top edge")
        let none = Layout(store: st, screens: [main], claude: nil)
        check(none.pos == Pt(1440 - 102 - 16, 0), "offset saved, no Claude, no spot: the corner")
        let s2 = MemStore(); s2.setNumber("lastX", 700); s2.setNumber("lastY", 300)
        check(Layout(store: s2, screens: [main], claude: claude).pos == Pt(700, 300), "restart with a saved absolute spot")
        none.claudeChanged(to: claude, id: 7)
        check(none.pos == Pt(320, 700) && none.host?.id == 7, "Claude appearing snaps a saved offset onto its top")
    }
    do { // the ledge he may walk on
        let l = Layout(store: MemStore(), screens: [main], claude: claude, claudeID: 7)
        l.plats = [Plat(win: 7, x0: 200, x1: 600, y: 800), Plat(win: 7, x0: 900, x1: 1100, y: 800)]
        l.land(at: Pt(300, 800), on: l.plats[0], wins: [cw])
        let r = l.ledge()
        check(r.lo == 200 - 51 && r.hi == 600 - 51, "walking stays on his open stretch of the top: centre within it (\(r))")
        l.leave()
        check(l.ledge().lo == 0 && l.ledge().hi == 1440 - 102, "on the floor: the whole screen")
    }
    do { // screens
        check(screenIndex(for: Pt(100, 100), in: [main, side]) == 0 && screenIndex(for: Pt(2000, 100), in: [main, side]) == 1, "screen of a point")
        check(screenIndex(for: Pt(-50, 2000), in: [main, side]) == 0, "a point off every screen: the nearest")
        let l = Layout(store: MemStore(), screens: [main, side], claude: nil)
        l.land(at: Pt(2000, 300), on: nil, wins: [])
        check(l.pos == Pt(2000, 300) && l.visible(for: l.pos) == side, "a landing on the second screen stays there")
        l.land(at: Pt(3300, 2000), on: nil, wins: [])
        check(l.pos.x <= 3360 - 102 && l.pos.y <= 955 - 84, "clamped into the second screen")
        let gap = Layout(store: MemStore(), screens: [Rc(0, 0, 800, 600), Rc(1000, 400, 800, 600)], claude: nil)
        gap.land(at: Pt(850, 100), on: nil, wins: [])
        check(gap.pos.x >= 0 && gap.pos.x <= 800 - 102, "the gap between screens never loses him (\(gap.pos.x))")
    }
    do { // window tops
        let H = 875.0
        let list = [WinInfo(pid: 9, layer: 0, bounds: Rc(600, 100, 500, 600), id: 2),          // in front
                    WinInfo(pid: 5, layer: 0, bounds: Rc(200, 175, 900, 600), id: 7),          // Claude, behind it
                    WinInfo(pid: 1, layer: 0, bounds: Rc(0, 0, 300, 300), id: 3, alpha: 0),     // invisible
                    WinInfo(pid: 1, layer: 3, bounds: Rc(0, 0, 300, 300), id: 4),               // floating level
                    WinInfo(pid: 42, layer: 0, bounds: Rc(0, 0, 300, 300), id: 5),              // ours
                    WinInfo(pid: 1, layer: 0, bounds: Rc(0, 0, 100, 300), id: 6)]               // too narrow
        let ws = surfaceWindows(list, ownPID: 42, primaryHeight: H)
        check(ws.map { $0.id } == [2, 7] && ws[1].r == Rc(200, 100, 900, 600), "surface windows: layer 0, visible, not ours, not tiny, flipped, front to back")
        let tops = exposedTops(ws, screens: [main])
        check(tops.contains(Plat(win: 2, x0: 600, x1: 1100, y: 775)), "the front window's top is open")
        check(tops.contains(Plat(win: 7, x0: 200, x1: 600, y: 700)) && !tops.contains { $0.win == 7 && $0.x1 > 600 }, "a window in front hides its stretch of the top behind it")
        let mid = exposedTops([SWin(id: 1, r: Rc(500, 0, 100, 800)), SWin(id: 7, r: Rc(200, 100, 900, 600))], screens: [main])
        check(mid.filter { $0.win == 7 } == [Plat(win: 7, x0: 200, x1: 500, y: 700), Plat(win: 7, x0: 600, x1: 1100, y: 700)], "a window across the middle splits the top in two")
        let level = exposedTops([SWin(id: 1, r: Rc(500, 100, 300, 600)), SWin(id: 7, r: Rc(200, 100, 900, 600))], screens: [main])
        check(level.filter { $0.win == 7 } == [Plat(win: 7, x0: 200, x1: 1100, y: 700)], "a window whose top is level with it does not hide it")
        let sliver = exposedTops([SWin(id: 1, r: Rc(240, 0, 900, 800)), SWin(id: 7, r: Rc(200, 100, 900, 600))], screens: [main])
        check(!sliver.contains { $0.win == 7 }, "a stretch narrower than his body is not a ledge")
        let tall = exposedTops([SWin(id: 7, r: Rc(200, 0, 900, 850))], screens: [main])
        check(tall.isEmpty, "a top with no room above him (under the menu bar) is not a ledge")
        check(fullScreenApp([SWin(id: 1, r: Rc(0, 0, 1440, 900))], displays: [Rc(0, 0, 1440, 900)]) && !fullScreenApp(ws, displays: [Rc(0, 0, 1440, 900)]), "a window the size of a display is a full-screen app")
        // support
        check(supportUnder(cx: 400, feet: 900, plats: tops)?.win == 7 && supportUnder(cx: 800, feet: 900, plats: tops)?.win == 2, "support: the highest top under his centre")
        check(supportUnder(cx: 400, feet: 650, plats: tops) == nil && supportUnder(cx: 1300, feet: 900, plats: tops) == nil, "support: none below his feet or under his centre is the floor")
        let settled = [Plat(win: 9, x0: 420, x1: 900, y: 560), Plat(win: 8, x0: 300, x1: 420, y: 500)]
        check(landingTop(cx: 551, feet: 559, plats: settled, target: 9)?.y == 560, "a trip still lands when the new window settled a point higher during the hop")
        check(landingTop(cx: 551, feet: 540, plats: settled, target: 9) == nil && landingTop(cx: 551, feet: 559, plats: [], target: 9) == nil, "a trip whose window moved away or closed ends in a fall")
        check(supportCheck(host: 7, centre: 400, wins: ws) == .stay, "his window still holds him")
        check(supportCheck(host: 9, centre: 400, wins: ws) == .gone, "his window closed, minimised or on another Space: gone")
        check(supportCheck(host: 7, centre: 1200, wins: ws) == .slid, "his window moved out from under him: slid")
        check(supportCheck(host: 7, centre: 800, wins: ws) == .covered(by: 2), "a window opened over his spot: covered")
        // the B rule: mood and height, never dice
        check(coverMove(state: "radiante", feet: 700, coverTop: 800) == .hop && coverMove(state: "radiante", feet: 700, coverTop: 900) == .fly, "radiante: onto the new window, a hop when close, a flight when high")
        check(coverMove(state: "contento", feet: 700, coverTop: 800) == .hop && coverMove(state: "contento", feet: 700, coverTop: 900) == .fall, "contento: a hop when close, else he falls")
        check(["justo", "cansado", "agotado", "enfermo", "dormido"].allSatisfy { coverMove(state: $0, feet: 700, coverTop: 720) == .fall }, "the other moods fall")
        check((0..<5).allSatisfy { _ in coverMove(state: "contento", feet: 700, coverTop: 800) == .hop }, "the same case always gives the same answer")
    }
    do { // flight over window tops
        let bd = Bounds(minX: 0, maxX: 1338, floorY: 0, ceilY: 791)
        let ledge = [Plat(win: 7, x0: 200, x1: 600, y: 300)]
        var b = Body(p: Pt(300, 500), v: Pt(0, 0)); var hits = 0, firstAt = -1, n = 0
        var f = Flight.flying
        while f == .flying && n < 600 { let r = stepFlyOver(&b, dt: 1.0 / 60, in: bd, plats: ledge); f = r.flight; if r.hit { hits += 1; if firstAt < 0 { firstAt = n } }; n += 1 }
        check(f == .landed && b.p.y == 300, "a fall lands on the window top under him (\(b.p.y))")
        check(hits >= 1 && firstAt >= 0 && firstAt < 40, "the first touch of a floor is reported (step \(firstAt))")
        var up = Body(p: Pt(300, 100), v: Pt(0, 1400)); var top = 0.0; n = 0; f = .flying
        while f == .flying && n < 600 { f = stepFlyOver(&up, dt: 1.0 / 60, in: bd, plats: ledge).flight; top = max(top, up.p.y); n += 1 }
        check(top > 300 && up.p.y == 300, "thrown up through a top he passes it, then lands on it on the way down")
        var miss = Body(p: Pt(800, 500), v: Pt(0, 0)); n = 0; f = .flying
        while f == .flying && n < 600 { f = stepFlyOver(&miss, dt: 1.0 / 60, in: bd, plats: ledge).flight; n += 1 }
        check(miss.p.y == 0, "outside the top's stretch he falls to the floor")
        var off = Body(p: Pt(500, 300), v: Pt(400, 0)); n = 0; f = .flying
        while f == .flying && n < 600 { f = stepFlyOver(&off, dt: 1.0 / 60, in: bd, plats: ledge).flight; n += 1 }
        check(off.p.y == 0 || off.p.x + 51 <= 600, "sliding off the end of a top he drops (\(off.p))")
        var d = Dragger()
        d.fly(Body(p: Pt(300, 600), v: Pt(0, -900)), in: bd, dizzy: true, plats: ledge)
        var firsts = 0, landedTick: Dragger.FlightTick?
        for _ in 0..<600 { guard let t = d.step(dt: 1.0 / 60) else { break }; if t.firstHit { firsts += 1 }; if t.landed { landedTick = t } }
        check(firsts == 1 && landedTick?.body.p.y == 300 && landedTick?.dizzy == true, "the dragger reports the first impact once (dizzy starts there) and lands on the top")
        var w = Dragger(), wallAt: Double?, floorAt: Double?, tt = 0.0
        w.fly(Body(p: Pt(800, 500), v: Pt(1800, 300)), in: bd, dizzy: true)
        for _ in 0..<900 { guard let t = w.step(dt: 1.0 / 60) else { break }; tt += 1.0 / 60
            if t.firstHit, wallAt == nil { wallAt = tt; check(t.body.p.x == bd.maxX, "the first bump of a hard sideways throw is the screen side (\(t.body.p))") }
            if t.body.p.y == bd.floorY, floorAt == nil { floorAt = tt } }
        check(wallAt != nil && floorAt != nil && wallAt! < floorAt!, "the stars start at the wall, before he reaches the floor (\(String(describing: wallAt)) < \(String(describing: floorAt)))")
        var drop = Body(p: Pt(300, 300), v: Pt(0, 0)), bumps = 0
        for _ in 0..<300 { if stepFlyOver(&drop, dt: 1.0 / 60, in: bd, plats: []).bump { bumps += 1 } }
        check(bumps >= 1 && drop.p.y == 0, "a plain drop bumps on the floor")
    }
    do { // the way up ()
        let win = Rc(600, 0, 500, 400)
        let low = Plat(win: 2, x0: 600, x1: 1100, y: 330)
        let r1 = planRoute(from: Pt(300, 300), lo: 0, hi: 1338, to: low, win: win, wantX: 700)
        check(r1 == [.walk(x: 700), .hop(to: Pt(700, 330))], "a top up to 40 pt higher: walk and hop (\(r1))")
        let mid = Plat(win: 2, x0: 600, x1: 1100, y: 400)
        let r2 = planRoute(from: Pt(300, 300), lo: 0, hi: 1338, to: mid, win: win, wantX: 700)
        check(r2 == [.walk(x: 518.0), .climb(y: 400, left: true), .hop(to: Pt(700, 400))], "up to 126 pt with the side within reach: climb (\(r2))")
        let hiWin = Rc(600, 0, 500, 600), high = Plat(win: 2, x0: 600, x1: 1100, y: 600)
        let r3 = planRoute(from: Pt(300, 300), lo: 0, hi: 1338, to: high, win: hiWin, wantX: 700)
        check(r3.count == 3 && { if case .ladder(600, true) = r3[1] { return true }; return false }(), "up to 360 pt: a ladder (\(r3))")
        let floating = Rc(600, 380, 500, 50), fl = Plat(win: 2, x0: 600, x1: 1100, y: 430)
        let r4 = planRoute(from: Pt(300, 300), lo: 0, hi: 1338, to: fl, win: floating, wantX: 700)
        check({ if case .ladder = r4[1] { return true }; return false }(), "a window that does not reach down to him: ladder, not climb")
        let sky = Plat(win: 2, x0: 600, x1: 1100, y: 800)
        check(planRoute(from: Pt(300, 300), lo: 0, hi: 1338, to: sky, win: Rc(600, 0, 500, 800), wantX: 700) == [.fly(to: Pt(700, 800))], "higher than 360 pt: he flies")
        check(planRoute(from: Pt(300, 300), lo: 250, hi: 450, to: mid, win: win, wantX: 700) == [.fly(to: Pt(700, 400))], "no side within reach on his ledge: he flies")
        check(planRoute(from: Pt(0, 300), lo: 0, hi: 1338, to: Plat(win: 2, x0: 1000, x1: 1400, y: 300), win: Rc(1000, 0, 400, 300), wantX: 1200) == [.fly(to: Pt(1200, 300))], "more than 500 pt away: he flies")
        let down = planRoute(from: Pt(300, 700), lo: 200, hi: 500, to: Plat(win: 3, x0: 700, x1: 1000, y: 300), win: Rc(700, 0, 300, 300), wantX: 800)
        check(down == [.walk(x: 500), .hop(to: Pt(800, 300))], "a lower top: walk to the end of his ledge and hop down (\(down))")
        check(planRoute(from: Pt(700, 300), lo: 0, hi: 1338, to: mid, win: win, wantX: 9999).last == .hop(to: Pt(1048, 400)), "the landing spot keeps his centre on the top")
        var rt = Route(r2); var p = Pt(300, 300); var clips = Set<String>(); var n = 0
        while !rt.done && n < 2000 { if let l = rt.leg, let c = Route.clip(l) { clips.insert(c) }; rt.step(&p, dt: 0.1); n += 1 }
        check(rt.done && p == Pt(700, 400) && clips == ["sf_climb"], "a route ends exactly on the top, with the climb clip on the way (\(p))")
        let walkSecs = Double(n) * 0.1
        check(walkSecs > 5 && walkSecs < 12, "walk 218 pt, climb 100 pt, hop: about 7 s (\(walkSecs))")
        var fr = Route([.fly(to: Pt(700, 800))]); var q = Pt(300, 300); var above = false
        while !fr.done { fr.step(&q, dt: 1.0 / 60); if q.y > 800 { above = true } }
        check(q == Pt(700, 800) && above, "a flight arcs over the target and lands on it")
        var opening = Route([.fly(to: Pt(500, 744))]), flyer = Pt(500, 500)
        for _ in 0..<20 { opening.step(&flyer, dt: 1.0 / 60) }
        opening.retarget(y: 750)
        for _ in 0..<600 where !opening.done { opening.step(&flyer, dt: 1.0 / 60) }
        check(opening.done && flyer == Pt(500, 750), "a flight onto a window that is still opening ends on its settled top")
        check(Route([.climb(y: 400, left: true)]).mirrored(at: Pt(0, 0)) && !Route([.ladder(y: 400, left: false)]).mirrored(at: Pt(0, 0)), "climbing he faces the wall (the art has it on his left)")
        check(Route([.walk(x: 100)]).mirrored(at: Pt(300, 0)) && !Route([.fly(to: Pt(500, 0))]).mirrored(at: Pt(300, 0)), "walking or flying left mirrors him")
    }
    do { // trips ()
        var e = Explorer()
        check(e.mayLeave(state: "contento", idle: true, working: false, now: 100) && e.mayLeave(state: "radiante", idle: true, working: false, now: 100), "contento or radiante, idle, nobody working: he may go")
        check(!e.mayLeave(state: "justo", idle: true, working: false, now: 100) && !e.mayLeave(state: "cansado", idle: true, working: false, now: 100), "other moods stay")
        check(!e.mayLeave(state: "contento", idle: false, working: false, now: 100) && !e.mayLeave(state: "contento", idle: true, working: true, now: 100), "busy or Claude working: he stays")
        e.lastTrip = 50
        check(!e.mayLeave(state: "contento", idle: true, working: false, now: 100) && e.mayLeave(state: "contento", idle: true, working: false, now: 141), "one trip every 90 s at most")
        e.awaySince = 200
        check(!e.mayLeave(state: "contento", idle: true, working: false, now: 1000), "away already: no second trip")
        check(e.shouldReturn(working: true, now: 210) && !e.shouldReturn(working: false, now: 210) && e.shouldReturn(working: false, now: 261), "back home when Claude works or after 60 s")
        let ps = [Plat(win: 7, x0: 0, x1: 900, y: 700), Plat(win: 2, x0: 0, x1: 300, y: 500), Plat(win: 3, x0: 0, x1: 500, y: 400), Plat(win: 4, x0: 0, x1: 80, y: 300)]
        check(pickTrip(ps, host: 7, claude: 7)?.win == 3 && pickTrip(ps, host: 3, claude: 7)?.win == 2, "a trip goes to the widest other top that is not Claude's")
        check(pickTrip([ps[0], ps[3]], host: 7, claude: 7) == nil, "nowhere wide enough: no trip")
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

    // He lives "with Claude" prebuilt in the plugin: HOME from the environment, one instance, live sessions, the plugin root
    do { // HOME and the config folder come from the environment, never from NSHomeDirectory() unless HOME is unusable
        check(brHome(env: ["HOME": "/Users/x"]) == "/Users/x", "brHome: HOME from the environment")
        check(brHome(env: ["HOME": "/Users/x/"]) == "/Users/x" && brHome(env: ["HOME": "/Users/x///"]) == "/Users/x", "brHome: trailing slashes are dropped")
        check(brHome(env: [:]) == NSHomeDirectory() && brHome(env: ["HOME": ""]) == NSHomeDirectory() && brHome(env: ["HOME": "rel/x"]) == NSHomeDirectory() && brHome(env: ["HOME": "/"]) == NSHomeDirectory(),
              "brHome: HOME unset, empty, relative or / falls back to NSHomeDirectory()")
        check(brConfigDir(home: "/h", env: ["CLAUDE_CONFIG_DIR": "/c/cfg"]) == "/c/cfg" && brConfigDir(home: "/h", env: [:]) == "/h/.claude"
              && brConfigDir(home: "/h", env: ["CLAUDE_CONFIG_DIR": ""]) == "/h/.claude" && brConfigDir(home: "/h", env: ["CLAUDE_CONFIG_DIR": "cfg"]) == "/h/.claude", "brConfigDir: CLAUDE_CONFIG_DIR when absolute, else HOME/.claude")
        check(brConfigEnv(env: ["CLAUDE_CONFIG_DIR": "/c/cfg/"]) == "/c/cfg" && brConfigEnv(env: [:]) == nil && brConfigEnv(env: ["CLAUDE_CONFIG_DIR": "x"]) == nil, "brConfigEnv: only a set, absolute CLAUDE_CONFIG_DIR is passed on")
        let d = BroteDirs(home: "/H")
        check(d.support == "/H/Library/Application Support/BroteWindow" && d.events == d.support + "/events.log" && d.lock == d.support + "/brote.lock" && d.sessions == d.support + "/sessions"
              && d.app == d.support + "/Brote.app" && d.logs == "/H/Library/Logs/BroteWindow" && d.probe == d.logs + "/probe.log", "BroteDirs: every path hangs from HOME")
        let e1 = statusEnv(home: "/h", configDir: "/c/cfg"), e2 = statusEnv(home: "/h", configDir: nil)
        check(e1["CLAUDE_CONFIG_DIR"] == "/c/cfg" && e1["HOME"] == "/h" && e1.count == 6 && e2["CLAUDE_CONFIG_DIR"] == nil && e2.count == 5, "statusEnv: CLAUDE_CONFIG_DIR goes through only when set")
        check(updateEnv(home: "/h", configDir: "/c/cfg")["CLAUDE_CONFIG_DIR"] == "/c/cfg" && updateEnv(home: "/h", configDir: nil).count == 3, "updateEnv: CLAUDE_CONFIG_DIR goes through only when set")
    }

    do { // the single-instance lock
        let fm = FileManager.default
        let home = tmp + "/lockhome", dirs = BroteDirs(home: home)
        func mode(_ p: String) -> Int { ((try? fm.attributesOfItem(atPath: p))?[.posixPermissions] as? NSNumber)?.intValue ?? -1 }
        func text(_ p: String) -> String { (try? String(contentsOfFile: p, encoding: .utf8)) ?? "?" }
        let a = takeInstanceLock(dirs: dirs)
        check(a.outcome == .held && a.fd >= 0, "lock: the first start holds it")
        check(mode(dirs.support) == 0o700 && mode(dirs.lock) == 0o600, "lock: the folder is 0700 and the lock file 0600")
        check(text(dirs.lock) == "\(getpid())\n", "lock: the file holds the decimal pid and a newline")
        check(a.fd >= 0 && (fcntl(a.fd, F_GETFD) & FD_CLOEXEC) != 0, "lock: the descriptor is close-on-exec (a child never keeps the lock alive)")
        fm.createFile(atPath: dirs.events, contents: Data("start abc\n".utf8), attributes: [.posixPermissions: 0o600])
        let b = takeInstanceLock(dirs: dirs)
        check(b.outcome == .busy && b.fd == -1, "lock: a second start finds it busy")
        check(text(dirs.lock) == "\(getpid())\n" && text(dirs.events) == "start abc\n", "lock: a busy start touches neither the lock file nor events.log")
        if a.fd >= 0 { close(a.fd) }
        let c = takeInstanceLock(dirs: dirs)
        check(c.outcome == .held, "lock: closing the first descriptor (the process ending) frees it")
        if c.fd >= 0 { close(c.fd) }
        try? "99999999999999999999999\n".write(toFile: dirs.lock, atomically: false, encoding: .utf8)
        let c2 = takeInstanceLock(dirs: dirs)
        check(c2.outcome == .held && text(dirs.lock) == "\(getpid())\n", "lock: an old, longer pid is truncated away")
        if c2.fd >= 0 { close(c2.fd) }
        try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dirs.support)
        let c3 = takeInstanceLock(dirs: dirs)
        check(c3.outcome == .held && mode(dirs.support) == 0o700, "lock: a looser folder of mine is set back to 0700")
        if c3.fd >= 0 { close(c3.fd) }
        // a symlinked BroteWindow, a symlinked Brote.app, a symlinked lock file, a plain file instead of the folder: refused, nothing written through
        let elsewhere = tmp + "/elsewhere"
        try? fm.createDirectory(atPath: elsewhere, withIntermediateDirectories: true)
        let h2 = tmp + "/lockhome2", d2 = BroteDirs(home: h2)
        try? fm.createDirectory(atPath: (d2.support as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        try? fm.createSymbolicLink(atPath: d2.support, withDestinationPath: elsewhere)
        let s1 = takeInstanceLock(dirs: d2)
        if case .refused = s1.outcome { check(true, "lock: a symlinked BroteWindow is refused") } else { check(false, "lock: a symlinked BroteWindow is refused") }
        check(((try? fm.contentsOfDirectory(atPath: elsewhere)) ?? ["x"]).isEmpty && s1.fd == -1, "lock: nothing is written through the symlink")
        try? fm.removeItem(atPath: d2.support)
        try? fm.createDirectory(atPath: d2.support, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try? fm.createSymbolicLink(atPath: d2.app, withDestinationPath: elsewhere)
        let s2 = takeInstanceLock(dirs: d2)
        if case .refused = s2.outcome { check(true, "lock: a symlinked Brote.app is refused") } else { check(false, "lock: a symlinked Brote.app is refused") }
        check(!fm.fileExists(atPath: d2.lock), "lock: a refused start creates no lock file")
        try? fm.removeItem(atPath: d2.app)
        try? fm.createSymbolicLink(atPath: d2.lock, withDestinationPath: elsewhere + "/target")
        let s3 = takeInstanceLock(dirs: d2)
        check(s3.outcome != .held && s3.fd == -1 && !fm.fileExists(atPath: elsewhere + "/target"), "lock: a symlinked lock file is never followed (O_NOFOLLOW)")
        try? fm.removeItem(atPath: d2.support)
        fm.createFile(atPath: d2.support, contents: Data("x".utf8))
        let s4 = takeInstanceLock(dirs: d2)
        if case .refused = s4.outcome { check(true, "lock: a plain file where the folder should be is refused") } else { check(false, "lock: a plain file where the folder should be is refused") }
    }

    do { // the sessions the plugin writes: BroteWindow/sessions/<pid>.json, live only while the process is the one that wrote it
        let fm = FileManager.default
        let dir = tmp + "/sess"
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        func put(_ name: String, _ body: String, mode: Int = 0o600) { fm.createFile(atPath: dir + "/" + name, contents: Data(body.utf8), attributes: [.posixPermissions: mode]) }
        func gone(_ name: String) -> Bool { !fm.fileExists(atPath: dir + "/" + name) }
        let starts: [Int32: Int] = [4242: 1000, 4243: 2000, 4244: 3000]
        let alive: (Int32) -> Bool = { starts[$0] != nil }, startOf: (Int32) -> Int? = { starts[$0] }
        put("4242.json", #"{"pid": 4242, "start": 1000, "sid": "abcdef012345"}"#)
        var r = scanSessions(dir: dir, alive: alive, startOf: startOf)
        check(r.live == [SessionEntry(pid: 4242, start: 1000, sid: "abcdef012345")] && r.dead.isEmpty, "sessions: a file whose process is alive with the same start time is live")
        put("4243.json", #"{"pid": 4243, "start": 1999, "sid": "x"}"#)                       // the pid was reused: another start time
        put("4245.json", #"{"pid": 4245, "start": 5, "sid": "gone"}"#)                       // nobody home
        r = scanSessions(dir: dir, alive: alive, startOf: startOf)
        check(r.live.map { $0.pid } == [4242] && r.dead.count == 2, "sessions: a reused pid (other start time) and a gone process are dead")
        check(gone("4243.json") && gone("4245.json") && !gone("4242.json"), "sessions: dead entries are deleted, the live one stays")
        put("4243.json", #"{"pid": 4243, "start": 1999}"#)
        r = scanSessions(dir: dir, reap: false, alive: alive, startOf: startOf)
        check(r.dead.count == 1 && !gone("4243.json"), "sessions: with reap off nothing is deleted")
        try? fm.removeItem(atPath: dir + "/4243.json")
        put("4244.json", #"{"pid": 4244, "start": 3000, "sid": "two"}"#)
        r = scanSessions(dir: dir, alive: alive, startOf: startOf)
        check(r.live.count == 2, "sessions: two live sessions count two")
        var gone2 = starts; gone2[4244] = nil
        r = scanSessions(dir: dir, alive: { gone2[$0] != nil }, startOf: { gone2[$0] })
        check(r.live.count == 1 && r.live[0].pid == 4242, "sessions: two sessions, one dies: one is still live")
        for junk in ["{", "[1,2]", "null", "", #"{"pid": "4242", "start": 1000}"#, #"{"pid": 4242.5, "start": 1000}"#, #"{"pid": true, "start": 1000}"#, #"{"pid": 4242}"#, #"{"start": 1000}"#,
                     #"{"pid": 4242, "start": 1000, "sid": 7}"#, #"{"pid": 4242, "start": 1000, "sid": "../../etc/x"}"#, #"{"pid": 4242, "start": 1000, "sid": "abcdefghijklm"}"#, #"{"pid": 4242, "start": "1000"}"#] {
            for n in ["4242.json"] { put(n, junk) }
            let rr = scanSessions(dir: dir, alive: alive, startOf: startOf)
            check(!rr.live.contains { $0.pid == 4242 } && !gone("4242.json"), "sessions: garbage is ignored, not live and not deleted: " + junk.prefix(30))
        }
        put("4242.json", #"{"pid": 4242, "start": 1000, "sid": "ok"}"#)
        put("4244.json", #"{"pid": 4243, "start": 2000, "sid": "ok"}"#)                      // the name and the pid disagree
        put("notes.txt", #"{"pid": 4243, "start": 2000}"#); put("x.json", #"{"pid": 4243, "start": 2000}"#); put("0.json", #"{"pid": 0, "start": 1}"#); put("1.json", #"{"pid": 1, "start": 1}"#)
        put("-5.json", #"{"pid": -5, "start": 1}"#)
        let odd = scanSessions(dir: dir, alive: { _ in true }, startOf: { _ in 1000 })
        check(odd.live.map { $0.pid } == [4242] && odd.dead.isEmpty, "sessions: pid <= 1, odd names and a name that differs from the pid are ignored")
        put("9.json", String(repeating: " ", count: 5000) + #"{"pid": 9, "start": 1}"#)
        check(!scanSessions(dir: dir, reap: false, alive: { _ in true }, startOf: { _ in 1 }).live.contains { $0.pid == 9 }, "sessions: an oversized file is ignored")
        let elsewhere = tmp + "/sess-target.json"
        fm.createFile(atPath: elsewhere, contents: Data(#"{"pid": 4243, "start": 2000}"#.utf8), attributes: [.posixPermissions: 0o600])
        try? fm.createSymbolicLink(atPath: dir + "/4243.json", withDestinationPath: elsewhere)
        let viaLink = scanSessions(dir: dir, alive: alive, startOf: startOf)
        check(!viaLink.live.contains { $0.pid == 4243 } && fm.fileExists(atPath: elsewhere), "sessions: a symlinked entry is ignored and its target is never touched")
        try? fm.removeItem(atPath: dir + "/4243.json")
        check(mkfifo(dir + "/4244.json.tmp", 0o600) == 0 && mkfifo(dir + "/4246.json", 0o600) == 0, "sessions: (a pipe can be made)")
        let withPipe = scanSessions(dir: dir, alive: alive, startOf: startOf)       // would hang if a pipe were opened blocking
        check(withPipe.live.contains { $0.pid == 4242 }, "sessions: a named pipe in the folder is skipped, never opened blocking")
        check(scanSessions(dir: dir, uid: getuid() &+ 1, alive: alive, startOf: startOf).live.isEmpty, "sessions: a folder or file owned by another user is ignored")
        try? fm.setAttributes([.posixPermissions: 0o770], ofItemAtPath: dir)
        check(scanSessions(dir: dir, alive: alive, startOf: startOf).live.isEmpty, "sessions: a group-writable sessions folder is ignored altogether")
        try? fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir)
        let link = tmp + "/sess-link"
        try? fm.createSymbolicLink(atPath: link, withDestinationPath: dir)
        check(scanSessions(dir: link, alive: alive, startOf: startOf).live.isEmpty && scanSessions(dir: tmp + "/no-such", alive: alive, startOf: startOf).live.isEmpty, "sessions: a symlinked or missing folder is no sessions")
        // the real process table
        let real = tmp + "/sess-real"
        try? fm.createDirectory(atPath: real, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let me = Int32(getpid()), myStart = processStart(me)
        check(myStart != nil && abs(Double(myStart ?? 0) - Date().timeIntervalSince1970) < 3600 && processAlive(me), "processStart: my own start time is read from the kernel, and I am alive")
        check(processStart(2_000_000_000) == nil && !processAlive(2_000_000_000), "processStart/processAlive: a pid that does not exist")
        fm.createFile(atPath: real + "/\(me).json", contents: Data("{\"pid\": \(me), \"start\": \(myStart ?? 0), \"sid\": \"selftest\"}".utf8), attributes: [.posixPermissions: 0o600])
        fm.createFile(atPath: real + "/\(me &+ 1).json", contents: Data("{\"pid\": \(me &+ 1), \"start\": 1, \"sid\": \"other\"}".utf8), attributes: [.posixPermissions: 0o600])
        let rr = scanSessions(dir: real)
        check(rr.live.map { $0.pid } == [me], "sessions: with the real process table my own file is live and a made-up start time is not")
        fm.createFile(atPath: real + "/\(me).json", contents: Data("{\"pid\": \(me), \"start\": \((myStart ?? 0) + 1), \"sid\": \"reuse\"}".utf8), attributes: [.posixPermissions: 0o600])
        check(scanSessions(dir: real).live.isEmpty, "sessions: my pid with a start time one second off is a reused pid, dead")
    }

    do { // the project the data line asks about follows the user's work: the newest live session that names a usable one, else the launch argument
        let fm = FileManager.default
        let dir = tmp + "/sess-proj"
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let pA = tmp + "/proj-A", pB = tmp + "/proj-B", pFile = tmp + "/proj-file"
        try? fm.createDirectory(atPath: pA, withIntermediateDirectories: true); try? fm.createDirectory(atPath: pB, withIntermediateDirectories: true)
        fm.createFile(atPath: pFile, contents: Data("x".utf8))
        let rA = realPath(pA) ?? "?", rB = realPath(pB) ?? "?"
        func put(_ pid: Int, project: String?, age: TimeInterval) {
            let body = "{\"pid\": \(pid), \"start\": \(pid), \"sid\": \"s\(pid)\"" + (project.map { ", \"project\": \"\($0)\"" } ?? "") + "}"
            let path = dir + "/\(pid).json"
            fm.createFile(atPath: path, contents: Data(body.utf8), attributes: [.posixPermissions: 0o600])
            try? fm.setAttributes([.modificationDate: Date(timeIntervalSinceNow: -age)], ofItemAtPath: path)
        }
        var up = Set<Int32>([5001, 5002, 5003])
        func pick(launch: String? = nil) -> String? {
            let sc = scanSessions(dir: dir, reap: false, alive: { up.contains($0) }, startOf: { Int($0) })
            return chooseProject(live: sc.live, mtimes: sc.mtimes, launch: launch)
        }
        put(5001, project: pA, age: 100); put(5002, project: pB, age: 10)
        check(pick() == rB, "project: the live session whose file is newest wins")
        let sc0 = scanSessions(dir: dir, reap: false, alive: { up.contains($0) }, startOf: { Int($0) })
        check(sc0.live.count == 2 && abs((sc0.mtimes[5002] ?? 0) - (Date().timeIntervalSince1970 - 10)) < 5 && (sc0.mtimes[5001] ?? 0) < (sc0.mtimes[5002] ?? 0), "project: the scan returns each live file's mtime")
        check(sc0.live.first { $0.pid == 5001 }?.project == pA, "project: the entry carries the project the file names")
        put(5001, project: pA, age: 1)                                         // the other session is now the newest: the next refresh switches
        check(pick() == rA, "project: changing the newest session switches the project on the next refresh")
        up.remove(5001)
        check(pick() == rB, "project: a dead session's project is ignored")
        put(5003, project: tmp + "/proj-nowhere", age: 0)
        check(pick() == rB, "project: a newest session whose folder does not exist falls back to the next one")
        put(5003, project: pFile, age: 0)
        check(pick() == rB, "project: a project that is a file is skipped")
        put(5003, project: "/usr", age: 0)
        let lk = tmp + "/proj-link"
        try? fm.createSymbolicLink(atPath: lk, withDestinationPath: "/usr")
        check(getuid() == 0 || (pick() == rB && projectArgument(lk) == nil), "project: a folder (or a link to one) owned by another user is skipped")
        check(projectArgument(pA, uid: getuid() &+ 1) == nil && projectArgument(pA) == rA, "projectArgument: the owner is checked")
        put(5003, project: "relative/path", age: 0)
        check(pick() == rB && scanSessions(dir: dir, reap: false, alive: { up.contains($0) }, startOf: { Int($0) }).live.contains { $0.pid == 5003 }, "project: a bad project drops only that field, the session stays live")
        up = [5003]
        check(pick() == nil && pick(launch: rA) == rA, "project: no usable session project -> the launch argument, else none")
        check(chooseProject(live: [], mtimes: [:], launch: nil) == nil && chooseProject(live: [], mtimes: [:], launch: rB) == rB, "project: no sessions at all")
        check(chooseProject(live: [SessionEntry(pid: 9, start: 1, sid: "", project: tmp + "/gone")], mtimes: [9: 5], launch: rA) == rA, "project: a vanished project falls back to the launch argument")
        let ok = parseSessionFile(Data(#"{"pid": 7, "start": 1, "sid": "a", "project": "/a/b"}"#.utf8))
        check(ok?.project == "/a/b", "parseSessionFile: an absolute project is kept")
        for bad in [#""rel""#, #""""#, "5", "null", #"["/a"]"#, "\"/a\\u0000b\"", "\"/" + String(repeating: "x", count: 1030) + "\""] {
            let e = parseSessionFile(Data(("{\"pid\": 7, \"start\": 1, \"sid\": \"a\", \"project\": " + bad + "}").utf8))
            check(e != nil && e?.project == nil && e?.pid == 7, "parseSessionFile: a bad project (" + bad.prefix(12) + ") drops only that field")
        }
    }

    do { // when he leaves: never in the first 120 s, then 120 s with no live session, or the plugin gone
        let g = LifecycleK.grace
        check(g == 120 && LifecycleK.sessionPoll == 5 && LifecycleK.pluginPoll == 60, "lifecycle: 120 s grace, sessions looked at every 5 s, the plugin every 60 s")
        check(lifecycleDecision(now: 119, lastLiveAt: nil, startedAt: 0, live: 0, pluginPresent: true) == .stay, "lifecycle: no session at 119 s stays")
        check(lifecycleDecision(now: 120, lastLiveAt: nil, startedAt: 0, live: 0, pluginPresent: true) == .quit("no live session for 120 s"), "lifecycle: no session at 120 s quits")
        check(lifecycleDecision(now: 5000, lastLiveAt: 4950, startedAt: 0, live: 0, pluginPresent: true) == .stay, "lifecycle: the last session ended 50 s ago: stay")
        check(lifecycleDecision(now: 5000, lastLiveAt: 4880, startedAt: 0, live: 0, pluginPresent: true) == .quit("no live session for 120 s"), "lifecycle: the last session ended 120 s ago: quit")
        check(lifecycleDecision(now: 5000, lastLiveAt: 0, startedAt: 0, live: 1, pluginPresent: true) == .stay, "lifecycle: a live session stays however long it lasts")
        check(lifecycleDecision(now: 50, lastLiveAt: nil, startedAt: 0, live: 0, pluginPresent: false) == .stay, "lifecycle: never quits in the first 120 s, not even for a plugin that is gone")
        check(lifecycleDecision(now: 130, lastLiveAt: 125, startedAt: 0, live: 2, pluginPresent: false) == .quit("plugin removed"), "lifecycle: the plugin gone quits even with live sessions")
        check(lifecycleDecision(now: 90, lastLiveAt: 200, startedAt: 100, live: 0, pluginPresent: true) == .stay, "lifecycle: a clock before the start (never) does not quit")
        // a run on an injected clock, polled every 5 s
        var st = LifecycleState(startedAt: 1000, watchPlugin: false)
        var quitAt: Double?
        func run(_ from: Double, _ to: Double, live: (Double) -> Int) {
            var t = from
            while t <= to && quitAt == nil { if case .quit = st.step(now: t, live: live(t), pluginProbe: { true }) { quitAt = t }; t += 5 }
        }
        run(1000, 1400, live: { $0 < 1200 ? 1 : 0 })
        check(quitAt == 1315, "lifecycle: the last session is seen at 1195 and he leaves 120 s later (\(String(describing: quitAt)))")
        st = LifecycleState(startedAt: 1000, watchPlugin: false); quitAt = nil
        run(1000, 1600, live: { $0 < 1200 ? 1 : ($0 >= 1300 && $0 < 1400 ? 1 : 0) })
        check(quitAt == 1515, "lifecycle: a session that comes back at 1300 resets the 120 s (left at \(String(describing: quitAt)))")
        st = LifecycleState(startedAt: 1000, watchPlugin: false); quitAt = nil
        run(1000, 5000, live: { $0 < 2000 ? 2 : 1 })
        check(quitAt == nil, "lifecycle: two sessions, one ends: he stays as long as one lives")
        st = LifecycleState(startedAt: 1000, watchPlugin: false); quitAt = nil
        run(1000, 1200, live: { _ in 0 })
        check(quitAt == 1120, "lifecycle: a start with no session at all leaves at 120 s, not before (\(String(describing: quitAt)))")
        // the plugin is looked at once a minute, only when asked to
        var looks = 0, answer: Bool? = true
        st = LifecycleState(startedAt: 0, watchPlugin: true); quitAt = nil
        var t = 0.0
        while t <= 300 && quitAt == nil { if case .quit = st.step(now: t, live: 1, pluginProbe: { looks += 1; return answer }) { quitAt = t }; if t == 140 { answer = nil }; if t == 200 { answer = false }; t += 5 }
        check(looks >= 4 && looks <= 6, "lifecycle: the plugin file is read once a minute (\(looks) reads in 300 s)")
        check(quitAt == 240, "lifecycle: an unreadable file does not count (stay at 180), a file without the plugin quits at the next look (\(String(describing: quitAt)))")
        var reads = 0
        var off = LifecycleState(startedAt: 0, watchPlugin: false)
        for k in 0..<100 { _ = off.step(now: Double(k) * 5, live: 1, pluginProbe: { reads += 1; return false }) }
        check(reads == 0, "lifecycle: a run without --plugin-root never reads the plugin file")
    }

    do { // installed_plugins.json: the plugin is gone only when a well-formed file has no budgit and no studio
        let fm = FileManager.default
        let cfg = tmp + "/cfg-plug"
        try? fm.createDirectory(atPath: cfg + "/plugins", withIntermediateDirectories: true)
        func put(_ body: String?) {
            let p = cfg + "/plugins/installed_plugins.json"
            try? fm.removeItem(atPath: p)
            if let b = body { fm.createFile(atPath: p, contents: Data(b.utf8), attributes: [.posixPermissions: 0o600]) }
        }
        put(nil); check(pluginInstalled(configDir: cfg) == nil, "pluginInstalled: a missing file is unknown (stay)")
        put(#"{"version": 2, "plugins": {"budgit@budgit-hub": [{"scope": "user", "installPath": "/x", "version": "0.13.2"}]}}"#)
        check(pluginInstalled(configDir: cfg) == true, "pluginInstalled: budgit@market is present")
        put(#"{"plugins": {"studio@old-market": []}}"#); check(pluginInstalled(configDir: cfg) == true, "pluginInstalled: studio@market is present too")
        put(#"{"plugins": {"other@m": [{}], "budgit": [{}]}}"#); check(pluginInstalled(configDir: cfg) == true, "pluginInstalled: the name part before @ is what counts")
        put(#"{"plugins": {"other@m": [{}], "budgitx@m": [{}], "my-studio@m": [{}], "@budgit": [{}]}}"#); check(pluginInstalled(configDir: cfg) == false, "pluginInstalled: only the exact names budgit and studio count")
        put(#"{"plugins": {}}"#); check(pluginInstalled(configDir: cfg) == false, "pluginInstalled: a well-formed file with no plugins at all: gone")
        for bad in ["{", "[]", "", "null", #"{"plugins": []}"#, #"{"plugins": "x"}"#, #"{"version": 2}"#] { put(bad); check(pluginInstalled(configDir: cfg) == nil, "pluginInstalled: unreadable or unexpected shape is unknown, never gone: " + bad.prefix(20)) }
        put(#"{"plugins": {"other@m": [{}]}"# + String(repeating: " ", count: 1_100_000) + "}")
        check(pluginInstalled(configDir: cfg) == nil, "pluginInstalled: a file over 1 MB is unknown")
        put(nil)
        let real = tmp + "/ip-real.json"
        fm.createFile(atPath: real, contents: Data(#"{"plugins": {"other@m": [{}]}}"#.utf8))
        try? fm.createSymbolicLink(atPath: cfg + "/plugins/installed_plugins.json", withDestinationPath: real)
        check(pluginInstalled(configDir: cfg) == nil, "pluginInstalled: a symlinked file is unknown")
        check(pluginInstalled(configDir: tmp + "/no-cfg") == nil, "pluginInstalled: a missing config folder is unknown")
    }

    do { // two layouts, one trust rule: a dev clone (--hub: plugin/bin, plugin/scripts) and the installed plugin (--plugin-root: bin, scripts)
        let fm = FileManager.default
        let h = PluginLayout.hub("/hh"), r = PluginLayout.pluginRoot("/rr")
        check(h.budgit == "/hh/plugin/bin/budgit" && h.binDir == "/hh/plugin/bin" && h.scriptsDir == "/hh/plugin/scripts" && h.pycache == "/hh/plugin/scripts/__pycache__" && !h.isRoot, "layout: a hub clone keeps plugin/ in the path")
        check(r.budgit == "/rr/bin/budgit" && r.binDir == "/rr/bin" && r.scriptsDir == "/rr/scripts" && r.pycache == "/rr/scripts/__pycache__" && r.isRoot, "layout: a plugin root has bin and scripts at its top")
        check(statusArgv(layout: h, project: "/hh") == statusArgv(plugin: "/hh"), "statusArgv: the hub layout is the old argv")
        check(statusArgv(layout: r, project: nil) == ["/usr/bin/python3", "-B", "/rr/bin/budgit", "status", "--brote", "--live"], "statusArgv: a plugin root passes no --project (the cache folder is not a project)")
        check(statusArgv(layout: r, project: "/p") == ["/usr/bin/python3", "-B", "/rr/bin/budgit", "status", "--brote", "--live", "--project", "/p"], "statusArgv: a project given on the command line is passed on")
        let cfg = tmp + "/cfg-root", cache = cfg + "/plugins/cache"
        func build(_ root: String) {
            try? fm.createDirectory(atPath: root + "/bin", withIntermediateDirectories: true); try? fm.createDirectory(atPath: root + "/scripts", withIntermediateDirectories: true)
            fm.createFile(atPath: root + "/bin/budgit", contents: Data("x".utf8), attributes: [.posixPermissions: 0o755])
            fm.createFile(atPath: root + "/scripts/studiolib.py", contents: Data("x".utf8), attributes: [.posixPermissions: 0o644])
        }
        let root = cache + "/budgit-hub/budgit/0.13.2"
        build(root)
        let real = realPath(root) ?? "?"
        check(trustedPluginRoot(root, configDir: cfg)?.root == real && trustedPluginRoot(root, configDir: cfg)?.budgit == real + "/bin/budgit", "pluginRoot: a version folder under <config>/plugins/cache is trusted, as its real path")
        check(pluginRootTrusted(root, configDir: cfg) && !pluginRootTrusted(tmp + "/nowhere", configDir: cfg), "pluginRoot: the Bool form")
        let loose = tmp + "/loose-root"
        build(loose)
        check(trustedPluginRoot(loose, configDir: cfg) == nil, "pluginRoot: a good folder outside <config>/plugins/cache is refused")
        check(trustedPluginRoot(cache, configDir: cfg) == nil && trustedPluginRoot(cache + "/", configDir: cfg) == nil, "pluginRoot: the cache folder itself is not a root")
        check(trustedPluginRoot(cache + "/budgit-hub/budgit/0.13.2/../../../../../../loose-root", configDir: cfg) == nil, "pluginRoot: .. out of the cache is refused (the real path is what counts)")
        let out = cache + "/budgit-hub/budgit/evil"
        try? fm.createSymbolicLink(atPath: out, withDestinationPath: loose)
        check(trustedPluginRoot(out, configDir: cfg) == nil, "pluginRoot: a link in the cache to a folder outside it is refused")
        let cfgLink = tmp + "/cfg-link"
        try? fm.createSymbolicLink(atPath: cfgLink, withDestinationPath: cfg)
        check(trustedPluginRoot(cfgLink + "/plugins/cache/budgit-hub/budgit/0.13.2", configDir: cfgLink)?.root == real, "pluginRoot: a config folder that is itself a link still works (both sides are resolved)")
        for (path, why) in [(root, "the version folder"), (root + "/bin", "bin"), (root + "/scripts", "scripts")] {
            try? fm.setAttributes([.posixPermissions: 0o775], ofItemAtPath: path)
            check(trustedPluginRoot(root, configDir: cfg) == nil, "pluginRoot: a group-writable " + why + " is refused")
            try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: path)
        }
        try? fm.setAttributes([.posixPermissions: 0o666], ofItemAtPath: root + "/scripts/studiolib.py")
        check(trustedPluginRoot(root, configDir: cfg) == nil, "pluginRoot: a world-writable module in scripts is refused")
        try? fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: root + "/scripts/studiolib.py")
        try? fm.setAttributes([.posixPermissions: 0o757], ofItemAtPath: root + "/bin/budgit")
        check(trustedPluginRoot(root, configDir: cfg) == nil, "pluginRoot: a world-writable budgit is refused")
        try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root + "/bin/budgit")
        check(trustedPluginRoot(root, configDir: cfg) != nil, "pluginRoot: trusted again once the modes are back")
        check(!layoutTrusted(PluginLayout.pluginRoot(tmp + "/no-bin"), fm: fm) && layoutTrusted(PluginLayout.pluginRoot(root), fm: fm), "layoutTrusted: the same rule for both layouts")
        // A folder Claude Code registered outside the cache, and a directory marketplace's live folder (a developer setup: the registry keeps a stale cache path)
        func writeJSON(_ path: String, _ o: Any) { try? fm.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true); fm.createFile(atPath: path, contents: try? JSONSerialization.data(withJSONObject: o), attributes: [.posixPermissions: 0o644]) }
        func registry(_ entries: [String: [Any]]) { writeJSON(cfg + "/plugins/installed_plugins.json", ["version": 2, "plugins": entries]) }
        let hub = tmp + "/hub-market", live = hub + "/plugin"
        build(live)
        let liveReal = realPath(live) ?? "?"
        registry(["studio@studio-hub": [["scope": "user", "installPath": cache + "/studio-hub/studio/0.11.36", "version": "0.11.36"]]])
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: a live folder is refused while no marketplace says so")
        writeJSON(cfg + "/plugins/known_marketplaces.json", ["studio-hub": ["source": ["source": "directory", "path": hub], "installLocation": hub]])
        writeJSON(hub + "/.claude-plugin/marketplace.json", ["name": "studio-hub", "plugins": [["name": "studio", "source": "./plugin"]]])
        check(trustedPluginRoot(live, configDir: cfg)?.root == liveReal, "pluginRoot: the live folder of a directory marketplace the plugin is installed from is trusted")
        writeJSON(cfg + "/settings.json", ["enabledPlugins": ["studio@studio-hub": false]])
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: the same folder is refused while the plugin is switched off in settings")
        writeJSON(cfg + "/settings.json", ["enabledPlugins": ["studio@studio-hub": true]])
        check(trustedPluginRoot(live, configDir: cfg) != nil, "pluginRoot: switched on again")
        fm.createFile(atPath: cfg + "/settings.json", contents: Data("{ not json".utf8), attributes: [.posixPermissions: 0o644])
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: refused while settings.json is there but unreadable")
        try? fm.removeItem(atPath: cfg + "/settings.json")
        check(trustedPluginRoot(live, configDir: cfg) != nil, "pluginRoot: no settings.json at all is fine")
        try? fm.createSymbolicLink(atPath: cfg + "/settings.json", withDestinationPath: tmp + "/nowhere")
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: a settings.json that is a link is never read, so the folder is refused")
        try? fm.removeItem(atPath: cfg + "/settings.json")
        writeJSON(cfg + "/settings.json", ["enabledPlugins": ["studio@studio-hub": true]])
        writeJSON(cfg + "/plugins/known_marketplaces.json", ["studio-hub": ["source": ["source": "directory", "path": "hub-market"], "installLocation": hub]])
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: a relative source path is refused")
        writeJSON(cfg + "/plugins/known_marketplaces.json", ["studio-hub": ["source": ["source": "directory", "path": hub], "installLocation": hub]])
        for (what, market, plug) in [("a github marketplace", ["source": ["source": "github", "repo": "x/y"], "installLocation": hub], "./plugin"),
                                      ("an install location that is another folder", ["source": ["source": "directory", "path": hub], "installLocation": tmp], "./plugin"),
                                      ("a source that leaves the folder", ["source": ["source": "directory", "path": hub], "installLocation": hub], "../loose-root"),
                                      ("an absolute source", ["source": ["source": "directory", "path": hub], "installLocation": hub], loose)] as [(String, Dict, String)] {
            writeJSON(cfg + "/plugins/known_marketplaces.json", ["studio-hub": market])
            writeJSON(hub + "/.claude-plugin/marketplace.json", ["name": "studio-hub", "plugins": [["name": "studio", "source": plug]]])
            check(trustedPluginRoot(live, configDir: cfg) == nil && trustedPluginRoot(loose, configDir: cfg) == nil, "pluginRoot: refused with " + what)
        }
        writeJSON(cfg + "/plugins/known_marketplaces.json", ["studio-hub": ["source": ["source": "directory", "path": hub], "installLocation": hub]])
        writeJSON(hub + "/.claude-plugin/marketplace.json", ["name": "studio-hub", "plugins": [["name": "other", "source": "./plugin"]]])
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: refused when the marketplace lists another plugin at that folder")
        writeJSON(hub + "/.claude-plugin/marketplace.json", ["name": "studio-hub", "plugins": [["name": "studio", "source": "./plugin"]]])
        try? fm.setAttributes([.posixPermissions: 0o775], ofItemAtPath: hub)
        check(trustedPluginRoot(live, configDir: cfg) == nil, "pluginRoot: refused when the marketplace folder is group-writable")
        try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: hub)
        check(trustedPluginRoot(live, configDir: cfg) != nil, "pluginRoot: trusted again once the mode is back")
        registry(["studio@studio-hub": [["scope": "user", "installPath": loose, "version": "0.13.1"]]])
        check(trustedPluginRoot(loose, configDir: cfg)?.root == realPath(loose), "pluginRoot: a folder the registry lists as an installPath is trusted outside the cache")
        let market = cfg + "/plugins/marketplaces/m/plugin"
        build(market)
        registry(["studio@m": [["scope": "user", "installPath": market, "version": "1"]]])
        check(trustedPluginRoot(market, configDir: cfg) == nil, "pluginRoot: never a marketplaces/ copy, even when the registry lists it")
        registry([:])
        // the project argument: an absolute folder that exists and is mine
        let proj = tmp + "/a-project"
        try? fm.createDirectory(atPath: proj, withIntermediateDirectories: true)
        check(projectArgument(proj) == realPath(proj) && projectArgument("rel/x") == nil && projectArgument(tmp + "/none") == nil && projectArgument(root + "/bin/budgit") == nil && projectArgument("") == nil, "projectArgument: only an existing absolute folder")
    }

    do { // the probe log is private: folder 0700, file 0600, never through a link
        let fm = FileManager.default
        func mode(_ p: String) -> Int { ((try? fm.attributesOfItem(atPath: p))?[.posixPermissions] as? NSNumber)?.intValue ?? -1 }
        let pd = tmp + "/priv-logs", pp = pd + "/probe.log"
        let p = Probe(path: pp, privateFolder: true); p.reset()
        check(mode(pd) == 0o700 && mode(pp) == 0o600, "probe: a new log is 0600 in a 0700 folder")
        _ = p.log("P1", [("a", "b")])
        check(mode(pp) == 0o600 && ((try? String(contentsOfFile: pp, encoding: .utf8)) ?? "").contains("PROBE P1 a=b"), "probe: appending keeps 0600")
        try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: pd); try? fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: pp)
        Probe(path: pp, privateFolder: true).reset()
        check(mode(pd) == 0o700 && mode(pp) == 0o600, "probe: an older, looser folder and log are set to 0700 and 0600")
        let od = tmp + "/other-logs"
        try? fm.createDirectory(atPath: od, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o755])
        Probe(path: od + "/probe.log").reset()
        check(mode(od) == 0o755 && mode(od + "/probe.log") == 0o600, "probe: a folder that is not the app's own (scenario runs) keeps its mode, the file is still 0600")
        let target = tmp + "/probe-target"
        fm.createFile(atPath: target, contents: Data("keep".utf8))
        try? fm.removeItem(atPath: pp); try? fm.createSymbolicLink(atPath: pp, withDestinationPath: target)
        let q = Probe(path: pp, privateFolder: true); q.reset()
        check(!q.log("P1", [("a", "b")]) && ((try? String(contentsOfFile: target, encoding: .utf8)) ?? "") == "keep", "probe: a symlinked log is never written through")
    }

    do { // the chat records: only the three keys the link needs, and only what has the right type
        let fm = FileManager.default
        let home = tmp + "/rec-home"
        let dir = home + "/Library/Application Support/Claude/claude-code-sessions/o/a"
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let rec: Dict = ["sessionId": "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "cliSessionId": "0123456789ab-x", "isArchived": false, "title": "my secret chat", "cwd": "/Users/x/secret",
                         "model": "m", "messages": ["a", "b"], "nested": ["k": "v"]]
        try? JSONSerialization.data(withJSONObject: rec).write(to: URL(fileURLWithPath: dir + "/local_1.json"))
        try? JSONSerialization.data(withJSONObject: ["sessionId": 5, "cliSessionId": ["x"], "isArchived": "yes", "title": "t"]).write(to: URL(fileURLWithPath: dir + "/local_2.json"))
        let recs = readChatRecords(home: home)
        check(recs.count == 2 && recs.allSatisfy { Set($0.keys).isSubset(of: ["sessionId", "cliSessionId", "isArchived"]) }, "readChatRecords: only sessionId, cliSessionId and isArchived are kept (no title, no folder, no messages)")
        check(recs.first?["sessionId"] as? String == "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" && recs.first?["cliSessionId"] as? String == "0123456789ab-x" && (recs.first?["isArchived"] as? Bool) == false, "readChatRecords: the three values are intact")
        check(recs.last?["sessionId"] == nil && recs.last?["cliSessionId"] == nil && (recs.last?["isArchived"] as? String) == "yes", "readChatRecords: a value of the wrong type is dropped")
    }

    do { // every way out ends in the same cleanup: events.log goes, the lock is the OS's to release, sessions are not touched
        let fm = FileManager.default
        let home = tmp + "/exit-home", dirs = BroteDirs(home: home)
        try? fm.createDirectory(atPath: dirs.sessions, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        fm.createFile(atPath: dirs.events, contents: Data("start x\n".utf8), attributes: [.posixPermissions: 0o600])
        fm.createFile(atPath: dirs.lock, contents: Data("1\n".utf8), attributes: [.posixPermissions: 0o600])
        fm.createFile(atPath: dirs.sessions + "/7.json", contents: Data("{}".utf8))
        cleanupOnExit(dirs: dirs)
        check(!fm.fileExists(atPath: dirs.events) && fm.fileExists(atPath: dirs.lock) && fm.fileExists(atPath: dirs.sessions + "/7.json"), "cleanupOnExit: events.log is removed; the lock file and the sessions stay")
        cleanupOnExit(dirs: dirs)
        check(!fm.fileExists(atPath: dirs.events), "cleanupOnExit: twice is harmless")
        let keep = tmp + "/exit-keep"
        fm.createFile(atPath: keep, contents: Data("k".utf8))
        try? fm.createSymbolicLink(atPath: dirs.events, withDestinationPath: keep)
        cleanupOnExit(dirs: dirs)
        check(!fm.fileExists(atPath: dirs.events) && fm.fileExists(atPath: keep), "cleanupOnExit: a symlinked events.log is unlinked, its target kept")
        let w = removeMenuWords(prefs: ["es-ES"]), we = removeMenuWords(prefs: ["en-US"]), wf = removeMenuWords(prefs: ["fr-FR"])
        check(w.title.hasPrefix("Quitar") && we.title.hasPrefix("Remove") && wf.title == we.title && removeMenuWords(prefs: ["fr", "es"]).title == w.title, "removeMenuWords: Spanish or English, English by default")
        check(uninstallArgv(layout: PluginLayout.pluginRoot("/rr")) == ["/usr/bin/python3", "-B", "/rr/scripts/floatctl.py", "uninstall"] && uninstallArgv(layout: PluginLayout.hub("/hh")) == nil,
              "uninstallArgv: the plugin's own uninstall by argument list (no shell), only for an installed plugin root")
        check(ExitCause.menu.note == "quit from the menu" && ExitCause.signal(15).note == "signal 15: cleaning up" && ExitCause.lifecycle("plugin removed").note == "plugin removed", "ExitCause: the three ways out have one note each")
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
        do { // a sign moves with its bubble when he faces left but still reads the right way round
            let a = RGBA(r: 1, g: 0, b: 0, a: 255), b = RGBA(r: 2, g: 0, b: 0, a: 255), c = RGBA(r: 3, g: 0, b: 0, a: 255)
            var g = Grid(w: 6, h: 1, cells: [a, b, nil, nil, nil, c]); g.glyphs = [[0, 0, 2, 1]]
            let m = g.mirrored()
            check(m.cells == [c, nil, nil, nil, a, b] && m.glyphs == [[4, 0, 2, 1]], "mirrored: the sign box keeps its order, the rest mirrors")
            check(m.mirrored().cells == g.cells, "mirrored twice: the same picture")
            var bare = g; bare.glyphs = []
            check(glyphsReadRight(g, flipped: true) && glyphsReadRight(g, flipped: false) && !glyphsReadRight(bare, flipped: true),
                  "the scenario trace: a sign in its box reads right when he faces left; a mirrored sign with no box does not")
        }
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

    // each tick makes a single JS call, and a frame is pushed only when something changed (a cold JS call costs about 50 us at 10 Hz)
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
    check(argv == ["/usr/bin/python3", "-B", "/h/plugin/bin/budgit", "status", "--brote", "--live", "--project", "/h"], "status argv")
    check(!argv.contains("--limits-json") && !argv.contains("-c") && !argv.contains { $0.hasPrefix("/bin/") }, "no shell, no limits json")
    let env = statusEnv(home: "/Users/x")
    check(env["PYTHONDONTWRITEBYTECODE"] == "1" && env["PYTHONIOENCODING"] == "utf-8" && env["PATH"] == "/usr/bin:/bin" && env["HOME"] == "/Users/x" && env.count == 5, "status env is explicit and small")
    check(K.statusTimeout == 5 && K.statusEvery == 30, "5 s kill, 30 s refresh")
    check(parseStatus(Data("{\"state\":\"cansado\",\"h5\":80}".utf8)) != nil, "parseStatus: a status object")
    check(parseStatus(Data("not json".utf8)) == nil && parseStatus(Data("[1]".utf8)) == nil && parseStatus(Data("{}".utf8)) == nil && parseStatus(Data()) == nil, "parseStatus: malformed -> nil")
    do { // the update offer and the chat link
        let up: Dict = ["update": ["kind": "available", "running": "0.13.0", "available": "0.13.1", "market": "budgit-hub", "plugin": "budgit"]]
        let o = updateOffer(up)
        check(o == UpdateOffer(version: "0.13.1", market: "budgit-hub", plugin: "budgit"), "updateOffer: an available release names the version, marketplace and plugin")
        check(updateOffer(["update": ["kind": "stale-chat", "running": "0.13.0", "available": "0.13.1"]]) == nil && updateOffer([:]) == nil && updateOffer(nil) == nil, "updateOffer: a stale chat or nothing is no offer")
        for bad in [["available": "0.13.1; rm -rf /", "market": "m", "plugin": "p"], ["available": "x", "market": "m", "plugin": "p"], ["available": "0.13.1", "market": "-m", "plugin": "p"],
                    ["available": "0.13.1", "market": "m", "plugin": "p q"], ["available": "0.13.1", "market": "m"], ["available": "0.13.1", "market": "a/b", "plugin": "p"]] as [Dict] {
            var u = bad; u["kind"] = "available"
            check(updateOffer(["update": u]) == nil, "updateOffer: hostile text never becomes a command: " + String(describing: bad["available"] ?? bad["market"] ?? ""))
        }
        check(updateArgvs(claude: "/c/claude", offer: o!) == [["/c/claude", "plugin", "marketplace", "update", "budgit-hub"], ["/c/claude", "plugin", "update", "budgit@budgit-hub"]], "updateArgvs: the two commands of `budgit update`, as arguments")
        check(updateText(offer: o!) == "claude plugin marketplace update budgit-hub\nclaude plugin update budgit@budgit-hub", "updateText: what can be pasted into a terminal")
        check(findClaude(home: "/h", exists: { $0 == "/opt/homebrew/bin/claude" }) == "/opt/homebrew/bin/claude" && findClaude(home: "/h", exists: { _ in false }) == nil
              && findClaude(home: "/h", exists: { _ in true }) == "/h/.local/bin/claude", "findClaude: the first known place that holds it, nil when none")
        check(updateEnv(home: "/h")["PATH"]?.hasPrefix("/usr/bin:/bin") == true && updateEnv(home: "/h").count == 3, "updateEnv: explicit and small")
        let L = "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", cli = "0123456789ab-cdef-0123-4567-89abcdef0123"
        let one: [Dict] = [["sessionId": L, "cliSessionId": cli, "title": "x"]]
        check(chatLink(prefix: "0123456789ab", records: one) == "claude://claude.ai/epitaxy/" + L, "chatLink: one match is the epitaxy link")
        check(chatLink(prefix: "ffffffffffff", records: one) == nil, "chatLink: no match, no link")
        let L2 = "local_11111111-2222-3333-4444-555555555555"
        check(chatLink(prefix: "0123456789ab", records: one + [["sessionId": L2, "cliSessionId": cli]]) == nil, "chatLink: two matches are not guessed")
        check(chatLink(prefix: "0123456789ab", records: [["sessionId": L, "cliSessionId": cli, "isArchived": true]]) == nil, "chatLink: an archived chat is not a target")
        check(chatLink(prefix: "0123456789ab", records: [["sessionId": L, "cliSessionId": cli, "isArchived": false]]) == "claude://claude.ai/epitaxy/" + L, "chatLink: isArchived false still matches")
        check(chatLink(prefix: "0123456789ab", records: [["sessionId": "local_x/../../etc", "cliSessionId": cli]]) == nil, "chatLink: a record id of the wrong shape never reaches the link")
        check(chatLink(prefix: "01234", records: one) == nil && chatLink(prefix: "0123456789ab/", records: one) == nil && chatLink(prefix: "", records: one) == nil, "chatLink: a short or hostile prefix finds nothing")
        let home = NSTemporaryDirectory() + "chatrec-" + UUID().uuidString
        let dir = home + "/Library/Application Support/Claude/claude-code-sessions/org/acct"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        try? JSONSerialization.data(withJSONObject: one[0]).write(to: URL(fileURLWithPath: dir + "/" + L + ".json"))
        try? Data(repeating: 32, count: 70_000).write(to: URL(fileURLWithPath: dir + "/local_big.json"))
        try? Data("not json".utf8).write(to: URL(fileURLWithPath: dir + "/local_bad.json"))
        check(readChatRecords(home: home).count == 1 && chatLink(prefix: "0123456789ab", records: readChatRecords(home: home)) != nil, "readChatRecords: reads the real records, skips oversized and broken ones")
        check(readChatRecords(home: home + "-none").isEmpty, "readChatRecords: a missing folder is no records")
        try? FileManager.default.removeItem(atPath: home)
        check(K.chatOpensByLink == true, "a permission call's click opens the chat that asked (the hand test showed the link lands on it)")
    }
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

    do { // the README scene (--record): real physics and the real director, a scripted clock and window list
        let (rOpt, _) = PetBridge.load(dir: assets)
        if let rb = rOpt {
            let fr = recordScene(bridge: rb)
            check(fr.count == Rec.frames && fr.first.map { $0.pos.y == Rec.start.y && abs($0.pos.x - Rec.start.x) <= 3 } == true && fr.first?.cover == nil, "record: \(Rec.frames) frames, he starts on the first window's top, the second one is not open yet")
            check(fr.contains { $0.pos.y < Rec.start.y - 1 && $0.pos.y > Rec.floorY + 1 } && fr.last?.pos.y == Rec.floorY, "record: he falls and ends on the floor")
            check(fr.last?.cover == Rec.cover.r && fr.contains { $0.cover != nil && $0.cover != Rec.cover.r }, "record: the second window zooms open and stays")
            check(fr.last.map { $0.key.contains("\"desk\"") && $0.key.contains("lp_code") } ?? false, "record: he ends at his laptop, typing")
            check(recordScene(bridge: PetBridge.load(dir: assets).0!).map { $0.grid } == fr.map { $0.grid }, "record: the same frames every run")
        } else { check(false, "record: the sprite runtime loads") }
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
            db.feed("post abc Bash\nstop abc\n"); for _ in 0..<65 { db.tick() }; _ = db.step()      // after pc_away (3 s drawn at half speed)
            check(db.calling && db.ctx.evaluateScript("__d.mode()")?.toString() == "att_1", "a finished turn calls him")
            check(db.ctx.evaluateScript("JSON.stringify(__glyphs({dy: 0, sit: false, dx: 0, ex: [['bub_call_ques', -10, 15, true], ['heart', 0, 0, false]]}))")?.toString() == "[[25,1,7,7]]",
                  "the \"?\" bubble reports its sign box; other extras none")
            check(["bub_call_ques", "bub_call_check", "bub_call_lock", "bub_think", "bub_done"].allSatisfy { db.ctx.evaluateScript("!!SPR.\($0).glyph")?.toBool() == true }, "every bubble with a sign has a sign box")
            check(db.ctx.evaluateScript("JSON.stringify(__S().ex.map(function(e){return e[0]}))")?.toString().contains("bub_call_check") == true, "with the peach check badge")
            check(db.frame() != nil, "and the picture builds")
            db.setFront(true); db.tick(); check(!db.calling || db.ctx.evaluateScript("__d.mode()")?.toString() == "c_answered", "coming to Claude answers the call")
            db.make("agotado"); db.configure(state: "agotado", h5: 0, stale: 0, held: false, hover: false, walking: false)
            check(db.pokeAgotado(away: 1) && db.ctx.evaluateScript("__d.state")?.toString() == "agotado", "an agotado poke is answered by the director")
            // the app's body clips: every clip a route or a fall plays exists, wins while it lasts, and stops on demand
            db.make("contento"); db.configure(state: "contento", h5: 0, stale: 0, held: false, hover: false, walking: false)
            let body = ["sf_climb", "sf_ladder", "sf_fly", "sf_ride", "sf_fall_close", "sf_fall_move", "sf_fall_cover", "sf_sit", "sf_peek", "sf_edge"]
            check(body.allSatisfy { db.play($0) }, "every body clip the app plays is in personality.js")
            check(!db.play("sf_nope"), "an unknown clip is refused")
            db.play("sf_climb", loop: Route.loop("sf_climb")); for _ in 0..<90 { db.tick() }; _ = db.step()
            check(db.directing && db.ctx.evaluateScript("__d.spec().id || __d.clip.id")?.toString() == "sf_climb", "a looping climb is still on after 9 s")
            db.stopClip(); for _ in 0..<2 { db.tick() }; _ = db.step()
            check(db.ctx.evaluateScript("__d.forced")?.isNull == true, "stopClip ends it")
            // a walking clip: its shift leaves the picture and comes out of walkDx, so the app moves him and nothing jumps
            check(db.walkDx() == nil, "no clip: no walk shift")
            db.play("sf_top"); _ = db.step(); let w0 = db.walkDx()
            for _ in 0..<74 { db.tick() }; _ = db.step()
            check(w0 == -6 && (db.walkDx() ?? 0) >= 5 && db.ctx.evaluateScript("__d.spec().dx")?.toInt32() == 0, "sf_top: drawn in place, a walk of 12 art px for the app")
            db.stopClip(); for _ in 0..<2 { db.tick() }; _ = db.step()
            check(db.walkDx() == nil, "a stopped walk: no shift left")
            check(!db.working, "no turn open: not working")
            db.feed("prompt w1\npre w1 Edit\n"); db.tick()
            check(db.working, "a turn open: working (he goes home)")
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
        do { // the data line's changes: what he reacts to
            let a: Dict = ["queue_recent": [["id": "R-8"]], "doing": [["id": "R-5"], ["id": "R-6"]], "stale": ["R-6"], "done_total": 4, "resets_at": 1000.0]
            var b = a; b["queue_recent"] = [["id": "R-9"], ["id": "R-8"]]
            check(feedChanges(old: a, new: b).clips == ["t_new"] && feedChanges(old: a, new: b).done == 0, "feed: a new item in next")
            var m = a; m["queue_recent"] = [] as [Any]; m["doing"] = [["id": "R-5"], ["id": "R-6"], ["id": "R-8"]]
            check(feedChanges(old: a, new: m).clips == ["t_start"], "feed: an item moved to doing is a start, not a new item")
            var u = a; u["stale"] = [] as [Any]; u["done_total"] = 6
            check(feedChanges(old: a, new: u).clips == ["t_unstale"] && feedChanges(old: a, new: u).done == 2, "feed: a stale item moves again; two newly done")
            var g = a; g["stale"] = [] as [Any]; g["doing"] = [["id": "R-5"]]; g["done_total"] = 5
            check(feedChanges(old: a, new: g).clips.isEmpty && feedChanges(old: a, new: g).done == 1, "feed: a stale item that left doing is done, not unstuck")
            var w = a; w["resets_at"] = 1000.0 + 7 * 86400
            check(feedChanges(old: a, new: w).clips == ["tr_week"], "feed: the weekly quota reset")
            check(feedChanges(old: nil, new: a).clips.isEmpty && feedChanges(old: ["state": "justo", "stale": 1], new: a).clips.isEmpty
                  && feedChanges(old: ["state": "justo", "stale": 1], new: a).done == 0, "feed: no reaction at launch or to the saved line (numbers only)")
            check(feedQuiet(["counts": ["doing": 0, "next": 0, "later": 3]]) && !feedQuiet(["counts": ["doing": 0, "next": 1]]) && !feedQuiet([:]), "feed: nothing in progress or next")
            check(feedRunsOut(["runs_out": 1792900000.0]) && !feedRunsOut(["runs_out": NSNull()]) && !feedRunsOut([:]), "feed: the week runs out early")
            check(batteryLow(percent: 15, onPower: false) && !batteryLow(percent: 15, onPower: true) && !batteryLow(percent: 40, onPower: false) && !batteryLow(percent: nil, onPower: false),
                  "battery: low only below 20 % on battery; a Mac without one never")
            let g2 = PetGlue(bridge: bridge)
            g2.setData(state: "contento", h5: 0, stale: 0)
            bridge.ctx.evaluateScript("__d.forced = null; __d.reading = null; __d.cool = {}")
            check(bridge.setFeed(clips: ["t_start", "t_new"], done: 1, quiet: false, runsOut: false), "feed: the bridge hands it to him")
            _ = g2.step()
            check(bridge.ctx.evaluateScript("__d.clip && __d.clip.id")?.toString() == "t_done", "feed: done beats a start and a new item")
            bridge.setIdle(seconds: 1300); bridge.ctx.evaluateScript("__d.forced = null"); _ = g2.step()
            check(bridge.ctx.evaluateScript("__d.clip && __d.clip.id")?.toString() == "ti_idle20", "idle 20 min: asleep")
            bridge.setIdle(seconds: 0); _ = g2.step()
            check(bridge.ctx.evaluateScript("__d.clip && __d.clip.id")?.toString() == "ti_return", "input again: he wakes with a wave")
            bridge.ctx.evaluateScript("__d.forced = null; __d.cool = {}")
        }
        do { // the Libreta opens: he presents the book, unless he is reading (then he puts it back)
            let g = PetGlue(bridge: bridge)
            g.setData(state: "contento", h5: 0, stale: 0)
            check(g.libretaOpened(), "Libreta opened: he presents the book")
            _ = g.step()
            check(bridge.ctx.evaluateScript("__d.clip && __d.clip.id")?.toString() == "bk_present", "bk_present is what he shows")
            bridge.ctx.evaluateScript("__d.forced = null; __d.startReading()"); _ = g.step()
            check(!g.libretaOpened(), "reading: no present (the perch puts the book back)")
            bridge.ctx.evaluateScript("__d.reading = null")
        }
        do { // Brote and the book: art never closer than 9 pt; he keeps to his side, moves aside from 24 pt, crosses only when cornered
            let b = bookBlock(book: Pt(600, 93), broteY: 93)
            check(b != nil && b!.lo == 525 && b!.hi == 636, "book block: his art 9 pt from the cover on either side")
            check(bookBlock(book: Pt(600, 400), broteY: 93) == nil, "a book floating far above him blocks nothing")
            check(ledgeAroundBook(lo: 0, hi: 1300, x: 200, block: b) == (0, 525) && ledgeAroundBook(lo: 0, hi: 1300, x: 900, block: b) == (636, 1300),
                  "the stroll keeps to his own side of the book")
            check(ledgeAroundBook(lo: 0, hi: 400, x: 200, block: b) == (0, 400), "a book beyond his ledge changes nothing")
            check(clearOfBook(x: 480, block: b, lo: 0, hi: 1300, dragging: true) == nil, "40 pt away: he stays")
            check(clearOfBook(x: 520, block: b, lo: 0, hi: 1300, dragging: true).map { $0.x == 510 && !$0.crossing } == true, "dragged within 24 pt: he backs off on his side")
            check(clearOfBook(x: 520, block: b, lo: 0, hi: 1300, dragging: false) == nil, "the book lying 14 pt from him: he stays (his own walk ends 9 pt from it)")
            check(clearOfBook(x: 560, block: b, lo: 0, hi: 1300, dragging: false).map { $0.x == 525 } == true && clearOfBook(x: 610, block: b, lo: 0, hi: 1300, dragging: false).map { $0.x == 636 } == true,
                  "the book dropped on him: out to the nearer side")
            check(clearOfBook(x: 520, block: b, lo: 515, hi: 1300, dragging: true).map { $0.x == 651 && $0.crossing } == true, "cornered: over the book to the other side")
            var st = Stroll(); st.rest = 0; var sx = 100.0
            st.tick(state: "contento", busy: false, x: &sx, lo: 0, hi: 1000, rng: { 0.1 }, visit: 520)
            check(st.target == 520, "now and then a walk ends beside the book")
            check(besideBook(x: 510, block: b) && besideBook(x: 650, block: b) && !besideBook(x: 400, block: b) && !besideBook(x: 510, block: nil), "beside the book: 9 to 40 pt of gap")
        }
        check(perchOf(host: nil, claudeID: 7, pos: Pt(400, 93), claude: nil) == ("floor", false, false) && perchOf(host: 7, claudeID: 7, pos: Pt(400, 500), claude: Rc(300, 200, 500, 300)).on == "claude"
              && perchOf(host: 9, claudeID: 7, pos: Pt(400, 500), claude: Rc(300, 200, 500, 300)).on == "other", "perch: floor, Claude's top, another window's top")
        check(perchOf(host: nil, claudeID: 7, pos: Pt(200, 93), claude: Rc(320, 50, 500, 600)).nearClaude && !perchOf(host: nil, claudeID: 7, pos: Pt(0, 93), claude: Rc(320, 50, 500, 600)).nearClaude
              && !perchOf(host: nil, claudeID: 7, pos: Pt(200, 93), claude: Rc(320, 400, 500, 300)).nearClaude
              && !perchOf(host: nil, claudeID: 7, pos: Pt(830, 93), claude: Rc(320, 50, 500, 600)).nearClaude, "perch: next to the left side of Claude's window (the wall ag_lean draws is on his right), only when it reaches down to him")
        check(claudeWindowClip(old: Rc(0, 0, 500, 400), new: nil, ridingIt: false) == "w_minimise" && claudeWindowClip(old: nil, new: Rc(0, 0, 500, 400), ridingIt: false) == "w_restore"
              && claudeWindowClip(old: Rc(0, 0, 500, 400), new: Rc(80, 0, 500, 400), ridingIt: false) == "w_chase" && claudeWindowClip(old: Rc(0, 0, 500, 400), new: Rc(80, 0, 500, 400), ridingIt: true) == nil
              && claudeWindowClip(old: Rc(0, 0, 500, 400), new: Rc(10, 0, 500, 400), ridingIt: false) == nil, "Claude's window: gone, back, or a jump he chases (not when he rides it, not a nudge)")
        check(callFlip(claude: Rc(100, 150, 400, 300), broteCx: 751) && !callFlip(claude: Rc(900, 150, 400, 300), broteCx: 751),
              "a call faces Claude's window: mirrored when it is on his left, as drawn when it is on his right (R-257)")
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
        bridge.setAmbient("quieto")                          // the pet's own pointing only: no idle clip (t_stale points too, with stale tasks)
        defer { bridge.setAmbient("vivo") }
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
    do { // libretaModel3 and the watch set (the round-3 page; a fixed clock and zone, fake names only)
        let t3: Dict = [
            "book.button": "Notebook", "book.live_chip": "live · last change {ago}", "book.badge_wait_one": "waits for you", "book.badge_wait_many": "wait for you",
            "book.badge_working": "working", "book.badge_resting": "resting", "book.h_week": "This week", "book.week_done": "{n} done", "book.week_letters": "MTWTFSS",
            "book.h_wait_one": "Waits for you", "book.h_wait_many": "Wait for you", "book.h_working": "Working", "book.needs": "needs {what}",
            "book.needs_permission": "your permission", "book.needs_question": "an answer", "book.needs_other": "something from you", "book.no_item": "no task claimed",
            "book.rest_one": "{n} session resting", "book.quiet_sessions": "{n} sessions resting", "book.rest_paused_one": "{n} task of theirs paused", "book.rest_paused_many": "{n} tasks of theirs paused",
            "book.progress": "{done} of {total} done · {pct} %", "book.progress_none": "Nothing done yet.", "book.h_next": "To start", "book.group_next": "{n} to start",
            "book.fold_quiet": "nobody working · {n} to start", "book.more_in": "and {n} more in {projects}", "book.join_and": "and", "book.h_done": "Done",
            "book.old": "Data from a while ago", "book.empty_doing": "Nothing running.", "book.empty_done": "Nothing done yet.", "book.no_live_data": "No live session data.",
            "book.today": "Today", "book.yesterday": "Yesterday", "book.parked_one": "{n} parked idea", "book.parked_many": "{n} parked ideas", "book.project_other": "other",
            "book.ago_s": "{n} s ago", "book.ago_min": "{n} min ago", "book.ago_h": "{n} h ago", "book.since_at": "since {hm}", "book.since_yesterday": "since yesterday", "book.since_day": "since {day}",
            "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]]
        let feedJSON = #"""
        {"version": 3, "contract": "3.1",
         "working": [
           {"kind": "session", "project": "taller", "title": "Invoice layout", "session_name": "Invoice layout", "session_status": "waiting", "waiting_for": "permission", "since": 1792744800},
           {"kind": "claim", "project": "studio", "item": "R-1", "title": "Claim title", "session_name": "Build lane", "session_status": "busy", "waiting_for": null, "since": 1792744900},
           {"kind": "session", "project": "huerto", "title": "Bed map", "session_name": "Bed map", "session_status": "busy", "waiting_for": null, "since": 1792743000},
           {"kind": "session", "project": null, "title": "Seed list", "session_name": "Seed list", "session_status": "busy", "waiting_for": null, "since": 1792742000}],
         "paused": [{"project": "studio", "item": "R-2", "title": "Old lane"}], "quiet_sessions": 3,
         "projects": [
           {"id": "studio", "name": "studio", "error": null, "counts": {"doing": 2, "next": 69, "later": 39, "done": 89}, "queue_recent": [{"id": "R-3", "title": "Next one"}], "last_change": 1792744955},
           {"id": "huerto", "name": "huerto", "error": null, "counts": {"doing": 0, "next": 17, "later": 0, "done": 5}, "queue_recent": [], "last_change": 1792743969},
           {"id": "recetario", "name": "recetario", "error": "ledger unreadable", "counts": null, "queue_recent": [], "last_change": 1792661629}],
         "watch": {"files": ["/h/a/ledger.json"], "check_s": 2}, "week": [9, 14, 11, 7, 0, 0, 0],
         "recent_done": [
           {"project": "studio", "item": "R-4", "title": "Done a", "done_at": "2026-10-23T09:12:40Z"},
           {"project": "taller", "item": "R-5", "title": "Done b", "done_at": "2026-10-23T08:47:02Z"},
           {"project": "studio", "item": "R-6", "title": "Done c", "done_at": "2026-10-22T17:30:11Z"},
           {"project": "huerto", "item": "R-7", "title": "Done d", "done_at": "2026-10-22T15:05:56Z"}]}
        """#
        var fx = (try? JSONSerialization.jsonObject(with: Data(feedJSON.utf8))) as? Dict ?? [:]
        check(libretaIsV3(fx) && !libretaIsV3(["version": 2]) && !libretaIsV3([:]), "model3: only a version 3 feed takes the new page, anything older keeps the old one")
        fx["t"] = t3
        let madrid = TimeZone(identifier: "Europe/Madrid")!, at = Date(timeIntervalSince1970: 1792748160)
        func m3(_ f: Dict) -> Libreta3Model { libretaModel3(feed: f, now: at, tz: madrid) }
        let m = m3(fx)
        check(m.badges.map { $0.n } == [1, 3, 3] && m.badges.map { $0.label } == ["waits for you", "working", "resting"], "model3: badges wait / working / resting")
        check(m.waits.count == 1 && m.waits[0].lead + m.waits[0].why == "needs your permission" && m.waits[0].ago == "56 min ago" && m.waits[0].project == "taller", "model3: the waiting row")
        check(m.works.map { $0.time } == ["09:53", "10:10", "10:41"], "model3: working rows in start order, with local time bubbles")
        check(m.works[0].project == "other" && m.works[2].itemId == "R-1" && m.works[2].itemText == "Claim title" && m.works[1].itemText == "no task claimed", "model3: a project-less row is tagged with the catalog's word, a claim shows its item")
        check(m.rest == "3 sessions resting \u{00B7} 1 task of theirs paused", "model3: the rest line")
        check(m.emptyWork == nil && m.emptyDone == nil, "model3: no empty-state lines while there are rows")
        check(libretaModel3(feed: fx, now: at, tz: madrid, old: true).chip == "Data from a while ago" && !m.chip.isEmpty && !libretaModel3(feed: fx, now: at, tz: madrid, old: true).chip.contains("live"), "model3: after a failed refresh the chip says the data is old, never live")
        var noSessions = fx
        noSessions["sources"] = ["claims": "ok", "sessions": "unreadable", "projects": 3, "skipped": 0, "capped": false] as Dict
        check(m3(noSessions).rest == "No live session data.", "model3: an unreadable session source says so in place of the rest line")
        var sound = fx; sound["sources"] = ["claims": "ok", "sessions": "ok"] as Dict
        check(m3(sound).rest == m.rest, "model3: a sound session source keeps the rest line")
        var empty = fx
        empty["working"] = [Dict](); empty["paused"] = [Dict](); empty["quiet_sessions"] = 0; empty["recent_done"] = [Dict]()
        let me = m3(empty)
        check(me.works.isEmpty && me.rest == nil && me.emptyWork == "Nothing running." && me.emptyDone == "Nothing done yet.", "model3: an empty Working and an empty Done layer each get their line")
        check(m.shelf.pots.map { $0.n } == [9, 14, 11, 7, 0, 0, 0] && m.shelf.today == 4 && m.shelf.pots[5].future && !m.shelf.pots[4].future && m.shelf.total == "41 done", "model3: the shelf marks today (Friday) and the days after it")
        check(m.shelf.caption == "94 of 221 done \u{00B7} 43 %" && m.shelf.fill == 26, "model3: progress caption and bar fill out of 60")
        check(m.tray.groups.count == 2 && m.tray.folds.isEmpty && m.tray.count == 86 && m.tray.groups[0].project == "studio" && m.tray.groups[0].header == "69 to start", "model3: the tray groups the active projects")
        check(m.tray.more == "and 85 more in studio and huerto", "model3: the tray's 'more' line names the projects left out")
        check(m.days.map { $0.label } == ["Today", "Yesterday"] && m.days.map { $0.rows.count } == [2, 2], "model3: done rows grouped by day")
        check(m.days.flatMap { $0.rows }.map { $0.time ?? "-" } == ["11:12", "10:47", "19:30", "17:05"], "model3: done time bubbles in the local zone")
        check(m.parked == "39 parked ideas \u{00B7} studio 39" && m.doneCount == 94 && m.chip == "live \u{00B7} last change 53 min ago", "model3: parked line, done count and the live chip")
        check(m3(fx) == m, "model3: the same feed gives an equal model (the no-change shortcut)")
        var f2 = fx; f2["working"] = (fx["working"] as? [Dict] ?? []).filter { ($0["project"] as? String) != "huerto" }
        let q = m3(f2)
        check(q.tray.groups.count == 1 && q.tray.folds.count == 1 && q.tray.folds[0].text == "nobody working \u{00B7} 17 to start" && q.tray.more == "and 68 more in studio", "model3: a project nobody works on folds, and the 'more' line leaves it out")
        var f3 = fx
        f3["recent_done"] = [["project": "studio", "item": "R-8", "title": "a", "done_at": "2026-10-23T08:47:10Z"], ["project": "studio", "item": "R-9", "title": "b", "done_at": "2026-10-23T08:47:50Z"], ["project": "huerto", "item": "R-10", "title": "c", "done_at": "2026-10-20T08:00:00Z"]]
        let d3 = m3(f3).days
        check(d3[0].rows.map { $0.time ?? "-" } == ["10:47", "-"] && d3.last?.label == "Tuesday", "model3: one minute prints its bubble once; an older day gets its weekday")
        var f4 = fx; f4["working"] = []; f4["quiet_sessions"] = 0; f4["paused"] = []
        let e = m3(f4)
        check(e.badges.count == 2 && e.waits.isEmpty && e.rest == nil, "model3: nobody working: no wait badge, no rest line")
        check(libretaModel3(feed: ["version": 3], now: at, tz: madrid).badges.count == 2, "model3: a feed with no words and no rows does not crash")
        // the watch set: stat() only, home-bound, at most 64 paths, runs spaced
        let home = tmp + "/", a = tmp + "/ledger.json"
        try? "{}".write(toFile: a, atomically: true, encoding: .utf8)
        var w = WatchSet()
        check(w.reset([a, "/etc/hosts", tmp + "/../x"], home: home) && w.paths == [a], "watch: only paths under home, never with ..")
        check(!w.changed(), "watch: nothing changed")
        try? "{\"a\":1}".write(toFile: a, atomically: true, encoding: .utf8)
        check(w.changed() && !w.changed(), "watch: an atomic rewrite is seen once")
        try? FileManager.default.removeItem(atPath: a)
        check(w.changed(), "watch: a deleted file is a change")
        try? "{}".write(toFile: a, atomically: true, encoding: .utf8)
        check(!w.reset([a, a], home: home) && w.changed(), "watch: a write during a feed run survives the reset; a repeated path is one path")
        check(!w.reset([a], home: home) && w.reset((0..<100).map { tmp + "/f\($0)" }, home: home) && w.paths.count == 64, "watch: the same list is not 'moved'; at most 64 paths")
        var pace = WatchPace()
        check(pace.delay(now: 10) == WatchPace.settle, "watch: the first run waits only the settle time")
        pace.lastRun = 10
        check(near(pace.delay(now: 11), 2.0) && pace.delay(now: 20) == WatchPace.settle, "watch: runs stay 3 s apart, an old run does not delay")
        check(WatchPace.every == 2.0, "watch: the files are checked every 2 s")
        // the timers are off while the panel is closed: none before the first open, none after a close, no run without the look
        let wt = WatchTimers()
        check(!wt.active && wt.tick == nil && wt.run == nil, "watch timers: a closed panel has no timer")
        wt.schedule(after: 0.01) { }
        check(wt.run == nil && !wt.active, "watch timers: a feed run cannot be scheduled while the panel is closed")
        var looks = 0, runs = 0
        wt.start(every: 0.01) { looks += 1 }
        let first = wt.tick
        wt.schedule(after: 0.02) { runs += 1 }
        check(wt.active && wt.pending && first?.isValid == true, "watch timers: an open panel has the look and the pending run")
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.15))
        check(looks >= 1 && runs == 1 && !wt.pending, "watch timers: the look repeats and a run fires once and clears itself")
        wt.start(every: 0.01) { looks += 1 }
        check(first?.isValid == false && wt.tick?.isValid == true, "watch timers: opening again leaves exactly one look")
        wt.schedule(after: 5) { runs += 1 }
        let last = wt.run
        wt.stop()
        let frozen = (looks, runs)
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.1))
        check(!wt.active && wt.tick == nil && wt.run == nil && last?.isValid == false && frozen == (looks, runs),
              "watch timers: after a close both timers are gone and nothing fires")
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

/// The rounded page the panel shows: the system's window colour (the round-3 paper for a version 3 feed), a hairline edge, and the content under a scroll view that never takes the key.
final class LibretaFrame: NSView {
    let scroll = NSScrollView()
    let content = LibretaContent(frame: .zero)
    let content3 = LibretaContent3(frame: .zero)
    /// true while the round-3 page is the document: its paper and edge replace the system's
    var paper = false {
        didSet {
            guard paper != oldValue else { return }
            scroll.documentView = paper ? content3 : content
            needsDisplay = true
        }
    }
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
            layer?.backgroundColor = (paper ? C3.paper : NSColor.windowBackgroundColor).cgColor
            layer?.borderColor = (paper ? C3.edge : NSColor.separatorColor).cgColor
        }
    }
    override func viewDidChangeEffectiveAppearance() { needsDisplay = true; content.needsDisplay = true; content3.needsDisplay = true }
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

// MARK: - the round-3 Libreta page (colours, fonts, pixel art, layout into items, drawing)

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: a)
}
func dyn(_ l: NSColor, _ d: NSColor) -> NSColor {
    NSColor(name: nil) { ap in ap.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? d : l }
}
func white(_ a: CGFloat) -> NSColor { NSColor(white: 1, alpha: a) }
func black(_ a: CGFloat) -> NSColor { NSColor(white: 0, alpha: a) }

/// Round-3 colour tokens, each a light/dark pair (values from the approved preview).
enum C3 {
    static let paper = dyn(rgb(0xfaf6ee), rgb(0x2a2622))
    static let ink = dyn(black(0.85), white(0.88))
    static let ink2 = dyn(black(0.6), white(0.62))
    static let ink3 = dyn(black(0.3), white(0.3))
    static let sep = dyn(rgb(0x3c280a, 0.10), white(0.09))
    static let edge = dyn(black(0.16), white(0.15))
    static let green = dyn(rgb(0x2fb350), rgb(0x30d158))
    static let shelf = dyn(rgb(0xf2e8d5), rgb(0x34302a))
    static let shelfRim = dyn(rgb(0xe3d4b8), rgb(0x463f35))
    static let wait = dyn(rgb(0xfff3d1), rgb(0x4a3d22))
    static let waitEdge = dyn(rgb(0xaa7814, 0.18), rgb(0xf0c86e, 0.32))
    static let tray = dyn(rgb(0xf1e9da), rgb(0x221f1b))
    static let card = dyn(rgb(0xfffdf9), rgb(0x312d28))
    static let vine = dyn(rgb(0x7fae6b), rgb(0x6f9a60))
    static let bub = dyn(rgb(0xe4f1dc), rgb(0x3a5532))
    static let bubTx = dyn(rgb(0x2d5a22), rgb(0xcfeac4))
    static let hbub = dyn(rgb(0xefe9df), rgb(0x3d3832))
    static let tagTx = dyn(black(0.78), white(0.86))
    static let liftA = dyn(rgb(0x5a3c14, 0.06), black(0.25))     // 0 1 1
    static let liftB = dyn(rgb(0x5a3c14, 0.08), black(0.28))     // 0 4 14
    static let inner = dyn(rgb(0x3c280a, 0.08), black(0.35))
    /// Brote's four soft tag colours; a project keeps one by a stable hash of its name (no project names in this file: it is exported).
    static let tags: [NSColor] = [dyn(rgb(0xe4dbf7), rgb(0x3d3557)), dyn(rgb(0xd6e7f6), rgb(0x2a3f52)),
                                  dyn(rgb(0xfbdbe4), rgb(0x4f2f3a)), dyn(rgb(0xdcefd6), rgb(0x2f4430))]
    static let tagOther = dyn(rgb(0xe6e3ea), rgb(0x38363d))
    static func tag(_ name: String, other: String) -> NSColor {
        if name == other || name.isEmpty { return tagOther }
        return tags[name.unicodeScalars.reduce(0) { $0 &+ Int($1.value) } % tags.count]
    }
}

/// The display face: SF Pro Rounded (the approved "Redondeada").
func roundedFont(_ size: CGFloat, _ w: NSFont.Weight) -> NSFont {
    let f = NSFont.systemFont(ofSize: size, weight: w)
    if let d = f.fontDescriptor.withDesign(.rounded), let r = NSFont(descriptor: d, size: size) { return r }
    return f
}

// pixel art, same grids as the preview
let PIX_PAL: [Character: NSColor] = [
    "l": rgb(0xcfd6a0), "L": rgb(0xa5ad70), "s": rgb(0xa5ad70), "n": rgb(0x8e9ee0), "N": rgb(0x6b79bd), "y": rgb(0xf5d683),
    "o": dyn(rgb(0xa493c4), rgb(0x8b7bb0)), "P": dyn(rgb(0xddd5e6), white(0.1)), "r": rgb(0xec7f94), "R": rgb(0xc95c74),
    "c": rgb(0xf8bccb), "C": rgb(0xf292ab), "W": rgb(0xfffaf2), "p": rgb(0xf7a8c0), "m": rgb(0x8a7a6e), "k": rgb(0xfcdbe4),
    "g": rgb(0x7fb069), "G": rgb(0x5a8f4a), "z": rgb(0xb9b2c8)]
enum Pix {
    static let doing = ["..l..l..", ".lLl.lL.", ".lLLllL.", "..LsLL..", "...ss...", "...ss...", "...ss...", "........"]
    static let next = ["........", "...nn...", "..nNNn..", ".nnNNnn.", ".nnnNnn.", "..nnnn..", "...nn...", "........"]
    static let done = ["........", "......gg", ".....gG.", "g...gG..", "gG.gG...", ".GgG....", "..G.....", "........"]
    static let later = ["..yy....", ".yy.....", "yy......", "yy......", "yy......", ".yy.....", "..yy....", "........"]
    static let rest = ["........", "...zzz..", "..z.....", ".z......", ".z......", "..z...z.", "...zzz..", "........"]
    static let wait = ["........", ".oooooo.", "oWWWWWWo", "oWyWyWyo", "oWWWWWWo", ".ooooWo.", "....oo..", "........"]
    static let pot = ["CmmmmmmC", ".CcckcC.", ".CcccCC.", "..CCCC.."]
    static let plant: [[String]] = [
        ["........", "........", "........", "........", "........", "........"],
        ["........", "........", "........", "........", "...ll...", "...s...."],
        ["........", "........", ".ll..ll.", "..lLLl..", "...ss...", "...ss..."],
        ["....ll..", "...lL...", ".ll.s...", "..lLs.l.", "...sLl..", "...s...."],
        ["..p.p...", "...y....", "..p.p.l.", "...s.lL.", ".lLs.s..", "...s...."]]
    static func bar(_ fill: Int) -> [String] {
        (0..<5).map { y in String((0..<62).map { x -> Character in
            (y == 0 || y == 4 || x == 0 || x == 61) ? "o" : (x <= fill ? (y == 3 ? "L" : "l") : "P") }) }
    }
}

/// The Libreta round-3 page: laid out from the model and drawn straight into the view (no subviews), 380 pt wide. LibretaFrame owns the corner and the edge.
final class LibretaContent3: NSView {
    enum Item {
        case text(NSAttributedString, CGRect)
        case pix([String], CGFloat, CGPoint)
        case round(NSColor, CGRect, CGFloat)               // plain rounded fill
        case ring(NSColor, CGRect, CGFloat)                // 1 pt inset hairline
        case card(NSColor, CGRect, CGFloat)                // fill + the soft layer shadow, drawn once
        case sunken(NSColor, CGRect, CGFloat)              // fill + inner shadow
        case vine(CGFloat, CGFloat, CGFloat)               // x, y0, y1
        case dashed(NSColor, CGFloat, CGFloat, CGFloat)    // x0, x1, y
        case rule(NSColor, CGRect)                         // flat bar (shelf rim)
    }
    var items: [Item] = []
    var other = ""
    override var isFlipped: Bool { true }
    override var isOpaque: Bool { false }

    private func font(_ s: CGFloat, _ w: NSFont.Weight = .regular, mono: Bool = false, round: Bool = false, digits: Bool = false) -> NSFont {
        if mono { return NSFont.monospacedSystemFont(ofSize: s, weight: w) }
        if round { return roundedFont(s, w) }
        return digits ? NSFont.monospacedDigitSystemFont(ofSize: s, weight: w) : NSFont.systemFont(ofSize: s, weight: w)
    }
    private func attr(_ s: String, _ f: NSFont, _ c: NSColor, right: Bool = false, center: Bool = false) -> NSAttributedString {
        let p = NSMutableParagraphStyle(); p.lineBreakMode = .byTruncatingTail
        if right { p.alignment = .right }
        if center { p.alignment = .center }
        return NSAttributedString(string: s, attributes: [.font: f, .foregroundColor: c, .paragraphStyle: p])
    }
    /// text centred on `cy`; returns the width it wants (capped to `w`)
    @discardableResult private func txt(_ s: String, _ f: NSFont, _ c: NSColor, x: CGFloat, cy: CGFloat, w: CGFloat, right: Bool = false, center: Bool = false) -> CGFloat {
        let a = attr(s, f, c, right: right, center: center), sz = a.size()
        items.append(.text(a, CGRect(x: x, y: (cy - sz.height / 2).rounded(), width: w, height: ceil(sz.height) + 1)))
        return min(ceil(sz.width), w)
    }
    private func width(_ s: String, _ f: NSFont) -> CGFloat { ceil(attr(s, f, .black).size().width) }
    private func pix(_ rows: [String], _ unit: CGFloat, x: CGFloat, y: CGFloat) { items.append(.pix(rows, unit, CGPoint(x: x, y: y))) }

    /// pill with centred text; returns its width
    @discardableResult private func pill(_ s: String, _ f: NSFont, _ tx: NSColor, _ bg: NSColor, x: CGFloat, cy: CGFloat, h: CGFloat, padX: CGFloat, minW: CGFloat = 0, maxW: CGFloat = 400, alignRight: Bool = false) -> CGFloat {
        let w = min(maxW, max(minW, width(s, f) + 2 * padX))
        let px = alignRight ? x - w : x
        items.append(.round(bg, CGRect(x: px, y: cy - h / 2, width: w, height: h), h / 2))
        txt(s, f, tx, x: px + padX, cy: cy, w: w - 2 * padX, center: true)
        return w
    }
    @discardableResult private func tag(_ name: String, x: CGFloat, cy: CGFloat, maxW: CGFloat = 200) -> CGFloat {
        pill(name, font(11), C3.tagTx, C3.tag(name, other: other), x: x, cy: cy, h: 17, padX: 7, maxW: maxW)
    }

    func build(model m: Libreta3Model, other: String, width W: CGFloat) -> CGFloat {
        items = []; self.other = other
        let L: CGFloat = 18, R = W - 18, IW = R - L
        var y: CGFloat = 20

        // header: title + live chip
        txt(m.title, font(24, .semibold, round: true), C3.ink, x: L + 2, cy: y + 13, w: 150)
        if !m.chip.isEmpty {
            let f = font(11.5), cw = width(m.chip, f)
            txt(m.chip, f, C3.ink2, x: R - 2 - cw, cy: y + 13, w: cw + 2)
            pix([".g.", "ggg", ".g."], 3, x: R - 2 - cw - 6 - 9, y: y + 13 - 4.5)
        }
        y += 26 + 12

        // badges
        var bx = L
        for b in m.badges {
            let nf = font(13, .bold, round: true), lf = font(12)
            let numS = String(b.n), icon: [String] = b.kind == .wait ? Pix.wait : b.kind == .work ? Pix.doing : Pix.rest
            let tx = b.kind == .wait ? C3.ink : b.kind == .work ? C3.bubTx : C3.ink2
            let w = 7 + 16 + 6 + width(numS, nf) + 6 + width(b.label, lf) + 10, cy = y + 12
            switch b.kind {
            case .wait: items.append(.round(C3.wait, CGRect(x: bx, y: y, width: w, height: 24), 12)); items.append(.ring(C3.waitEdge, CGRect(x: bx, y: y, width: w, height: 24), 12))
            case .work: items.append(.round(C3.bub, CGRect(x: bx, y: y, width: w, height: 24), 12))
            case .rest: items.append(.ring(C3.sep, CGRect(x: bx, y: y, width: w, height: 24), 12))
            }
            pix(icon, 2, x: bx + 7, y: cy - 8)
            var tx0 = bx + 7 + 16 + 6
            txt(numS, nf, tx, x: tx0, cy: cy, w: 40); tx0 += width(numS, nf) + 6
            txt(b.label, lf, tx, x: tx0, cy: cy, w: 200)
            bx += w + 6
        }
        y += 24 + 16

        // shelf (Esta semana): tinted band, pots on a rim, progress under
        let s = m.shelf, sTop = y, shelfIdx = items.count
        var sy = y + 12
        txt(s.heading, font(15, .semibold, round: true), C3.ink, x: L + 14, cy: sy + 9, w: 200)
        txt(s.total, font(12), C3.ink2, x: R - 14 - 120, cy: sy + 9, w: 120, right: true)
        sy += 18 + 8
        let px0 = L + 12 + 4, step = (IW - 24 - 8 - 7 * 24) / 6 + 24
        for (i, p) in s.pots.enumerated() {
            let x = px0 + CGFloat(i) * step
            if !p.future { txt(String(p.n), font(11, .regular, digits: true), C3.ink2, x: x - 6, cy: sy + 7, w: 36, center: true) }
            pix(Pix.plant[p.future ? 0 : potLevel(p.n)] + Pix.pot, 3, x: x, y: sy + 16)
        }
        sy += 16 + 30
        items.append(.rule(C3.shelfRim, CGRect(x: L + 12, y: sy, width: IW - 24, height: 3)))
        sy += 3 + 5
        for (i, l) in s.letters.enumerated() where i < 7 {
            let x = px0 + CGFloat(i) * step
            let f = font(11, i == s.today ? .bold : .regular)
            txt(l, f, i == s.today ? C3.ink : C3.ink2, x: x, cy: sy + 8, w: 24, center: true)
        }
        sy += 17 + 12
        let cap = font(12), capW = width(s.caption, cap)
        if let fill = s.fill {
            pix(Pix.bar(fill), 3, x: L + 12, y: sy)
            if 186 + 10 + capW <= IW - 24 { txt(s.caption, cap, C3.ink2, x: L + 12 + 186 + 10, cy: sy + 7.5, w: capW + 2); sy += 15 }
            else { sy += 15 + 6; txt(s.caption, cap, C3.ink2, x: L + 12, cy: sy + 9, w: IW - 24); sy += 18 }
        } else { txt(s.caption, cap, C3.ink2, x: L + 12, cy: sy + 9, w: IW - 24); sy += 18 }
        sy += 12
        items.insert(.round(C3.shelf, CGRect(x: L, y: sTop, width: IW, height: sy - sTop), 14), at: shelfIdx)
        y = sy + 18

        // Te espera: the raised card
        if !m.waits.isEmpty {
            let top = y, cardIdx = items.count
            var wy = y + 12
            pix(Pix.wait, 3, x: L + 14, y: wy)
            txt(m.waitHeading, font(15, .semibold, round: true), C3.ink, x: L + 14 + 24 + 8, cy: wy + 12, w: 200)
            countBubble(String(m.waits.count), right: R - 14, cy: wy + 12)
            wy += 24 + 8
            for (i, w) in m.waits.enumerated() {
                if i > 0 { items.append(.dashed(C3.waitEdge, L + 14, R - 14, wy)) }
                txt(w.title, font(13.5, .semibold), C3.ink, x: L + 14, cy: wy + 6 + 10, w: IW - 28)
                let cy = wy + 6 + 20 + 3 + 9
                var x = L + 14
                x += tag(w.project, x: x, cy: cy) + 7
                let need = font(12), strong = font(12, .semibold)
                x += txt(w.lead, need, C3.ink2, x: x, cy: cy, w: R - 14 - x)
                x += txt(w.why, strong, C3.ink, x: x, cy: cy, w: max(0, R - 14 - x))
                txt(w.tail + " \u{00B7} " + w.ago, need, C3.ink2, x: x, cy: cy, w: max(0, R - 14 - x))
                wy += 54
            }
            wy += 8 - 7
            items.insert(.card(C3.wait, CGRect(x: L, y: top, width: IW, height: wy - top + 7), 14), at: cardIdx)
            items.insert(.ring(C3.waitEdge, CGRect(x: L, y: top, width: IW, height: wy - top + 7), 14), at: cardIdx + 1)
            y = wy + 7 + 22
        } else { y += 4 }

        // Trabajando: the vine, straight on the paper
        let vx = L + 2
        pix(Pix.doing, 3, x: vx, y: y)
        txt(m.workHeading, font(15, .semibold, round: true), C3.ink, x: vx + 24 + 8, cy: y + 12, w: 200)
        countBubble(String(m.works.count), right: R - 2, cy: y + 12)
        y += 24 + 8 + 4
        let vTop = y, vineIdx = items.count
        let bubCX = vx + 22, tx0 = vx + 44 + 10, tw = R - 2 - tx0
        for w in m.works {
            let bw = width(w.time, font(10.5, .semibold, digits: true)) + 12
            items.append(.round(C3.paper, CGRect(x: bubCX - bw / 2 - 3, y: y + 6 - 3, width: bw + 6, height: 24), 12))
            pill(w.time, font(10.5, .semibold, digits: true), C3.bubTx, C3.bub, x: bubCX - bw / 2, cy: y + 15, h: 18, padX: 6)
            txt(w.title, font(13.5, .semibold), C3.ink, x: tx0, cy: y + 5 + 10, w: tw)
            let cy = y + 5 + 20 + 2 + 9
            var x = tx0
            x += tag(w.project, x: x, cy: cy) + 7
            if let id = w.itemId {
                x += txt(id, font(11, .regular, mono: true), C3.ink2, x: x, cy: cy, w: 50) + 4
                txt(w.itemText, font(12), C3.ink2, x: x, cy: cy, w: max(0, R - 2 - x))
            } else { txt(w.itemText, font(12), C3.ink2, x: x, cy: cy, w: max(0, R - 2 - x)) }
            y += 53
        }
        if let r = m.rest {
            items.append(.round(C3.paper, CGRect(x: bubCX - 14, y: y + 2 + 12 - 14, width: 28, height: 28), 8))
            pix(Pix.rest, 3, x: bubCX - 12, y: y + 2)
            txt(r, font(12), C3.ink2, x: tx0, cy: y + 2 + 12, w: tw)
            y += 2 + 24
        }
        if let e = m.emptyWork { txt(e, font(12), C3.ink2, x: tx0, cy: y + 12, w: tw); y += 24 }
        let vEnd = y - (m.rest != nil ? 14 : 14)
        items.insert(.vine(bubCX, vTop + 12, max(vTop + 12, vEnd)), at: vineIdx)
        y += 22

        // Por empezar: the sunken tray
        let t = m.tray, tTop = y, trayIdx = items.count
        var ty = y + 12
        pix(Pix.next, 3, x: L + 14, y: ty)
        txt(t.heading, font(15, .semibold, round: true), C3.ink, x: L + 14 + 24 + 8, cy: ty + 12, w: 200)
        txt(String(t.count), font(12, .regular, digits: true), C3.ink2, x: R - 14 - 60, cy: ty + 12, w: 60, right: true)
        ty += 24 + 8
        for (gi, g) in t.groups.enumerated() {
            if gi > 0 { ty += 10 }
            var x = L + 14
            x += tag(g.project, x: x, cy: ty + 9) + 8
            txt(g.header, font(11.5, .regular, digits: true), C3.ink2, x: x, cy: ty + 9, w: R - 14 - x)
            ty += 18 + 2
            for it in g.items {
                txt(it.id, font(11, .regular, mono: true), C3.ink2, x: L + 14, cy: ty + 13.5, w: 40)
                txt(it.title, font(13), C3.ink, x: L + 14 + 48, cy: ty + 13.5, w: IW - 28 - 48)
                ty += 27
            }
        }
        for f in t.folds {
            ty += 10
            items.append(.dashed(C3.sep, L + 14, R - 14, ty))
            ty += 1 + 9
            var x = L + 14
            x += tag(f.project, x: x, cy: ty + 9) + 8
            txt(f.text, font(12), C3.ink2, x: x, cy: ty + 9, w: R - 14 - x)
            ty += 18
        }
        if let mo = t.more { ty += 8; txt(mo, font(11.5), C3.ink2, x: L + 14, cy: ty + 8.5, w: IW - 28); ty += 17 }
        ty += 10
        items.insert(.sunken(C3.tray, CGRect(x: L, y: tTop, width: IW, height: ty - tTop), 14), at: trayIdx)
        y = ty + 18

        // Hecho: the light card
        let dTop = y, doneIdx = items.count
        var dy = y + 12
        pix(Pix.done, 3, x: L + 14, y: dy)
        txt(m.doneHeading, font(15, .semibold, round: true), C3.ink, x: L + 14 + 24 + 8, cy: dy + 12, w: 200)
        txt(String(m.doneCount), font(12, .regular, digits: true), C3.ink2, x: R - 14 - 60, cy: dy + 12, w: 60, right: true)
        dy += 24 + 8
        for d in m.days {
            dy += 2; txt(d.label, font(11.5, .semibold), C3.ink2, x: L + 14, cy: dy + 8.5, w: 200); dy += 17 + 2
            for r in d.rows {
                pix(Pix.done, 2, x: L + 14, y: dy + 5 + 2)
                txt(r.title, font(13), C3.ink, x: L + 14 + 24, cy: dy + 5 + 9, w: 240)
                tag(r.project, x: L + 14 + 24, cy: dy + 5 + 18 + 2 + 8.5, maxW: 240)
                if let tm = r.time { pill(tm, font(11, .semibold, digits: true), C3.ink2, C3.hbub, x: R - 14, cy: dy + 5 + 9, h: 18, padX: 6, alignRight: true) }
                dy += 47
            }
        }
        if let e = m.emptyDone { txt(e, font(12), C3.ink2, x: L + 14, cy: dy + 8, w: IW - 28); dy += 24 }
        dy += 8 - 5
        items.insert(.card(C3.card, CGRect(x: L, y: dTop, width: IW, height: dy - dTop), 14), at: doneIdx)
        y = dy + 18

        // footer
        if let p = m.parked {
            pix(Pix.later, 3, x: L + 2, y: y)
            txt(p, font(12), C3.ink2, x: L + 2 + 24 + 8, cy: y + 12, w: IW - 36)
            y += 24
        }
        y += 24
        frame.size = NSSize(width: W, height: y)
        setAccessibilityLabel(m.title)
        needsDisplay = true
        return y
    }

    private func countBubble(_ s: String, right: CGFloat, cy: CGFloat) {
        pill(s, font(11, .bold, digits: true), C3.paper, C3.ink, x: right, cy: cy, h: 18, padX: 6, minW: 20, alignRight: true)
    }

    // MARK: drawing
    override func draw(_ dirtyRect: NSRect) {
        guard let g = NSGraphicsContext.current?.cgContext else { return }
        C3.paper.setFill(); bounds.fill()
        for it in items {
            switch it {
            case .rule(let c, let r): c.setFill(); r.fill()
            case .round(let c, let r, let rad): c.setFill(); NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad).fill()
            case .ring(let c, let r, let rad):
                c.setStroke(); let p = NSBezierPath(roundedRect: r.insetBy(dx: 0.5, dy: 0.5), xRadius: rad - 0.5, yRadius: rad - 0.5); p.lineWidth = 1; p.stroke()
            case .card(let c, let r, let rad):
                g.saveGState()
                // two soft shadows, the hairline one first; y grows downward on screen in this flipped view
                for (col, dy, blur) in [(C3.liftB, CGFloat(4), CGFloat(14)), (C3.liftA, CGFloat(1), CGFloat(1))] {
                    let sh = NSShadow(); sh.shadowColor = col; sh.shadowBlurRadius = blur; sh.shadowOffset = NSSize(width: 0, height: -dy)
                    NSGraphicsContext.saveGraphicsState(); sh.set(); c.setFill()
                    NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad).fill(); NSGraphicsContext.restoreGraphicsState()
                }
                g.restoreGState()
                c.setFill(); NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad).fill()
            case .sunken(let c, let r, let rad):
                c.setFill(); let p = NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad); p.fill()
                g.saveGState(); p.addClip()
                let hole = NSBezierPath(rect: r.insetBy(dx: -40, dy: -40)); hole.append(p); hole.windingRule = .evenOdd
                let sh = NSShadow(); sh.shadowColor = C3.inner; sh.shadowBlurRadius = 3; sh.shadowOffset = NSSize(width: 0, height: -1)
                sh.set(); NSColor.black.setFill(); hole.fill()
                g.restoreGState()
            case .vine(let x, let y0, let y1):
                C3.vine.setStroke(); let p = NSBezierPath(); p.move(to: NSPoint(x: x, y: y0)); p.line(to: NSPoint(x: x, y: y1))
                p.lineWidth = 2; p.lineCapStyle = .round; p.setLineDash([0.01, 4], count: 2, phase: 0); p.stroke()
            case .dashed(let c, let x0, let x1, let y):
                c.setStroke(); let p = NSBezierPath(); p.move(to: NSPoint(x: x0, y: y + 0.5)); p.line(to: NSPoint(x: x1, y: y + 0.5))
                p.lineWidth = 1; p.setLineDash([3, 3], count: 2, phase: 0); p.stroke()
            case .pix(let rows, let u, let o):
                for (yy, row) in rows.enumerated() { for (xx, ch) in row.enumerated() where ch != "." {
                    PIX_PAL[ch]?.setFill(); NSRect(x: o.x + CGFloat(xx) * u, y: o.y + CGFloat(yy) * u, width: u, height: u).fill() } }
            case .text(let a, let r): a.draw(with: r, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine])
            }
        }
    }
}

/// `--libreta-png <feed.json> <existing folder> [--now <epoch>] [--tz <id>] [--lang-feed]`: the round-3 page as the panel shows it (paper, rounded corner, hairline edge),
/// light and dark at 1x and 2x: light@1x.png, light@2x.png, dark@1x.png, dark@2x.png. No window, no child, no network; the words are the feed's own `t` (what --lang-feed names, so it is always on).
func runLibretaPng(feed path: String, out: String, now: Date?, tz: TimeZone?) -> Never {
    var isDir: ObjCBool = false
    guard let data = FileManager.default.contents(atPath: path), let feed = (try? JSONSerialization.jsonObject(with: data)) as? Dict, feed["t"] is Dict,
          FileManager.default.fileExists(atPath: out, isDirectory: &isDir), isDir.boolValue else {
        fputs("libreta-png: needs a feed JSON that carries `t`, and an existing output folder\n", stderr); exit(1)
    }
    _ = NSApplication.shared
    let model = libretaModel3(feed: feed, now: now ?? Date(), tz: tz ?? .current)
    let other = (feed["t"] as? Dict)?["book.project_other"] as? String ?? ""
    let W = CGFloat(K.libretaW), rad = CGFloat(K.libretaRadius)
    for dark in [false, true] {
        let ap = NSAppearance(named: dark ? .darkAqua : .aqua)!
        let v = LibretaContent3(frame: NSRect(x: 0, y: 0, width: W, height: 100))
        let h = v.build(model: model, other: other, width: W)
        v.appearance = ap
        for z in [1, 2] {
            func newRep() -> NSBitmapImageRep {
                let r = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(W) * z, pixelsHigh: Int(h) * z, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                         colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
                r.size = NSSize(width: W, height: h)
                return r
            }
            let page = newRep(), framed = newRep()
            ap.performAsCurrentDrawingAppearance { v.cacheDisplay(in: v.bounds, to: page) }
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: framed)
            ap.performAsCurrentDrawingAppearance {
                let box = NSRect(x: 0, y: 0, width: W, height: h)
                NSGraphicsContext.saveGraphicsState()
                NSBezierPath(roundedRect: box, xRadius: rad, yRadius: rad).addClip()
                page.draw(in: box)
                NSGraphicsContext.restoreGraphicsState()
                C3.edge.setStroke()
                let e = NSBezierPath(roundedRect: box.insetBy(dx: 0.25, dy: 0.25), xRadius: rad, yRadius: rad); e.lineWidth = 0.5; e.stroke()
            }
            NSGraphicsContext.restoreGraphicsState()
            guard let png = framed.representation(using: .png, properties: [:]) else { fputs("libreta-png: no PNG\n", stderr); exit(1) }
            let name = (dark ? "dark" : "light") + "@\(z)x.png"
            do { try png.write(to: URL(fileURLWithPath: out).appendingPathComponent(name)) } catch { fputs("libreta-png: \(name): \(error)\n", stderr); exit(1) }
            print("wrote \(name) (\(Int(W)) x \(Int(h)) pt)")
        }
    }
    exit(0)
}

/// `--record <folder>`: the scripted scene drawn as numbered PNG frames (record-gif.sh and record-gif.py turn them into the README GIF).
/// CoreGraphics at 2x like a Retina screen, his pixels unsmoothed. No window, no screen capture, no permission.
func runRecord(assets: String, out: String) -> Never {
    let (bOpt, why) = PetBridge.load(dir: assets)
    guard let bridge = bOpt else { fputs("record: \(why)\n", stderr); exit(1) }
    try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
    let frames = recordScene(bridge: bridge)
    let z = Rec.zoom, W = Int(Rec.size.x * z), H = Int(Rec.size.y * z), sp = CGColorSpace(name: CGColorSpace.sRGB)!
    func col(_ v: UInt32, _ a: CGFloat = 1) -> CGColor { CGColor(srgbRed: CGFloat((v >> 16) & 255) / 255, green: CGFloat((v >> 8) & 255) / 255, blue: CGFloat(v & 255) / 255, alpha: a) }
    func cg(_ r: Rc) -> CGRect { CGRect(x: r.x, y: r.y, width: r.w, height: r.h) }
    // the mock's colours are his own (ink, outline, cream, leaf, pink, butter, sky), so the windows look like his world
    let paper = col(0xf6f1ea), dot = col(0xe9e0d3), lift = col(0xe8dfd2), sheet = col(0xfffaf2), band = col(0xf2edfc), edge = col(0x9d8bcc)
    let bar = col(0xddd5e6), barDark = col(0xc9c1d1)
    func rounded(_ c: CGContext, _ r: CGRect, _ rad: CGFloat, fill: CGColor) { c.addPath(CGPath(roundedRect: r, cornerWidth: rad, cornerHeight: rad, transform: nil)); c.setFillColor(fill); c.fillPath() }
    func backdrop(_ c: CGContext) {
        c.setFillColor(paper); c.fill(CGRect(x: 0, y: 0, width: Rec.size.x, height: Rec.size.y))
        c.setFillColor(dot)
        for y in stride(from: 8.0, to: Rec.size.y, by: 16) { for x in stride(from: 8.0, to: Rec.size.x, by: 16) { c.fillEllipse(in: CGRect(x: x - 0.8, y: y - 0.8, width: 1.6, height: 1.6)) } }
        c.setFillColor(lift); c.fill(CGRect(x: 0, y: 0, width: Rec.size.x, height: Rec.floorY))                  // the floor he lands on
    }
    /// A window as a flat sticker: a hard offset shadow, a cream sheet, a lavender title band and outline, three dots in his pink, butter and leaf.
    func window(_ c: CGContext, _ r: CGRect) {
        rounded(c, r.offsetBy(dx: 4, dy: -4), 7, fill: lift)
        rounded(c, r, 7, fill: sheet)
        c.saveGState(); c.addPath(CGPath(roundedRect: r, cornerWidth: 7, cornerHeight: 7, transform: nil)); c.clip()
        c.setFillColor(band); c.fill(CGRect(x: r.minX, y: r.maxY - 20, width: r.width, height: 20))
        c.setFillColor(edge); c.fill(CGRect(x: r.minX, y: r.maxY - 20.75, width: r.width, height: 0.75))
        c.restoreGState()
        for (i, v) in [UInt32(0xec7f94), 0xf5d683, 0x86b57c].enumerated() { c.setFillColor(col(v)); c.fillEllipse(in: CGRect(x: r.minX + 9 + Double(i) * 11, y: r.maxY - 13.5, width: 7, height: 7)) }
        c.addPath(CGPath(roundedRect: r.insetBy(dx: 0.75, dy: 0.75), cornerWidth: 6.5, cornerHeight: 6.5, transform: nil)); c.setStrokeColor(edge); c.setLineWidth(1.5); c.strokePath()
    }
    /// The chat: the question on the right; the reply waits as three typing dots, then grows line by line while he types.
    func chat(_ c: CGContext, t: Double, work: Double?) {
        let r = cg(Rec.claude.r)
        window(c, r)
        let q = CGRect(x: r.maxX - 112, y: r.maxY - 46, width: 98, height: 18)
        rounded(c, q, 9, fill: col(0xe0d6f5)); rounded(c, CGRect(x: q.minX + 11, y: q.midY - 2.5, width: 62, height: 5), 2.5, fill: col(0xc6b6ea))
        let x0 = r.minX + 14
        if let w = work {
            for (i, len) in [150.0, 118, 136].enumerated() {
                let shown = min(len, max(0, (w - Double(i) * 0.75) * 170))
                if shown > 0 { rounded(c, CGRect(x: x0, y: r.maxY - 62 - Double(i) * 12, width: shown, height: 5), 2.5, fill: barDark) }
            }
        } else {
            for i in 0..<3 {
                let k = 0.5 + 0.5 * sin((t * 2.4 - Double(i) * 0.33) * 2 * .pi)          // a soft wave across the three dots
                c.setFillColor(col(0x9d8bcc, 0.35 + 0.65 * k)); c.fillEllipse(in: CGRect(x: x0 + Double(i) * 9, y: r.maxY - 64 + k * 1.5, width: 5, height: 5))
            }
        }
    }
    /// The window that opens over him: an address pill, a picture (sky, sun, two hills) and text lines, no words.
    func browser(_ c: CGContext, _ r: CGRect) {
        window(c, r)
        rounded(c, CGRect(x: r.midX - 52, y: r.maxY - 14, width: 104, height: 8), 4, fill: bar)
        let pic = CGRect(x: r.minX + 14, y: r.maxY - 112, width: r.width - 28, height: 80)
        c.saveGState(); c.addPath(CGPath(roundedRect: pic, cornerWidth: 4, cornerHeight: 4, transform: nil)); c.clip()
        c.setFillColor(col(0x9cc4e6)); c.fill(pic)
        c.setFillColor(col(0xf5d683)); c.fillEllipse(in: CGRect(x: pic.maxX - 58, y: pic.minY + 44, width: 22, height: 22))
        c.setFillColor(col(0xb0d6a6)); c.fillEllipse(in: CGRect(x: pic.minX - 30, y: pic.minY - 70, width: 190, height: 110))
        c.setFillColor(col(0x86b57c)); c.fillEllipse(in: CGRect(x: pic.minX + 100, y: pic.minY - 80, width: 200, height: 110))
        c.restoreGState()
        for (i, len) in [200.0, 168, 214, 120, 0, 190, 150].enumerated() where len > 0 {
            rounded(c, CGRect(x: r.minX + 14, y: pic.minY - 18 - Double(i) * 12, width: len, height: 5), 2.5, fill: i == 0 ? barDark : bar)
        }
    }
    var pics: [CGImage] = []
    for f in frames {
        guard let c = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W * 4, space: sp, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { continue }
        c.scaleBy(x: z, y: z)
        backdrop(c)
        chat(c, t: f.t, work: f.work)
        if let cr = f.cover {
            let k = min(1, cr.w / Rec.cover.r.w * 2.5 - 1.5)                  // fades in while it grows from 60 %
            c.saveGState(); c.setAlpha(max(0.15, k)); c.beginTransparencyLayer(auxiliaryInfo: nil)
            c.translateBy(x: cr.x, y: cr.y); c.scaleBy(x: cr.w / Rec.cover.r.w, y: cr.h / Rec.cover.r.h); c.translateBy(x: -Rec.cover.r.x, y: -Rec.cover.r.y)
            browser(c, cg(Rec.cover.r)); c.endTransparencyLayer(); c.restoreGState()
        }
        if let img = makeImage(f.grid, flipped: f.flip) {
            c.saveGState(); c.interpolationQuality = .none; c.setShouldAntialias(false)
            c.draw(img, in: CGRect(x: f.pos.x, y: f.pos.y, width: K.spriteW, height: K.spriteH)); c.restoreGState()
        }
        if let img = c.makeImage() { pics.append(img) }
    }
    for (n, img) in pics.enumerated() {
        var out1 = img
        let k = Double(n - (pics.count - Rec.blend)) / Double(Rec.blend + 1)
        if k > 0, let c = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W * 4, space: sp, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) {
            let all = CGRect(x: 0, y: 0, width: W, height: H)
            c.draw(img, in: all); c.setAlpha(1 - pow(1 - k, 2)); c.draw(pics[0], in: all)          // the loop's seam: an ease-out fade into the first frame
            out1 = c.makeImage() ?? img
        }
        guard let d = CGImageDestinationCreateWithURL(URL(fileURLWithPath: out + String(format: "/%03d.png", n)) as CFURL, "public.png" as CFString, 1, nil) else { continue }
        CGImageDestinationAddImage(d, out1, nil); CGImageDestinationFinalize(d)
    }
    var spriteColours = Set<String>()                                          // his exact colours: the GIF's palette keeps them
    for f in frames { for case let c? in f.grid.cells where c.a == 255 { spriteColours.insert(String(format: "%02x%02x%02x", c.r, c.g, c.b)) } }
    try? spriteColours.sorted().joined(separator: "\n").write(toFile: out + "/sprite-colours.txt", atomically: true, encoding: .utf8)
    print("record: \(frames.count) frames in \(out)")
    exit(0)
}

func uptime() -> Double { ProcessInfo.processInfo.systemUptime }
func mousePt() -> Pt { let m = NSEvent.mouseLocation; return Pt(Double(m.x), Double(m.y)) }

func windowList() -> [WinInfo] {
    guard let arr = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return [] }
    return arr.compactMap { d in
        guard let pid = d[kCGWindowOwnerPID as String] as? Int, let layer = d[kCGWindowLayer as String] as? Int,
              let bd = d[kCGWindowBounds as String] as? [String: Any], let r = CGRect(dictionaryRepresentation: bd as CFDictionary) else { return nil }
        return WinInfo(pid: pid, layer: layer, bounds: Rc(Double(r.origin.x), Double(r.origin.y), Double(r.width), Double(r.height)),
                       id: d[kCGWindowNumber as String] as? Int ?? 0, alpha: d[kCGWindowAlpha as String] as? Double ?? 1)
    }
}

/// Where the sprite runtime and the other resources are: the app bundle, or `--assets` in a scenario run.
var resDir = Bundle.main.resourcePath ?? "."

/// `--scenario`: a runner (tools/brote/float/scenario/scenario.py) opens test windows and sends commands on stdin; he writes a JSON-lines
/// trace for its checks. Nothing of the user's is touched: an in-memory store, no data line, no hook file, the probe log next to the trace.
final class Scenario {
    let t0 = uptime()
    var speed = 1.0, claude: Int?, hover: Bool?
    var lastClip: String?, lastAct: String?, lastCall: String?, lastFlip = false, ticks = 0
    let trace: FileHandle?
    let probePath: String
    init(trace path: String?) {
        let p = path ?? NSTemporaryDirectory() + "brote-scenario.jsonl"
        FileManager.default.createFile(atPath: p, contents: Data())
        trace = FileHandle(forWritingAtPath: p)
        probePath = (p as NSString).deletingLastPathComponent + "/probe.log"
    }
    /// The idle clocks (trips, time away) run `speed` times faster; physics and routes stay real time.
    func idleNow() -> Double { t0 + (uptime() - t0) * speed }
    func write(_ ev: String, _ fields: [String: Any] = [:]) {
        var d = fields; d["ev"] = ev; d["t"] = ((uptime() - t0) * 1000).rounded() / 1000
        guard let data = try? JSONSerialization.data(withJSONObject: d, options: [.sortedKeys]) else { return }
        trace?.write(data + Data("\n".utf8))
    }
    /// Commands on stdin, one per line, run on the main thread; the end of stdin quits.
    func listen(_ f: @escaping (String) -> Void) {
        Thread.detachNewThread {
            while let l = readLine() { DispatchQueue.main.async { f(l) } }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }
}

final class Brote: NSObject {
    let store: Store
    let scenario: Scenario?
    let probe: Probe
    let glue: PetGlue
    let layout: Layout
    let panel = HostPanel(size: NSSize(width: K.winW, height: K.winH))
    let view: SpriteView
    var plug: PluginLayout?, projectArg: String?          // where budgit is (a dev clone or the installed plugin) and the project it is asked about
    var installedPlugin: PluginLayout?                    // the trusted --plugin-root, with or without the data line (the "Remove Brote" item needs it)
    var life: LifecycleState?, lastLiveCount = -1, removing = false, lastProject: String?
    let dataOn: Bool
    let eventsOn: Bool                                   // read the plugin's event file (--no-events turns it off)
    var eventsOffset: UInt64 = 0
    var lastFront: Bool?, lastReduce: Bool?
    static let home = brHome(), configEnv = brConfigEnv()
    static let dirs = BroteDirs(home: home)
    static let configDir = brConfigDir(home: home)
    static let eventsPath = dirs.events
    var dragger = Dragger()
    var stroll = Stroll()
    var pressedDown = false
    // window tops: what the last look saw, the trip he is on, and the body clips the app plays meanwhile
    var wins: [SWin] = []
    var claudePIDs = Set<Int>()
    var route: Route?, routeTarget: Plat?, routeArrive: String?, routeClip: String?, routeMirror = false
    var routeTimer: Timer?, lastRouteStep = 0.0
    var explorer = Explorer(lastTrip: uptime())               // no trip in the first 90 s after launch
    var falling = false, riding = false, rideUntil = 0.0
    var chaseAgain = 0.0, hoverSince: Double?, longHovered = false, lastPerch = "", lastWalkDx: Int?
    var bookHidden = false, scootTo: Double?, bookLookAgain = 0.0, fetchTo: Double?, claudeGone = false
    var wheee = false, flightVX = 0.0                                   // the flight face is on (from the throw to the first bump or the landing)
    var pendingFall: DispatchWorkItem?
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
    var p19Left = 60
    var libretaModelShown: LibretaModel?, libretaModel3Shown: Libreta3Model?, libretaPalKey = "", feedOld = false, feedFresh = false, panelTouched = false
    // the live page: the files the feed names, checked with stat() every 2 s while the panel is open (no timer exists while it is closed)
    var watch = WatchSet(), pace = WatchPace(), watchTimers = WatchTimers()
    var status: Dict?
    var updating = false
    var statusBusy = false, docsProbed = false
    var hitTimer: Timer?, flightTimer: Timer?
    var lastFlight = 0.0, lastOrigin: NSPoint?
    var cpuMark = (wall: uptime(), cpu: 0.0), flightSeconds = 0.0, cpuFirst = true, touched = false, threadMark: [ThreadCPU] = []

    init(args: [String], policyAccepted: Bool) {
        scenario = args.contains("--scenario") ? Scenario(trace: args.firstIndex(of: "--trace").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }) : nil
        store = scenario == nil ? DefaultsStore() : MemStore()
        probe = Probe(path: scenario?.probePath ?? Brote.dirs.probe, privateFolder: scenario == nil)
        probe.reset()
        dataOn = !args.contains("--no-data") && scenario == nil
        eventsOn = !args.contains("--no-events") && scenario == nil
        let (bridge, why) = PetBridge.load(dir: resDir)
        guard let b = bridge else { fputs("brote: " + why + "\n", stderr); probe.plain("start failed: the sprite runtime did not load"); exit(1) }
        glue = PetGlue(bridge: b)
        let screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        layout = Layout(store: store, screens: screens, claude: nil)
        view = SpriteView(frame: NSRect(x: 0, y: 0, width: K.winW, height: K.winH))
        bookView = BookView(frame: NSRect(x: 0, y: 0, width: K.bookW, height: K.bookH))
        place = BookPlace(store: store)
        icon = BookIcon(dir: resDir)
        super.init()
        view.owner = self
        panel.contentView = view
        bookView.owner = self
        bookPanel.contentView = bookView
        bookPanel.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)      // above him; the Libreta panel sits below both
        if icon == nil { probe.plain("book-icon.json missing or unreadable: no book") }

        // where budgit lives: the installed plugin (`--plugin-root <version folder under <config>/plugins/cache>`, optionally `--project <dir>`) or a dev clone
        // (`--hub <clone>`, stage 2 only). Either is used only if it is mine and not writable by others; the data line also needs --no-data to be absent.
        if scenario == nil, let pr = argValue(args, "--plugin-root") {
            if let l = trustedPluginRoot(pr, configDir: Brote.configDir) { installedPlugin = l }
            else { probe.plain("plugin root refused (not under the config folder's plugins/cache, not a folder Claude Code registered for the plugin, or bin/budgit, bin, scripts or a .py there is missing, not mine, or writable by others)") }
        }
        if dataOn {
            if let l = installedPlugin {
                plug = l
                projectArg = argValue(args, "--project").flatMap { projectArgument($0) }       // the fallback; a live session's project wins at every refresh
            } else if args.contains("--plugin-root") {
                // refused above: the data line stays off
            } else if let h = argValue(args, "--hub") {
                let u = URL(fileURLWithPath: h).resolvingSymlinksInPath().path
                if hubTrusted(u) { plug = PluginLayout.hub(u); projectArg = u } else { probe.plain("hub refused (plugin/bin/budgit, plugin/bin, plugin/scripts or a .py there is missing, not mine, or writable by others)") }
            } else { probe.plain("no --hub and no --plugin-root: the data line stays off") }
        }

        probe.log("P4", [("policyAccepted", String(policyAccepted)), ("activationPolicy", String(NSApp.activationPolicy().rawValue))])
        probe.log("START", [("src", buildStamp(try? String(contentsOfFile: resDir + "/build.txt", encoding: .utf8))), ("dataOn", String(dataOn)), ("hub", plug.map { $0.isRoot ? "plugin" : "dev" } ?? "none"), ("pid", String(getpid()))])
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
        if scenario == nil, args.contains("--plugin-root") || args.contains("--lifecycle") { life = LifecycleState(startedAt: uptime(), watchPlugin: args.contains("--plugin-root")) }
        schedule()
        if dataOn { refreshStatus() }
        probeHost(); probeResources()
        scenario?.listen { [unowned self] line in self.command(line) }
    }

    static func rc(_ r: NSRect) -> Rc { Rc(Double(r.origin.x), Double(r.origin.y), Double(r.width), Double(r.height)) }

    // MARK: timers
    func schedule() {
        func every(_ s: Double, _ f: @escaping () -> Void) { let t = Timer(timeInterval: s, repeats: true) { _ in f() }; t.tolerance = s * 0.2; RunLoop.main.add(t, forMode: .common) }
        every(0.1) { [unowned self] in timed(0) { self.tick() } }
        every(0.5) { [unowned self] in timed(2) { self.claudePoll() } }
        every(K.statusEvery) { [unowned self] in if self.dataOn { self.refreshStatus() } }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [unowned self] _ in self.look() }
        every(60) { [unowned self] in self.probeHost(); self.probeResources() }
        if life != nil { every(LifecycleK.sessionPoll) { [unowned self] in self.lifecycleTick() } }
        every(2) { [unowned self] in self.senseIdle() }
        every(60) { [unowned self] in self.senseBattery() }
        senseBattery()
        scheduleHit(0.05)
    }
    /// Seconds since the last keyboard or mouse input (CGEventSource: no permission, no prompt).
    func senseIdle() {
        guard scenario == nil else { return }
        let any = CGEventType(rawValue: ~0)!
        glue.bridge.setIdle(seconds: Int(CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: any)))
    }
    /// The internal battery's charge and whether it is on power (IOKit power sources: no permission, no prompt). A Mac without a battery: never low.
    func senseBattery() {
        guard scenario == nil else { return }
        var percent: Int?, onPower = true
        if let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(), let list = IOPSCopyPowerSourcesList(info)?.takeRetainedValue() as? [CFTypeRef] {
            for ps in list {
                guard let d = IOPSGetPowerSourceDescription(info, ps)?.takeUnretainedValue() as? [String: Any],
                      (d[kIOPSTypeKey] as? String) == kIOPSInternalBatteryType else { continue }
                if let c = d[kIOPSCurrentCapacityKey] as? Int, let m = d[kIOPSMaxCapacityKey] as? Int, m > 0 { percent = c * 100 / m }
                onPower = (d[kIOPSPowerSourceStateKey] as? String) == kIOPSACPowerValue
            }
        }
        glue.bridge.setBattery(low: batteryLow(percent: percent, onPower: onPower))
    }
    func scheduleHit(_ s: Double) {
        hitTimer?.invalidate()
        let t = Timer(timeInterval: s, repeats: false) { [unowned self] _ in timed(1) { self.hitPoll() } }
        RunLoop.main.add(t, forMode: .common); hitTimer = t
    }

    // MARK: the 10 fps tick
    func tick() {
        let reps = max(1, Int(scenario?.speed ?? 1))                // a scenario's speed: the tick-counted idle clocks run that many ticks
        if route != nil {
        } else if dragger.isRest {
            let r = bookLedge()
            var x = layout.pos.x
            let visit = layout.host == nil && bookOnFloor ? bookBlockNow.map { x <= ($0.lo + $0.hi) / 2 ? $0.lo - 4 : $0.hi + 4 } : nil   // beside the book, 13 pt of gap
            for _ in 0..<reps { stroll.tick(state: glue.bridge.state, busy: glue.busy || pressedDown || glue.bridge.calm, x: &x, lo: r.lo, hi: r.hi, rng: { Double.random(in: 0..<1) }, visit: visit) }
            if x != layout.pos.x { layout.pos.x = x; positionWindows() }
            glue.walking = stroll.walking
            fetchStep()
        } else { glue.walking = false; fetchTo = nil }
        if layout.host != nil { timed(2) { look() } }       // 10 Hz while he stands on a window, so he follows it
        explore()
        if place.on, let bp = place.pos { sideLeft = bookOnLeft(bookOn: true, prevLeft: sideLeft, bookCx: bp.x + K.bookW / 2, broteCx: layout.pos.x + K.spriteW / 2) }
        var key: String?
        timed(3) { for _ in 0..<reps { key = glue.step() ?? key } }
        walkClip()
        bookRules()
        rebuildFrame(key: key)
        refreshBook()
        let perch = perchOf(host: layout.host?.id, claudeID: layout.claudeID, pos: layout.pos, claude: layout.claude)
        let beside = layout.host == nil && bookOnFloor && besideBook(x: layout.pos.x, block: bookBlockNow)
        if "\(perch)\(beside)\(libretaVisible)" != lastPerch { lastPerch = "\(perch)\(beside)\(libretaVisible)"; glue.bridge.setPerch(perch, book: beside, libreta: libretaVisible) }   // only on change
        if glue.hover && !glue.held {
            let now = uptime()
            if let h = hoverSince { if !longHovered && now - h >= K.longHover { longHovered = true; glue.bridge.play("w_longhover") } } else { hoverSince = now }
        } else { hoverSince = nil; longHovered = false }
        if let s = scenario { traceTick(s) }
    }

    /// A walking clip (sf_top, w_chase, ag_walk...) draws no shift of its own: he moves by it along his ledge, so it never jumps at its start or end.
    func walkClip() {
        guard let w = glue.bridge.walkDx() else { lastWalkDx = nil; return }
        if let last = lastWalkDx, w != last, route == nil, dragger.isRest, !falling, scootTo == nil {
            let r = bookLedge()
            layout.pos.x = clamp(layout.pos.x + Double(w - last) * K.scale * (cachedFlip ? -1 : 1), r.lo, r.hi); positionWindows()
        }
        lastWalkDx = w
    }

    // MARK: Brote and the book: two bodies that never overlap
    var bookBlockNow: (lo: Double, hi: Double)? { place.pos.flatMap { bookBlock(book: $0, broteY: layout.pos.y) } }
    /// The book lies on the floor of its screen (not held, not flying, not left floating mid-screen).
    var bookOnFloor: Bool { guard let p = place.pos, bookDrag.isRest else { return false }; return abs(p.y - bookBounds(visibleFrame(for: p)).floorY) < 1 }
    func bookLedge() -> (lo: Double, hi: Double) { let r = layout.ledge(); return ledgeAroundBook(lo: r.lo, hi: r.hi, x: layout.pos.x, block: bookBlockNow) }
    /// One 10 Hz step of the book rules: he moves aside when the book comes near (dragged at him, dropped on him, or he landed on it),
    /// and the real book hides while he holds it (never two books).
    func bookRules() {
        guard place.on else { return }
        if glue.bridge.holding != bookHidden {
            bookHidden = glue.bridge.holding
            if bookHidden { bookPanel.orderOut(nil) } else { positionBook(); bookPanel.orderFrontRegardless() }
            scenario?.write("book", ["shown": !bookHidden])
        }
        guard dragger.isRest, route == nil, !falling, pendingFall == nil, !bookHidden else { scootTo = nil; return }
        let r = layout.ledge()
        if scootTo == nil, let out = clearOfBook(x: layout.pos.x, block: bookBlockNow, lo: r.lo, hi: r.hi, dragging: bookDrag.isHeld) {
            scootTo = out.x
            glue.bridge.play(out.crossing ? "sf_fly" : bookDrag.isHeld ? "bk_dodge" : "bk_shoved")
            scenario?.write("dodge", ["to": out.x, "crossing": out.crossing])
        }
        if let to = scootTo {
            let d = to - layout.pos.x
            layout.pos.x += abs(d) <= K.scootStep ? d : (d > 0 ? K.scootStep : -K.scootStep); positionWindows()
            if abs(to - layout.pos.x) < 0.5 { scootTo = nil }
        }
    }
    /// The book landed after a throw: if he is free on the same floor, he startles ("!"), walks over to it and sits down beside it.
    func startFetch() {
        guard layout.host == nil, dragger.isRest, route == nil, !falling, pendingFall == nil, !glue.held, Stroll.pace(glue.bridge.state) != nil,
              bookOnFloor, let b = bookBlockNow else { return }
        let x = layout.pos.x, r = bookLedge(), spot = x <= (b.lo + b.hi) / 2 ? b.lo - 4 : b.hi + 4    // his side of the book, as the stroll's visit
        guard spot >= r.lo, spot <= r.hi, abs(spot - x) >= K.strollMin, glue.bridge.fetch(sit: false) else { return }
        fetchTo = spot; stroll.target = spot
        scenario?.write("fetch", ["to": spot])
    }
    /// One tick of a fetch: the book taken away ends it; once there he sits down beside it.
    func fetchStep() {
        guard let to = fetchTo else { return }
        if !bookOnFloor { fetchTo = nil; if stroll.target == to { stroll.target = nil }; return }
        guard stroll.target == nil else { return }
        fetchTo = nil
        if abs(layout.pos.x - to) < 1 { glue.bridge.fetch(sit: true) }
    }
    /// The pointer came onto the book or left it: he looks at it, curious (not while he reads, at most every 2 s).
    func bookHoverChanged(_ on: Bool) {
        guard on, !glue.bridge.reading, !bookHidden, !glue.held, uptime() >= bookLookAgain else { return }
        bookLookAgain = uptime() + 2
        glue.bridge.play("bk_look")
    }

    /// He mirrors toward a book on his left while he points; otherwise the walking-facing rule.
    func currentFlip(pointing: Bool) -> Bool {
        if route != nil { return routeMirror }
        if wheee { return flightVX < 0 }                                // the flight art faces right
        if glue.bridge.calling, let c = layout.claude { return callFlip(claude: c, broteCx: layout.pos.x + K.spriteW / 2) }
        if glue.bridge.bookClip, let b = place.pos { return b.x + K.bookW / 2 < layout.pos.x + K.spriteW / 2 }   // book clips draw the book on his right
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
        if libretaIsV3(st) { return fillLibreta3(st, frame: frame, force: force) }
        if frame.paper { frame.paper = false; libretaModel3Shown = nil }
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
    /// The round-3 page (feed version 3). Same contract as fillLibreta: false when nothing changed.
    func fillLibreta3(_ st: Dict, frame: LibretaFrame, force: Bool) -> Bool {
        let model = libretaModel3(feed: st, now: Date(), old: feedOld)
        if !force, model == libretaModel3Shown, frame.paper { return false }
        let t0 = uptime()
        frame.paper = true
        let c = frame.content3, top = frame.scroll.contentView.bounds.origin.y
        let h = c.build(model: model, other: (st["t"] as? Dict)?["book.project_other"] as? String ?? "", width: CGFloat(K.libretaW))
        c.frame.size.height = max(h, frame.scroll.bounds.height)
        frame.scroll.contentView.scroll(to: NSPoint(x: 0, y: min(top, max(0, h - frame.scroll.bounds.height))))
        if p19Left > 0, let rep = c.bitmapImageRepForCachingDisplay(in: c.bounds) {      // P19: layout plus one full draw
            c.cacheDisplay(in: c.bounds, to: rep); p19Left -= 1
            probe.log("P19", [("ms", String(Int((uptime() - t0) * 1000))), ("rows", String(model.works.count + model.waits.count)), ("h", String(Int(h)))], cap: 60)
        }
        libretaModel3Shown = model; libretaModelShown = nil; libretaPalKey = "v3"
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
        glue.libretaOpened()
        let slide: CGFloat = reduceMotion ? 0 : 14 * ((layout.claude.map { $0.center.x < r.x + r.w / 2 } ?? true) ? -1 : 1)
        p.setFrame(end.offsetBy(dx: slide, dy: 0), display: false)      // size first: the page is laid out against the scroll view's height
        fillLibreta(force: true)
        p.alphaValue = 0
        p.orderFrontRegardless()
        if let up = libretaAskedAt { probe.log("P17", [("ms", String(Int((uptime() - up) * 1000)))], cap: 60) }
        startWatch()
        if created { probe.log("P1", [("level", String(p.level.rawValue)), ("panels", String(visiblePanels())), ("ignoring", String(p.ignoresMouseEvents))]) }
        probe.log("BOOK", [("kind", "libreta"), ("open", "true")], cap: 200)
        NSAnimationContext.runAnimationGroup { c in
            c.duration = reduceMotion ? 0.12 : 0.2
            c.timingFunction = CAMediaTimingFunction(controlPoints: 0.23, 1, 0.32, 1)
            p.animator().alphaValue = 1
            if slide != 0 { p.animator().setFrame(end, display: true) }
        }
    }

    /// Open panel only: look at once, then every 2 s ask stat() whether a watched file moved; a change runs the feed once, after the writer settled and not within 3 s of the last run.
    func startWatch() {
        guard dataOn, plug != nil else { return }
        refreshStatus()
        watchTimers.start(every: WatchPace.every) { [unowned self] in
            guard self.libretaVisible else { self.watchTimers.stop(); return }      // a closed panel is never watched
            guard !self.watchTimers.pending, self.watch.changed() else { return }
            self.scheduleWatchRun(after: self.pace.delay(now: uptime()))
        }
    }
    func scheduleWatchRun(after delay: Double) {
        watchTimers.schedule(after: delay) { [unowned self] in
            guard self.libretaVisible else { return }
            if self.statusBusy { self.scheduleWatchRun(after: 1.0) } else { self.refreshStatus() }      // a run in flight: look again soon, the change must not be lost
        }
    }
    func stopWatch() { watchTimers.stop() }

    func closeLibreta() {
        guard libretaVisible, let p = libretaPanel else { return }
        libretaVisible = false; libretaClosing = true
        stopWatch()
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
            startFetch()
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
            if hov != bookHover { bookHover = hov; refreshBook(); bookHoverChanged(hov) }
        }
        if let lp = libretaPanel, lp.isVisible {
            let want = !libretaOverPointer(m)
            if lp.ignoresMouseEvents != want { lp.ignoresMouseEvents = want }
        }
        if dragger.isRest, !pressedDown { glue.setHover(scenario?.hover ?? (target == .brote)) }      // a scenario says where the pointer is
        var near = Rc(p.x, p.y, K.spriteW, K.spriteH).inflated(40).contains(m)
        if let br = bookRect, br.inflated(40).contains(m) { near = true }               // the fast poll covers both bodies
        if let lp = libretaPanel, lp.isVisible, lp.frame.insetBy(dx: -20, dy: -20).contains(NSPoint(x: m.x, y: m.y)) { near = true; panelTouched = true }       // and the open panel
        if near { touched = true }
        scheduleHit(near || pressedDown || bookPressedDown || dragger.isFlying || bookDrag.isFlying || NSEvent.pressedMouseButtons != 0 ? 1.0 / 60 : 1.0 / 20)
    }

    // MARK: the scenario runner's commands (see tools/brote/float/scenario/scenario.py)
    func command(_ line: String) {
        let p = line.split(separator: " ").map(String.init)
        guard let c = p.first else { return }
        func num(_ i: Int) -> Double? { i < p.count ? Double(p[i]) : nil }
        scenario?.write("cmd", ["line": line])
        switch c {
        case "mood": if p.count > 1 { glue.setData(state: p[1], h5: 0, stale: 0) }
        case "idle": if let s = num(1) { glue.bridge.setIdle(seconds: Int(s)) }                 // seconds since the last keyboard or mouse input
        case "battery": glue.bridge.setBattery(low: p.count > 1 && p[1] == "low")
        case "feed":                                                                         // feed <clip,clip|-> <done>: as if the data line changed
            let clips = p.count > 1 && p[1] != "-" ? p[1].split(separator: ",").map(String.init) : []
            glue.bridge.setFeed(clips: clips, done: Int(num(2) ?? 0), quiet: false, runsOut: false)
        case "claude": scenario?.claude = p.count > 1 ? Int(p[1]) : nil; look()
        case "place":
            readWindows()
            guard p.count > 2, let id = Int(p[1]), let rx = num(2), let w = wins.first(where: { $0.id == id }) else { return }
            let at = Pt(w.r.x + rx, w.r.maxY)
            guard let top = supportUnder(cx: spriteCentre(at).x, feet: at.y, plats: layout.plats), top.win == id else {
                scenario?.write("error", ["line": line, "why": "no open top there"]); return     // covered, or a full-screen window hides every top
            }
            stopFlight(); stopRoute(); pendingFall?.cancel(); pendingFall = nil
            layout.land(at: at, on: top, wins: wins)
            positionWindows(); look()
        case "floor":
            guard let x = num(1) else { return }
            stopFlight(); stopRoute()
            layout.land(at: Pt(x, layout.screens.first?.minY ?? 0), on: nil, wins: wins); positionWindows()
        case "drop":
            guard let x = num(1), let y = num(2) else { return }
            stopFlight(); stopRoute(); layout.leave()
            layout.pos = layout.clamped(Pt(x, y)); positionWindows(); readWindows()
            fall(clip: nil, cause: "drop")
        case "throw":
            guard let vx = num(1), let vy = num(2) else { return }
            stopFlight(); stopRoute(); readWindows()
            startThrow(Pt(vx, vy))
        case "click": _ = glue.click(now: uptime(), away: 1)
        case "hover": scenario?.hover = p.count > 1 && p[1] == "1"; glue.setHover(scenario?.hover ?? false)
        case "book":                                                   // the book on the floor at x, on screen
            guard icon != nil, let x = num(1) else { scenario?.write("error", ["line": line, "why": "no book art or no x"]); return }
            let fy = bookBounds(layout.screens.first ?? Rc(0, 0, 1440, 900)).floorY
            place.land(at: Pt(x, fy), screens: layout.screens); glue.bookOn = true; bookHidden = false
            refreshBook(); positionBook(); bookPanel.orderFrontRegardless()
        case "bookdrag":                                               // the book is held and moved it to x (as a drag does)
            guard let x = num(1), let bp = place.pos else { return }
            if !bookDrag.isHeld { bookDrag.press(at: Pt(bp.x + 30, bp.y + 30), origin: bp, now: uptime()) }
            _ = bookDrag.drag(to: Pt(x + 30, bp.y + 30), now: uptime()); place.move(to: Pt(x, bp.y)); positionBook()
        case "bookdrop":                                               // the book is let go at x (a slow release: it stays there)
            guard let x = num(1), let bp = place.pos else { return }
            if bookDrag.isHeld { _ = bookDrag.release(now: uptime()) }
            place.land(at: Pt(x, bp.y), screens: layout.screens); positionBook()
        case "bookthrow":                                              // the book is thrown from where it is with this speed (points per second)
            guard let vx = num(1), let vy = num(2), let bp = place.pos else { return }
            bookDrag.fly(Body(p: bp, v: Pt(vx, vy)), in: bookBounds(visibleFrame(for: bp)), dizzy: false); startBookFlight()
        case "bookhover": bookHoverChanged(p.count > 1 && p[1] == "1")
        case "read": glue.bridge.startReading()
        case "play": if p.count < 2 || !glue.bridge.play(p[1]) { scenario?.write("error", ["line": line, "why": "no such clip"]) }
        case "event": glue.bridge.feed(p.dropFirst().joined(separator: " ") + "\n")
        case "speed": scenario?.speed = max(1, num(1) ?? 1)
        case "quit": NSApp.terminate(nil)
        default: scenario?.write("error", ["line": line])
        }
    }

    /// One tick of the trace: what started playing (director clip, act, click reaction) and how a call shows (facing, sign readable).
    func traceTick(_ s: Scenario) {
        s.ticks += 1
        if layout.host == nil, s.ticks % 2 == 0 { traceLook(s, support: "none") }       // on the floor look() runs seldom: his spot every 0.2 s
        if let n = glue.bridge.now() {
            if n.clip != s.lastClip { s.lastClip = n.clip; if let c = n.clip { s.write("act", ["name": c]) } }
            let a = n.act == "poke" ? (n.poke ?? "poke") : n.act
            if a != s.lastAct { s.lastAct = a; if let a = a { s.write("act", ["name": a]) } }
        }
        let call = glue.bridge.calling ? glue.bridge.pendingCall()?.kind : nil
        if call != s.lastCall || (call != nil && cachedFlip != s.lastFlip) {
            s.lastCall = call; s.lastFlip = cachedFlip
            if let k = call { s.write("frame", ["flip": cachedFlip, "call": k, "glyph_ok": cachedGrid.map { glyphsReadRight($0, flipped: cachedFlip) } ?? false]) }
        }
    }

    // MARK: the mouse
    func pressed() {
        touched = true
        stopFlight(); stopRoute()
        pendingFall?.cancel(); pendingFall = nil
        if falling || riding { glue.bridge.stopClip(); falling = false; riding = false }
        explorer.awaySince = nil                          // moved by hand: wherever he is put is home for now
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
            if let call = glue.bridge.pendingCall(), callClicked(call) { return }       // a permission call brings Claude to the front, the update call asks
            let m = mousePt(), away = m.x < layout.pos.x + K.spriteW / 2 ? 1 : -1       // agotado runs away from the pointer
            let c = glue.click(now: uptime(), away: away)
            probe.log("CLICK", [("result", String(describing: c))], cap: 200)
            if unsupported() { fall(clip: nil, cause: "click") }       // a click stopped him mid-climb or mid-hop
            return
        }
        if isThrow(speed: v.speed) {
            startThrow(v)
            probe.log("THROW", [("speed", String(Int(v.speed))), ("tops", String(layout.plats.count))], cap: 200)
        } else {
            fall(clip: nil, cause: "drop")                   // a gentle drop: straight down onto whatever is under him
        }
    }

    /// A click on the call he is showing. True when it was answered here (no poke then).
    func callClicked(_ c: (kind: String, sid: String)) -> Bool {
        switch c.kind {
        case "perm": openChat(sid: c.sid); glue.bridge.touch(); probe.log("CALLCLICK", [("kind", "perm"), ("link", String(K.chatOpensByLink))], cap: 200); return true
        case "update": glue.bridge.touch(); probe.log("CALLCLICK", [("kind", "update")], cap: 200); askUpdate(); return true
        default: return false
        }
    }
    /// The chat that asked, when the hand test showed the link lands on it; otherwise only bring Claude to the front.
    func openChat(sid: String) {
        if K.chatOpensByLink, let link = chatLink(prefix: sid, records: readChatRecords(home: Brote.home)), let url = URL(string: link) { NSWorkspace.shared.open(url); return }
        NSRunningApplication.runningApplications(withBundleIdentifier: K.claudeBundle).first?.activate(options: [])
    }
    func updateWords() -> [String: String] { (status?["t"] as? Dict ?? [:]).compactMapValues { $0 as? String } }
    /// The one native question; the update runs only on the yes. No words from the catalog, no dialog.
    func askUpdate() {
        guard !updating, let o = updateOffer(status) else { return }
        let t = updateWords()
        guard let title = t["brote.update_title"], let yes = t["brote.update_yes"], let no = t["brote.update_no"] else { return }
        let a = NSAlert()
        a.messageText = fillT(title, ["v": o.version]); a.informativeText = t["brote.update_body"] ?? ""
        a.addButton(withTitle: yes); a.addButton(withTitle: no)
        NSApp.activate(ignoringOtherApps: true)
        let yesPicked = a.runModal() == .alertFirstButtonReturn
        glue.bridge.dismissUpdate()                          // either answer ends the call; the menu keeps offering it
        probe.log("UPDATE", [("answer", yesPicked ? "yes" : "no"), ("v", o.version)], cap: 200)
        if yesPicked { runUpdate(o) }
    }
    func runUpdate(_ o: UpdateOffer) {
        updating = true
        let home = Brote.home, cfg = Brote.configEnv, t = updateWords()
        DispatchQueue.global(qos: .utility).async { [weak self] in
            var ok = false
            if let c = findClaude(home: home, exists: { FileManager.default.isExecutableFile(atPath: $0) }) {
                ok = true
                for argv in updateArgvs(claude: c, offer: o) {
                    let r = runChild(argv, env: updateEnv(home: home, configDir: cfg), timeout: 180)
                    self?.probe.log("UPDATE", [("step", argv.dropFirst(2).joined(separator: " ")), ("exit", String(r.exit)), ("timedOut", String(r.timedOut)), ("ms", String(r.ms))], cap: 200)
                    if r.exit != 0 || r.timedOut { ok = false; break }
                }
            }
            DispatchQueue.main.async {
                guard let me = self else { return }
                me.updating = false
                let a = NSAlert()
                a.messageText = ok ? fillT(t["brote.update_done"], ["v": o.version]) : fillT(t["brote.update_fail"], ["commands": updateText(offer: o)])
                a.addButton(withTitle: "OK")
                NSApp.activate(ignoringOtherApps: true)
                a.runModal()
            }
        }
    }
    @objc func menuUpdate() { askUpdate() }

    /// Nothing holds him where he is: no window top at his feet and above the screen floor.
    func unsupported() -> Bool {
        layout.host == nil && layout.pos.y > layout.flightBounds().floorY + 0.5
            && supportUnder(cx: spriteCentre(layout.pos).x, feet: layout.pos.y, plats: layout.plats).map { abs($0.y - layout.pos.y) >= 1 } ?? true
    }

    /// Gravity takes him: from a closed, moved or covered window (after a short beat with its clip), or a drop (at once).
    func fall(clip: String?, cause: String) {
        let from = layout.host
        layout.leave(); riding = false
        if let c = clip { glue.bridge.play(c) }
        falling = clip != nil
        let go = DispatchWorkItem { [unowned self] in
            self.pendingFall = nil
            guard self.dragger.isRest, !self.pressedDown else { return }
            if cause == "covered", let h = from, let top = self.stillHeld(on: h.id) {        // the window moved off his spot during the pause: no fall
                self.layout.land(at: self.layout.pos, on: top, wins: self.wins)
                self.falling = false; self.glue.bridge.stopClip()
                self.probe.log("CANCEL", [("cause", cause), ("x", String(Int(self.layout.pos.x))), ("y", String(Int(self.layout.pos.y)))], cap: 200)
                self.scenario?.write("cancel", ["cause": cause])
                return
            }
            if self.layout.pos.y - (self.layout.screens.first?.minY ?? 0) >= K.glideFrom, Stroll.lively(self.glue.bridge.state), self.glue.bridge.play("sf_glide") {
                self.falling = true                                         // a long drop in a good mood: he glides on his leaf; landing ends it
            }
            self.dragger.fly(Body(p: self.layout.pos, v: Pt(0, 0)), in: self.layout.flightBounds(), dizzy: false, plats: self.layout.plats)
            self.startFlight()
        }
        probe.log("FALL", [("cause", cause), ("x", String(Int(layout.pos.x))), ("y", String(Int(layout.pos.y)))], cap: 200)
        scenario?.write("fall", ["cause": cause, "pos": [layout.pos.x, layout.pos.y], "tops": layout.plats.map { [$0.win, $0.x0, $0.x1, $0.y] }])
        if clip == nil { go.perform() } else { pendingFall = go; DispatchQueue.main.asyncAfter(deadline: .now() + K.fallPause, execute: go) }
    }

    /// A fresh look at the windows: the top of window `id` under his feet when it holds him again (nothing in front of his spot), else nil.
    func stillHeld(on id: Int) -> Plat? {
        readWindows()
        guard supportCheck(host: id, centre: spriteCentre(layout.pos).x, wins: wins) == .stay else { return nil }
        return supportUnder(cx: spriteCentre(layout.pos).x, feet: layout.pos.y, plats: layout.plats).flatMap { $0.win == id && abs($0.y - layout.pos.y) < 1 ? $0 : nil }
    }

    func startFlight() {
        lastFlight = uptime()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [unowned self] _ in self.flightStep() }
        RunLoop.main.add(t, forMode: .common); flightTimer = t
    }
    func stopFlight() { flightTimer?.invalidate(); flightTimer = nil; endWheee() }
    /// Let go with speed: he flies with the "wheee" face from that moment, facing the way he goes, until the first bump or the landing.
    func startThrow(_ v: Pt) {
        let bounds = layout.beginThrow(from: layout.pos)
        dragger.fly(Body(p: layout.pos, v: v), in: bounds, dizzy: dizzyAfter(speed: v.speed), plats: layout.plats)
        startFlight()
        flightVX = v.x; wheee = glue.bridge.play("w_wheee", loop: (0, 15))
    }
    func endWheee() { if wheee { wheee = false; glue.bridge.stopClip() } }

    func flightStep() {
        let now = uptime(), dt = min(now - lastFlight, 1.0 / 30)
        lastFlight = now; flightSeconds += dt
        guard let t = dragger.step(dt: dt) else { stopFlight(); return }
        layout.pos = t.body.p; flightVX = t.body.v.x
        positionWindows()
        if t.firstHit && t.dizzy { endWheee(); glue.bridge.act("dizzy") }   // the stars start on the first impact (a wall too), not after the bounces
        if t.landed {
            stopFlight()
            let top = supportUnder(cx: spriteCentre(layout.pos).x, feet: layout.pos.y, plats: dragger.plats).flatMap { abs($0.y - layout.pos.y) < 1 ? $0 : nil }
            layout.land(at: layout.pos, on: top, wins: wins)
            positionWindows()
            if falling { falling = false; glue.bridge.stopClip() }
            if !t.dizzy { glue.bridge.act("land") }
            probe.log("LAND", [("x", String(Int(layout.pos.x))), ("y", String(Int(layout.pos.y))), ("dizzy", String(t.dizzy)), ("on", top.map { String($0.win) } ?? "floor")], cap: 200)
            scenario?.write("land", ["on": top.map { $0.win as Any } ?? "floor", "pos": [layout.pos.x, layout.pos.y], "dizzy": t.dizzy])
        }
    }

    // MARK: window tops: following the window he stands on, trips and the way back
    /// One look at the window list (2 Hz; 10 Hz while he stands on a window): Claude's window, the open window tops, and whether his window still holds him.
    func look() {
        let primary = Double(NSScreen.screens.first?.frame.height ?? 0)
        let list = readWindows()
        let c = scenario.map { s in s.claude.flatMap { id in wins.first { $0.id == id }.map { (id: $0.id, r: $0.r) } } } ?? pickClaudeWin(list, pids: claudePIDs, primaryHeight: primary)
        if c?.r != layout.claude || c?.id != layout.claudeID {
            let before = layout.pos, old = layout.claude
            let clip = claudeWindowClip(old: old, new: c?.r, ridingIt: layout.host?.id == layout.claudeID && layout.host != nil)
            let back = clip == "w_restore" && claudeGone                     // first seen at launch is not a return
            if clip == "w_minimise" { claudeGone = true } else if c != nil { claudeGone = false }
            scenario?.write("claude", ["seen": c != nil, "clip": clip ?? "none", "rest": dragger.isRest, "route": route != nil, "falling": falling, "pending": pendingFall != nil])
            if dragger.isRest, route == nil, !falling, pendingFall == nil, let clip = clip, clip != "w_restore" || back,
               clip != "w_chase" || uptime() >= chaseAgain {
                if clip == "w_chase" { chaseAgain = uptime() + 10 }      // a drag is many small moves: one chase per 10 s
                glue.bridge.play(clip)
            }
            if dragger.isRest && route == nil && (layout.host == nil || layout.host?.r.w == 0) { layout.claudeChanged(to: c?.r, id: c?.id) }      // a saved spot on Claude's top waits for it
            else { layout.claude = c?.r; layout.claudeID = c?.id }
            positionWindows()
            probe.log("P10", [("claude", c.map { "\(Int($0.r.x)),\(Int($0.r.y)),\(Int($0.r.w))x\(Int($0.r.h))" } ?? "none"),
                              ("claudeDelta", old != nil && c != nil ? "\(Int(c!.r.x - old!.x)),\(Int(c!.r.y - old!.y))" : "n/a"),
                              ("petDelta", "\(Int(layout.pos.x - before.x)),\(Int(layout.pos.y - before.y))")], cap: 200)
        }
        checkSupport()
        if let s = scenario {
            let support = layout.host.map { h -> String in
                switch supportCheck(host: h.id, centre: spriteCentre(layout.pos).x, wins: wins) {
                case .stay: return "stay"
                case .gone: return "gone"
                case .slid: return "slid"
                case .covered(let by): return "covered:\(by)"
                }
            } ?? "none"
            traceLook(s, support: support)
        }
    }
    func traceLook(_ s: Scenario, support: String) {
        s.write("look", ["pos": [layout.pos.x, layout.pos.y], "host": layout.host.map { $0.id as Any } ?? NSNull(), "support": support,
                         "hostAt": layout.host.map { [$0.r.x, $0.r.y + $0.r.h] as Any } ?? NSNull(),
                         "book": place.pos.map { [$0.x, $0.y] as Any } ?? NSNull(), "bookShown": place.on && !bookHidden,
                         "mood": glue.bridge.state, "plats": layout.plats.count])
    }

    /// The window list now: the surfaces and their open tops. Returns the raw list for Claude's window.
    @discardableResult
    func readWindows() -> [WinInfo] {
        let primary = Double(NSScreen.screens.first?.frame.height ?? 0)
        let list = windowList()
        wins = surfaceWindows(list, ownPID: Int(getpid()), primaryHeight: primary)
        let full = fullScreenApp(wins, displays: NSScreen.screens.map { Brote.rc($0.frame) })
        layout.plats = full ? [] : exposedTops(wins, screens: layout.screens)
        return list
    }

    /// He rides his window when it moves, and falls, hops or flies when it goes away, slides off or is covered (the B rule).
    func checkSupport() {
        guard dragger.isRest, route == nil, pendingFall == nil, !pressedDown, let h = layout.host, h.r.w > 0 else { return }
        let now = uptime(), before = layout.pos
        if let w = wins.first(where: { $0.id == h.id }), w.r != h.r { layout.hostChanged(to: w.r); positionWindows() }
        if layout.pos != before {
            rideUntil = now + 0.4
            if !riding { riding = true; stroll.target = nil; glue.bridge.play("sf_ride", loop: Route.loop("sf_ride")) }
        } else if riding && now >= rideUntil { riding = false; glue.bridge.stopClip() }
        switch supportCheck(host: h.id, centre: spriteCentre(layout.pos).x, wins: wins) {
        case .stay: return
        case .gone: fall(clip: "sf_fall_close", cause: "gone")
        case .slid: fall(clip: "sf_fall_move", cause: "slid")
        case .covered(let by):
            guard let cover = wins.first(where: { $0.id == by }) else { return }
            let cx = spriteCentre(layout.pos).x
            let move = coverMove(state: glue.bridge.state, feet: layout.pos.y, coverTop: cover.r.maxY)
            let onto = layout.plats.filter { $0.win == by }.min { abs(($0.x0 + $0.x1) / 2 - cx) < abs(($1.x0 + $1.x1) / 2 - cx) }
            if move != .fall, let pl = onto {
                let to = Pt(clamp(layout.pos.x, pl.x0 - K.spriteW / 2 + 1, pl.x1 - K.spriteW / 2 - 1), pl.y)
                startRoute([move == .hop ? .hop(to: to) : .fly(to: to)], to: pl, arrive: nil, why: move == .hop ? "cover-hop" : "cover-fly")
            } else { fall(clip: "sf_fall_cover", cause: "covered") }
        }
    }

    /// A trip to another window top when he is happy and idle, and home to Claude's top when it works or after a while.
    func explore() {
        guard dragger.isRest, route == nil, pendingFall == nil, !pressedDown else { return }
        let now = scenario?.idleNow() ?? uptime()
        if explorer.awaySince == nil && now - explorer.lastTrip < K.tripEvery { return }      // the cheap check first, ten times a second
        let working = glue.bridge.working
        if explorer.shouldReturn(working: working, now: now) {
            explorer.awaySince = nil
            guard let cid = layout.claudeID, layout.host?.id != cid, let c = wins.first(where: { $0.id == cid }),
                  let pl = layout.plats.filter({ $0.win == cid }).max(by: { $0.x1 - $0.x0 < $1.x1 - $1.x0 }) else { return }
            go(to: pl, win: c.r, wantX: c.r.x + (store.number("offsetX") ?? (c.r.w - K.spriteW) / 2), arrive: nil, why: working ? "home-work" : "home")
        } else if explorer.mayLeave(state: glue.bridge.state, idle: !glue.busy, working: working, now: now),
                  let pl = pickTrip(layout.plats, host: layout.host?.id, claude: layout.claudeID), let w = wins.first(where: { $0.id == pl.win }) {
            explorer.lastTrip = now; explorer.awaySince = now
            go(to: pl, win: w.r, wantX: Double.random(in: pl.x0...max(pl.x0, pl.x1 - K.spriteW)), arrive: ["sf_sit", "sf_peek", "sf_edge"].randomElement(), why: "trip")
        }
    }

    func go(to pl: Plat, win: Rc, wantX: Double, arrive: String?, why: String) {
        let l = layout.ledge()
        startRoute(planRoute(from: layout.pos, lo: l.lo, hi: l.hi, to: pl, win: win, wantX: wantX), to: pl, arrive: arrive, why: why)
    }

    /// A route played at 60 fps: walking, climbing, a ladder, a hop or a flight, each with its clip.
    func startRoute(_ legs: [Leg], to target: Plat, arrive: String?, why: String) {
        guard !legs.isEmpty else { return }
        stopRoute()
        layout.leave(); riding = false; stroll.target = nil; stroll.walking = false
        route = Route(legs); routeTarget = target; routeArrive = arrive; routeClip = nil
        lastRouteStep = uptime()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [unowned self] _ in self.routeStep() }
        RunLoop.main.add(t, forMode: .common); routeTimer = t
        probe.log("ROUTE", [("why", why), ("to", String(target.win)), ("legs", legs.map { "\($0)" }.joined(separator: " "))], cap: 200)
        scenario?.write("route", ["why": why, "to": target.win, "legs": legs.map { "\($0)" }])
    }
    func stopRoute() {
        routeTimer?.invalidate(); routeTimer = nil
        guard route != nil else { return }
        route = nil; glue.walking = false
        if routeClip != nil { glue.bridge.stopClip(); routeClip = nil }
    }
    func routeStep() {
        guard var r = route else { stopRoute(); return }
        let now = uptime(), dt = min(now - lastRouteStep, 1.0 / 30)
        lastRouteStep = now
        if let tg = routeTarget, let moved = layout.plats.first(where: { $0.win == tg.win && $0.x0 < tg.x1 && $0.x1 > tg.x0 }), moved.y != tg.y {
            r.retarget(y: moved.y); routeTarget = moved                    // the window settled or was dragged: aim at its top now
        }
        var p = layout.pos
        routeMirror = r.mirrored(at: p)
        r.step(&p, dt: dt)
        route = r
        let clip = r.leg.flatMap(Route.clip)
        if clip != routeClip {
            if let c = clip { glue.bridge.play(c, loop: Route.loop(c)) } else if routeClip != nil { glue.bridge.stopClip() }
            routeClip = clip
        }
        if case .walk? = r.leg { glue.walking = true } else { glue.walking = false }
        layout.pos = p
        positionWindows()
        if r.done { finishRoute(at: p) }
    }
    func finishRoute(at p: Pt) {
        let arrive = routeArrive, target = routeTarget?.win
        let climbed = route?.legs.contains { if case .climb = $0 { return true }; if case .ladder = $0 { return true }; return false } ?? false
        stopRoute()
        readWindows()                                                       // the last look can be 0.1 s old
        guard let top = landingTop(cx: spriteCentre(p).x, feet: p.y, plats: layout.plats, target: target) else {
            fall(clip: nil, cause: "route-top-gone"); return                  // the window moved or closed on the way
        }
        let p = Pt(p.x, top.y)
        layout.land(at: p, on: top, wins: wins)
        scenario?.write("land", ["on": top.win, "pos": [p.x, p.y], "dizzy": false])
        if let a = arrive { glue.bridge.play(a) } else if climbed { glue.bridge.play("sf_hang") } else { glue.bridge.act("land") }      // up a side: he hangs on the edge and pulls himself up
    }

    func showMenu(_ event: NSEvent, in v: NSView) {
        let menu = NSMenu()
        let item = NSMenuItem(title: menuTitle(prefs: Locale.preferredLanguages, table: loadMenuTable(dir: resDir)), action: #selector(quit), keyEquivalent: "")
        item.target = self
        if let o = updateOffer(status), !updating, let line = updateWords()["brote.update_menu"] {
            let up = NSMenuItem(title: fillT(line, ["v": o.version]), action: #selector(menuUpdate), keyEquivalent: "")
            up.target = self
            menu.addItem(up)
        }
        menu.addItem(item)
        if let l = installedPlugin, uninstallArgv(layout: l) != nil, !removing {
            let rm = NSMenuItem(title: removeMenuWords(prefs: Locale.preferredLanguages).title, action: #selector(menuRemove), keyEquivalent: "")
            rm.target = self
            menu.addItem(rm)
        }
        NSMenu.popUpContextMenu(menu, with: event, for: v)
    }
    /// The one way out of the app (menu, signal, lifecycle): a note, the one cleanup, terminate. brote.lock is left for the OS to release.
    func finish(_ cause: ExitCause) { probe.plain(cause.note); cleanupOnExit(dirs: Brote.dirs); NSApp.terminate(nil) }
    @objc func quit() { finish(.menu) }

    /// "Remove Brote": after a yes, the plugin's own `floatctl.py uninstall` (no shell, a small environment). It stops this app itself; if it only reports success he leaves here.
    @objc func menuRemove() {
        guard let l = installedPlugin, let argv = uninstallArgv(layout: l), !removing else { return }
        guard trustedPluginRoot(l.root, configDir: Brote.configDir) != nil else { probe.log("REMOVE", [("answer", "refused: plugin no longer trusted")], cap: 5); return }
        let w = removeMenuWords(prefs: Locale.preferredLanguages)
        let a = NSAlert()
        a.messageText = w.ask; a.informativeText = w.body
        a.addButton(withTitle: w.yes); a.addButton(withTitle: w.no)
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { probe.log("REMOVE", [("answer", "no")], cap: 5); return }
        probe.log("REMOVE", [("answer", "yes")], cap: 5)
        removing = true
        let env = statusEnv(home: Brote.home, configDir: Brote.configEnv)
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let r = runChild(argv, env: env, timeout: 60)
            DispatchQueue.main.async {
                guard let me = self else { return }
                me.removing = false
                me.probe.log("REMOVE", [("exit", String(r.exit)), ("timedOut", String(r.timedOut)), ("ms", String(r.ms))], cap: 5)
                if r.exit == 0 && !r.timedOut { me.finish(.menu) }
            }
        }
    }

    // MARK: living with Claude: the sessions the plugin writes decide when he leaves (every 5 s; never in the first 120 s)
    func lifecycleTick() {
        guard var st = life else { return }
        let live = scanSessions(dir: Brote.dirs.sessions).live.count
        if live != lastLiveCount { lastLiveCount = live; probe.log("LIFE", [("live", String(live))], cap: 200) }
        let verdict = st.step(now: uptime(), live: live, pluginProbe: { pluginInstalled(configDir: Brote.configDir) })
        life = st
        if case .quit(let why) = verdict { finish(.lifecycle(why)) }
    }

    // MARK: what Claude is doing: the plugin's hooks append one short line per event to a file that only this app creates
    func startEvents() {
        guard eventsOn else { return }
        resetEventsFile(dirs: Brote.dirs)              // empty: no backlog; only after the instance lock is held
        eventsOffset = 0
        probe.plain("events: file reset")
        // killed from outside (pkill, a closed Terminal tab): the file goes too, or the hooks would keep appending to it. DispatchSource
        // handlers run on the main queue like any other code here, so nothing unsafe happens inside a raw signal handler.
        for sig in [SIGTERM, SIGHUP, SIGINT] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in self?.finish(.signal(sig)) }
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
        claudePIDs = Set(NSRunningApplication.runningApplications(withBundleIdentifier: K.claudeBundle).map { Int($0.processIdentifier) })
        layout.screens = NSScreen.screens.map { Brote.rc($0.visibleFrame) }
        if bookDrag.isRest, place.screensChanged(layout.screens) { positionBook() }         // a display went away: the book is back on screen
        defer { repositionLibreta() }
        look()
    }

    // MARK: the data line
    func loadSaved() {
        guard let s = store.string("lastStatus"), let d = s.data(using: .utf8), let st = parseStatus(d) else { return }
        apply(restored(st))
    }

    func apply(_ st: Dict) {
        let old = status
        status = st
        let n = { (k: String) -> Int in (st[k] as? NSNumber)?.intValue ?? 0 }
        glue.setData(state: (st["state"] as? String) ?? "contento", h5: n("h5"), stale: (st["stale"] as? [Any])?.count ?? 0)
        glue.bridge.setUpdate(updateOffer(st)?.version)
        let ch = feedChanges(old: old, new: st)
        glue.bridge.setFeed(clips: ch.clips, done: ch.done, quiet: feedQuiet(st), runsOut: feedRunsOut(st))
        watch.reset(((st["watch"] as? Dict)?["files"] as? [Any] ?? []).compactMap { $0 as? String }, home: Brote.home)
        refreshBook()
        if libretaVisible { fillLibreta(force: false) }
    }

    func refreshStatus() {
        guard dataOn, let l = plug, !statusBusy else { return }
        guard l.isRoot ? trustedPluginRoot(l.root, configDir: Brote.configDir) != nil : hubTrusted(l.root) else { probe.log("P9", [("exit", "plugin no longer trusted")], cap: 5); return }
        let sc = scanSessions(dir: Brote.dirs.sessions, reap: false)
        let project = chooseProject(live: sc.live, mtimes: sc.mtimes, launch: projectArg)
        if project != lastProject { lastProject = project; probe.log("PROJECT", [("now", project == nil ? "none" : "set")], cap: 60) }
        guard let project = project else { return }                        // no project: nothing to ask, the data line stays off
        statusBusy = true; pace.lastRun = uptime()
        let argv = statusArgv(layout: l, project: project), env = statusEnv(home: Brote.home, configDir: Brote.configEnv)
        let cache = l.pycache
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
                                      ("pycacheBefore", String(before)), ("pycacheAfter", String(after)), ("live", "1"), ("bytes", String(r.out.count))], cap: 60)
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
        probe.log("P8", [("xattr", names), ("bundle", "app")])
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
        if dataOn, let l = plug, !l.isRoot, !docsProbed {
            let h = URL(fileURLWithPath: l.root)
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

var instanceLockFD: Int32 = -1       // held for the whole life of the process; the OS releases it when we exit

func launchApp() -> Never {
    // the very first thing, before the probe log, events.log, the window server or any window: one instance per HOME
    if !CommandLine.arguments.contains("--scenario") {
        let got = takeInstanceLock(dirs: Brote.dirs)
        switch got.outcome {
        case .held: instanceLockFD = got.fd
        case .busy: exit(0)
        case .refused(let why): fputs("brote: not started: " + why + "\n", stderr); exit(0)
        case .failed(let why): fputs("brote: not started: " + why + "\n", stderr); exit(1)
        }
    }
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
#if !CORE_ONLY
if cli.contains("--scenario") {                               // the scenario runner's mode: see tools/brote/float/scenario/scenario.py
    if cli.contains("--check") { print("scenario: ok"); exit(0) }
    if let j = cli.firstIndex(of: "--assets"), j + 1 < cli.count { resDir = cli[j + 1] }
}
if let i = cli.firstIndex(of: "--libreta-png") {            // GUI-free: the round-3 page as PNGs, for review
    func opt(_ k: String) -> String? { cli.firstIndex(of: k).flatMap { $0 + 1 < cli.count ? cli[$0 + 1] : nil } }
    guard i + 2 < cli.count else { fputs("usage: --libreta-png <feed.json> <existing folder> [--now <epoch>] [--tz <id>] [--lang-feed]\n", stderr); exit(2) }
    runLibretaPng(feed: cli[i + 1], out: cli[i + 2], now: opt("--now").flatMap(Double.init).map { Date(timeIntervalSince1970: $0) }, tz: opt("--tz").flatMap { TimeZone(identifier: $0) })
}
if let i = cli.firstIndex(of: "--record"), i + 1 < cli.count {
    var assets = Bundle.main.resourcePath ?? "."
    if let j = cli.firstIndex(of: "--assets"), j + 1 < cli.count { assets = cli[j + 1] }
    runRecord(assets: assets, out: cli[i + 1])
}
#endif
#if CORE_ONLY
fputs("brote core build: only --selftest and --bench are available\n", stderr)
exit(2)
#else
launchApp()
#endif
