#!/usr/bin/python3
"""budgit brote (also: budgit float): the floating Brote window, shipped prebuilt inside the plugin (R-292 plan B).

    floatctl.py on                         install the prebuilt Brote.app into ~/Library/Application Support/BroteWindow and turn it on
    floatctl.py off | stop | status        stop opening it with Claude (remembered, never undone by an update) | quit the running app | one-screen state
    floatctl.py uninstall                  remove every file this feature ever wrote
    floatctl.py ensure | session-end       SessionStart / SessionEnd hooks: always exit 0; the first ensure after an install turns it on by itself
                                           (macOS, a screen, not CI or SSH; BUDGIT_FLOAT_AUTO=off stops that) and prints one systemMessage line, once

Trust: only a plugin folder Claude Code itself registered (installed_plugins.json: the cache copy, or a folder it was pointed at; never a
marketplaces/ copy; owned by the user, not group/other-writable) is ever copied, and only when every file of it matches MANIFEST.sha256
and carries no quarantine mark. The copy is launched by its own binary (never `open`) with an empty environment. macOS only; nothing here asks the system for a permission.
Exit codes: 0 done, 4 refused (the reason is printed). Stdlib only, Python 3.9.
"""
import fcntl
import json
import os
import stat
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))


_STDIN = [None]            # the hook's stdin, read once, whichever path needs it first


def _quick_prompt_check(argv, environ):
    """UserPromptSubmit runs on every message, so the common answers come before the heavy imports (about 25 ms): True when there is nothing to do.
    Off or damaged state, or he is up and this very chat is registered. Anything else (not decided yet, not running, chat unknown) takes the full path."""
    try:
        home = environ.get("HOME", "")
        if sys.platform != "darwin" or not os.path.isabs(home):
            return False
        bw = os.path.join(home, "Library", "Application Support", "BroteWindow")
        st = os.lstat(bw)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return False
        f = os.path.join(bw, "float.json")
        fst = os.lstat(f)                                       # missing: not decided yet, the full path turns him on
        if not stat.S_ISREG(fst.st_mode) or fst.st_uid != os.getuid() or fst.st_size > 4096:
            return False
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        try:
            state = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            state = None
        if not isinstance(state, dict) or state.get("float") != "on":
            return True                                         # off, or a file nobody can read: it never turns him on
        _STDIN[0] = sys.stdin.read(65536)
        sid = json.loads(_STDIN[0] or "{}").get("session_id")
        if not isinstance(sid, str) or not sid:
            return False
        lock = os.open(os.path.join(bw, "brote.lock"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return False                                    # nobody holds the lock: he is not running
            except BlockingIOError:
                pass
        finally:
            os.close(lock)
        sdir = os.path.join(bw, "sessions")
        for name in sorted(os.listdir(sdir))[:200]:
            if not name.endswith(".json"):
                continue
            path = os.path.join(sdir, name)
            pst = os.lstat(path)
            if not stat.S_ISREG(pst.st_mode) or pst.st_size > 4096:
                continue
            pfd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                entry = json.loads(os.read(pfd, 4096).decode("utf-8", "replace"))
            finally:
                os.close(pfd)
            if isinstance(entry, dict) and entry.get("sid") == sid[:12] and isinstance(entry.get("pid"), int):
                try:
                    os.kill(entry["pid"], 0)
                except PermissionError:
                    pass
                except OSError:
                    continue
                return True
        return False
    except (OSError, ValueError, AttributeError):
        return False


if __name__ == "__main__" and "--prompt" in sys.argv[2:] and _quick_prompt_check(sys.argv, os.environ):
    sys.exit(0)

import ctypes  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, HERE)
import floatmanifest as fm  # noqa: E402

BUNDLE_ID = "es.budgit.brote.float"
PLUGIN_NAMES = ("budgit", "studio")           # the public and the internal name of this plugin
SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PID_RE = re.compile(r"^[0-9]{2,10}$")
SKIP_NAMES = {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "env"}
LOG_MAX = 64 * 1024

MSG = {
    "en": {
        "mac_only": "Brote's floating window is macOS only.",
        "auto_on": "Brote's floating window is on; /budgit-brote off turns it off.",
        "refused": "Not done: {why}",
        "on_done": "Brote's floating window is on. It opens with your next Claude session (and now).",
        "off_done": "Brote's floating window is off. It is closed and will not open with Claude any more.",
        "off_already": "Brote's floating window was already off.",
        "stopped": "Brote's window was closed.",
        "not_running": "Brote's window was not running.",
        "status": "Floating window: {state}\nBuild: {build}\nApp: {app}",
        "running": "running", "stopped_state": "not running", "none": "not installed",
        "uninstalled": "Brote's floating window is removed: its app, settings, logs and session files are gone. It will not come back by itself; /budgit-brote on brings it back.",
        "not_installed_plugin": "this copy of the plugin is not the version Claude Code installed ({detail}); update or reinstall the plugin",
        "quarantine": "com.apple.quarantine is set on the bundle, so it came from outside Claude Code's own install; reinstall the plugin",
        "bw_bad": "the folder {bw} is not a plain folder of yours ({detail}); nothing was written there",
        "attr_check": "the attribute check of the bundle could not run, so it is treated as quarantined; nothing was installed",
    },
    "es": {
        "mac_only": "La ventana flotante de Brote es solo para macOS.",
        "auto_on": "Brote flotante activado; /budgit-brote off para apagarlo.",
        "refused": "No se hizo: {why}",
        "on_done": "La ventana flotante de Brote está activada. Se abre con tu próxima sesión de Claude (y ahora).",
        "off_done": "La ventana flotante de Brote está desactivada. Se ha cerrado y ya no se abre con Claude.",
        "off_already": "La ventana flotante de Brote ya estaba desactivada.",
        "stopped": "Se cerró la ventana de Brote.",
        "not_running": "La ventana de Brote no estaba abierta.",
        "status": "Ventana flotante: {state}\nVersión: {build}\nApp: {app}",
        "running": "abierta (running)", "stopped_state": "cerrada", "none": "no instalada",
        "uninstalled": "La ventana flotante de Brote está quitada: su app, ajustes, registros y ficheros de sesión ya no existen. No volverá sola; /budgit-brote on la trae de vuelta.",
        "not_installed_plugin": "esta copia del plugin no es la versión que Claude Code instaló ({detail}); actualiza o reinstala el plugin",
        "quarantine": "com.apple.quarantine está puesto en el paquete, así que no vino de la instalación propia de Claude Code; reinstala el plugin",
        "bw_bad": "la carpeta {bw} no es una carpeta normal tuya ({detail}); no se escribió nada ahí",
        "attr_check": "no se pudo comprobar los atributos del paquete, así que se trata como en cuarentena; no se instaló nada",
    },
}


def lang():
    try:
        import i18n
        return i18n.detect() if i18n.detect() in MSG else "en"
    except Exception:
        return "en"


def say(key, **kw):
    return MSG[lang()][key].format(**kw)


class Refuse(Exception):
    pass


# ---- the machine this runs on ------------------------------------------------------------------------------------------------

class Ctx:
    def __init__(self):
        home = os.environ.get("HOME", "")
        if not os.path.isabs(home) or not os.path.isdir(home):
            raise Refuse("HOME is not a folder")
        self.home = os.path.realpath(home)
        cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(self.home, ".claude")
        self.cfg = os.path.realpath(cfg)
        self.root = os.path.dirname(HERE)                       # <plugin root>/scripts/floatctl.py
        self.flt = os.path.join(self.root, "float")
        self.lib = os.path.join(self.home, "Library")
        self.bw = os.path.join(self.lib, "Application Support", "BroteWindow")
        self.installed_lines = os.path.join(self.bw, "Brote.app.sha256")
        self.exe = os.path.join(self.bw, "Brote.app", "Contents", "MacOS", "Brote")
        self.real_home = False
        try:
            import pwd
            self.real_home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir) == self.home
        except Exception:
            pass


def p(ctx, name):
    return os.path.join(ctx.bw, name)


def is_mac():
    return sys.platform == "darwin"


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None


def mine_dir(path):
    st = lstat(path)
    return st is not None and stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()


def read_json_file(path):
    text = read_small(path)
    if text is None:
        return None
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def read_small(path, limit=64 * 1024):
    """Text of a small plain file of ours, or None. Never opens a pipe or follows a link, never reads more than limit bytes."""
    st = lstat(path)
    if st is None or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > limit:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        return os.read(fd, limit + 1).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def write_private(path, text):
    """Atomic 0600 write that never follows a symlink at the target."""
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def log(ctx, text):
    """ensure.log: why a hook did nothing. 0600, no file paths, bounded size."""
    try:
        if not mine_dir(ctx.bw):
            return
        path = p(ctx, "ensure.log")
        st = lstat(path)
        if st is not None and not stat.S_ISREG(st.st_mode):
            return                                                  # a pipe or a link in its place: say nothing rather than block
        if st is not None and st.st_size > LOG_MAX:
            with open(path, "rb") as f:
                f.seek(-LOG_MAX // 2, 2)
                keep = f.read().split(b"\n", 1)[-1]
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC)
            os.write(fd, keep)
            os.close(fd)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for folder, mark in ((ctx.cfg, "$CLAUDE_CONFIG_DIR"), (ctx.home, "~")):       # the longer, more specific path first
                text = text.replace(folder, mark)
            os.write(fd, f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}\n".encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except OSError:
        pass


# ---- trust ---------------------------------------------------------------------------------------------------------------------

def _sound_dir(path):
    st = lstat(path)
    return st is not None and stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022


def directory_marketplace_root(ctx, key):
    """True when ctx.root is the live plugin folder of a "directory" marketplace the plugin `key` (name@market) is installed from:
    known_marketplaces.json says market -> source directory <path> with installLocation == that real folder, and that folder's own
    .claude-plugin/marketplace.json (same market name) lists the plugin with a relative source that resolves exactly to ctx.root."""
    if "@" not in key:
        return False
    name, market = key.split("@", 1)
    known = read_json_file(os.path.join(ctx.cfg, "plugins", "known_marketplaces.json")) or {}
    m = known.get(market)
    if not isinstance(m, dict) or not isinstance(m.get("source"), dict) or m["source"].get("source") != "directory":
        return False
    if not isinstance(m.get("installLocation"), str) or not isinstance(m["source"].get("path"), str):
        return False
    loc = os.path.realpath(m["installLocation"])
    if not os.path.isabs(m["source"]["path"]) or os.path.realpath(m["source"]["path"]) != loc or not os.path.isabs(m["installLocation"]) or not _sound_dir(loc):
        return False
    mj = read_json_file(os.path.join(loc, ".claude-plugin", "marketplace.json")) or {}
    if mj.get("name") != market or not isinstance(mj.get("plugins"), list):
        return False
    for e in mj["plugins"]:
        src = e.get("source") if isinstance(e, dict) else None
        if not (isinstance(e, dict) and e.get("name") == name and isinstance(src, str) and src.startswith("./") and ".." not in src.split("/")):
            continue
        cand = os.path.realpath(os.path.join(loc, src))
        if cand == ctx.root and cand.startswith(loc + os.sep):
            return True
    return False


def installed_version(ctx):
    """The version Claude Code registered for THIS folder, or raise Refuse. Two ways to be registered: the registry lists this folder as an
    installPath (the cache, or a folder Claude Code itself registered), or the plugin is installed from a "directory" marketplace whose
    live folder this is (the registry then keeps a stale cache path; Claude reads the live folder). Never a marketplaces/ copy."""
    reg = read_json_file(os.path.join(ctx.cfg, "plugins", "installed_plugins.json"))
    plugins = (reg or {}).get("plugins")
    if not isinstance(plugins, dict):
        raise Refuse(say("not_installed_plugin", detail="no installed_plugins.json"))
    for key, entries in plugins.items():
        if not isinstance(key, str) or key.split("@")[0] not in PLUGIN_NAMES or not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            by_path = isinstance(e.get("installPath"), str) and os.path.realpath(e["installPath"]) == ctx.root
            if not by_path and not directory_marketplace_root(ctx, key):
                continue
            if ctx.root.startswith(os.path.join(ctx.cfg, "plugins", "marketplaces") + os.sep):
                raise Refuse(say("not_installed_plugin", detail="a marketplace copy is not an install"))
            if not by_path:
                sp = os.path.join(ctx.cfg, "settings.json")
                settings = read_json_file(sp)
                if settings is None and lstat(sp) is not None:
                    raise Refuse(say("not_installed_plugin", detail="settings.json cannot be read"))      # present but unreadable: do not guess it is switched on
                enabled = (settings or {}).get("enabledPlugins") or {}
                if isinstance(enabled, dict) and enabled.get(key) is False:
                    raise Refuse(say("not_installed_plugin", detail="the plugin is switched off in settings"))
            for d in (ctx.root, ctx.flt):
                if not _sound_dir(d):
                    raise Refuse(say("not_installed_plugin", detail="folder not owned by you or writable by others"))
            if not by_path:                                                  # the registry's version is the stale cache's: the live folder says its own
                live = read_json_file(os.path.join(ctx.root, ".claude-plugin", "plugin.json")) or {}
                v = live.get("version")
                if isinstance(v, str) and re.fullmatch(r"\d{1,5}(\.\d{1,5}){0,3}", v):
                    return v
            return str(e.get("version") or "")
    raise Refuse(say("not_installed_plugin", detail="not the registered install path"))


def gui_session():
    """True when this user is the one at the screen: /dev/console belongs to the user logged in on the display."""
    try:
        return os.stat("/dev/console").st_uid == os.getuid()
    except OSError:
        return False


def auto_allowed(env=None):
    """The automatic first start happens only where there is a screen and somebody to see it: not in CI, not over SSH, not switched off."""
    env = os.environ if env is None else env
    if env.get("CI") or env.get("SSH_CONNECTION") or env.get("SSH_TTY") or env.get("BUDGIT_FLOAT_AUTO", "").lower() in ("off", "0", "no"):
        return False
    return gui_session()


def quarantined(path):
    """True when the quarantine attribute is on anything under path. A check that cannot run is an error, never a pass."""
    try:
        r = subprocess.run(["/usr/bin/xattr", "-rl", path], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise Refuse(say("attr_check"))
    if r.returncode != 0:
        raise Refuse(say("attr_check"))
    return "com.apple.quarantine" in r.stdout


def plugin_bundle_problems(ctx):
    if quarantined(os.path.join(ctx.flt, "Brote.app")):
        return [say("quarantine")]
    return fm.check(ctx.flt)


# ---- the support folder ----------------------------------------------------------------------------------------------------------

def bw_state(ctx):
    """None when the folder does not exist; otherwise float.json's dict ({} when unreadable). Raises Refuse for a bad folder."""
    st = lstat(ctx.bw)
    if st is None:
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise Refuse(say("bw_bad", bw=ctx.bw, detail="a link or not yours"))
    if st.st_mode & 0o077:
        os.chmod(ctx.bw, 0o700)
    return read_json_file(p(ctx, "float.json")) or {}


def float_choice(ctx):
    """"on", "off" or "unset" (nobody has decided yet). A state file that is there but unreadable counts as off: it never turns him on again."""
    st = bw_state(ctx)
    if st is None or lstat(p(ctx, "float.json")) is None:
        return "unset"
    return "on" if st.get("float") == "on" else "off"


def bw_create(ctx):
    for d in (ctx.lib, os.path.dirname(ctx.bw)):
        st = lstat(d)
        if st is None:
            os.mkdir(d, 0o755)
        elif not stat.S_ISDIR(st.st_mode):
            raise Refuse(say("bw_bad", bw=ctx.bw, detail=os.path.basename(d) + " is a link"))
    if bw_state(ctx) is None:
        os.mkdir(ctx.bw, 0o700)


def lock(ctx, secs=8.0):
    fd = os.open(p(ctx, "ensure.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    end = time.time() + secs
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.time() > end:
                os.close(fd)
                raise Refuse("another Brote command is running")
            time.sleep(0.05)


def rm_tree(path):
    """Remove a file, link or folder of ours without following links."""
    st = lstat(path)
    if st is None:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path, ignore_errors=True)
    else:
        os.unlink(path)


# ---- the running app -------------------------------------------------------------------------------------------------------------

def lock_held(ctx):
    """True while a process holds brote.lock: that is what "the app is running" means. The probe takes a shared lock for an instant and lets go."""
    st = lstat(p(ctx, "brote.lock"))
    if st is None or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        return False
    try:
        fd = os.open(p(ctx, "brote.lock"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def running_pid(ctx):
    """pid of the running app: the pid in brote.lock while the lock is held, and only when that process is ours and runs the installed binary."""
    if not lock_held(ctx):
        return None
    try:
        pid = int((read_small(p(ctx, "brote.lock"), 32) or "").strip())
    except ValueError:
        return None
    if pid <= 1:
        return None
    try:
        r = subprocess.run(["/bin/ps", "-o", "uid=,args=", "-p", str(pid)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = r.stdout.strip().split(None, 1)
    if len(parts) != 2 or parts[0] != str(os.getuid()):
        return None
    if ctx.exe not in parts[1]:
        return None
    return pid


def holds_lock(ctx, pid):
    """True when this pid really has brote.lock open (what float-uninstall.sh checks with lsof): a pid written in the file is not proof by itself."""
    try:
        r = subprocess.run(["/usr/sbin/lsof", "-t", "--", p(ctx, "brote.lock")], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid) in r.stdout.split()


def stop_app(ctx):
    pid = running_pid(ctx)
    if pid is None or not holds_lock(ctx, pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            time.sleep(0.05)
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    return True


def close_old_instances(ctx):
    """Close a Brote that an older build or a hand start left running from this very folder: our user, our binary, no --plugin-root (the plugin
    always passes it) and not the process that holds brote.lock. Only those pids; if lsof cannot say who holds the lock, nothing is closed."""
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    try:
        ps = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,args="], capture_output=True, text=True, env=env, timeout=4)
        held = subprocess.run(["/usr/sbin/lsof", "-t", "--", p(ctx, "brote.lock")], capture_output=True, text=True, env=env, timeout=4)   # after ps: an app that takes the lock in between is seen as its holder
    except (OSError, subprocess.SubprocessError):
        return []
    if held.returncode not in (0, 1) or ps.returncode != 0:
        return []
    holders = set(held.stdout.split())
    old = []
    for line in ps.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or parts[1] != str(os.getuid()) or parts[0] in holders or not parts[0].isdigit():
            continue
        args = parts[2]
        if (args == ctx.exe or args.startswith(ctx.exe + " ")) and "--plugin-root" not in args and int(parts[0]) != os.getpid():
            old.append(int(parts[0]))
    for pid in old:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):
                time.sleep(0.05)
                os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return old


def launch(ctx, root):
    env = {"PATH": "/usr/bin:/bin", "HOME": ctx.home, "CLAUDE_CONFIG_DIR": ctx.cfg}
    null = os.open(os.devnull, os.O_RDWR)
    try:
        subprocess.Popen([ctx.exe, "--plugin-root", root], env=env, stdin=null, stdout=null, stderr=null, close_fds=True,
                         start_new_session=True, cwd="/")
    finally:
        os.close(null)


# ---- install / swap ----------------------------------------------------------------------------------------------------------------

def _renamex_swap(a, b):
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.renamex_np(a.encode(), b.encode(), 2) == 0        # RENAME_SWAP
    except (OSError, AttributeError):
        return False


def installed_problems(ctx):
    if not lstat(ctx.exe):
        return ["MISSING Brote.app"]
    st = lstat(ctx.installed_lines)
    if st is None or not stat.S_ISREG(st.st_mode):
        return ["MISSING Brote.app.sha256"]
    text = read_small(ctx.installed_lines)
    if text is None:
        return ["MISSING Brote.app.sha256"]
    return fm.check_app(ctx.bw, text)


def repair(ctx):
    """Finish what a crash in the middle of a swap left: a missing app comes back from .prev, a pending sidecar is promoted when it fits."""
    app, prev = p(ctx, "Brote.app"), p(ctx, "Brote.app.prev")
    if lstat(app) is None and mine_dir(prev):
        os.rename(prev, app)
    new = ctx.installed_lines + ".new"
    if lstat(new) is not None:
        text = read_small(new)
        if text is not None and not fm.check_app(ctx.bw, text):
            os.replace(new, ctx.installed_lines)
        else:
            rm_tree(new)                                          # also a pipe or a link somebody put there
    for name in os.listdir(ctx.bw):
        if name.startswith(".stage-") and mine_dir(p(ctx, name)):
            shutil.rmtree(p(ctx, name), ignore_errors=True)


def install(ctx, text, version=""):
    """Copy the verified plugin bundle next to the installed one and swap them; the old one stays as Brote.app.prev."""
    stage = tempfile.mkdtemp(prefix=".stage-", dir=ctx.bw)
    try:
        shutil.copytree(os.path.join(ctx.flt, "Brote.app"), os.path.join(stage, "Brote.app"), symlinks=True)
        bad = fm.check_app(stage, text)
        if bad:
            raise Refuse("the copy does not match the manifest: " + "; ".join(bad[:3]))
        if quarantined(os.path.join(stage, "Brote.app")):
            raise Refuse(say("quarantine"))
        write_private(ctx.installed_lines + ".new", text)
        app, prev = p(ctx, "Brote.app"), p(ctx, "Brote.app.prev")
        staged = os.path.join(stage, "Brote.app")
        if lstat(app) is None:
            os.rename(staged, app)
        else:
            if _renamex_swap(staged, app):
                rm_tree(prev)
                os.rename(staged, prev)
            else:
                rm_tree(prev)
                os.rename(app, prev)
                os.rename(staged, app)
        os.replace(ctx.installed_lines + ".new", ctx.installed_lines)
        state = read_json_file(p(ctx, "float.json"))
        if state is not None and version:                     # what the downgrade guard compares with is the build installed last
            state["version"] = version
            write_private(p(ctx, "float.json"), json.dumps(state))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def bring_up(ctx, version, wait=8.0):
    """Make the installed copy current and start it when it is not running. Returns the list of reasons it did not (empty = fine).
    The plugin's own bundle is hashed and checked for quarantine only when something is about to be installed from it."""
    fd = lock(ctx, wait)
    try:
        if float_choice(ctx) == "off":                           # an `off` that landed after the caller decided wins
            return []
        repair(ctx)
        close_old_instances(ctx)
        try:
            want = fm.app_lines(ctx.flt)
        except (OSError, ValueError):
            return ["MISSING MANIFEST.sha256"]
        have = read_small(ctx.installed_lines) if lstat(ctx.exe) is not None else None
        if have != want:
            bad = plugin_bundle_problems(ctx)
            if bad:
                return bad
            state = read_json_file(p(ctx, "float.json")) or {}
            if running_pid(ctx) is not None:
                pass                                              # a newer build waits for the next quiet start
            elif have is not None and version_tuple(state.get("version")) > version_tuple(version):
                return ["the plugin build is older than the installed one; not replaced"]
            else:
                install(ctx, want, version)
        bad = installed_problems(ctx)
        if bad:
            return bad
        if running_pid(ctx) is None:
            launch(ctx, ctx.root)
        return []
    finally:
        os.close(fd)


# ---- sessions ------------------------------------------------------------------------------------------------------------------------

_libproc = None


def bsdinfo(pid):
    """{ppid, uid, name, start} of a process via libproc, or None."""
    global _libproc
    try:
        if _libproc is None:
            _libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buf = ctypes.create_string_buffer(136)
        if _libproc.proc_pidinfo(int(pid), 3, 0, buf, 136) != 136:       # PROC_PIDTBSDINFO
            return None
        raw = buf.raw
        return {"ppid": struct.unpack_from("<I", raw, 16)[0], "uid": struct.unpack_from("<I", raw, 20)[0],
                "name": raw[48:64].split(b"\0")[0].decode("utf-8", "replace"), "start": struct.unpack_from("<Q", raw, 120)[0]}
    except (OSError, AttributeError, struct.error):
        return None


def pick_session_pid(chain):
    """chain = [(process name, pid)] nearest ancestor first; the first one that is not a shell or an interpreter is Claude."""
    for name, pid in chain:
        low = name.lower()
        if low in SKIP_NAMES or re.match(r"^python[0-9.]*$", low):
            continue
        return pid
    return None


def find_claude_pid():
    chain, pid = [], os.getppid()
    for _ in range(12):
        info = bsdinfo(pid)
        if not info or pid <= 1:
            break
        chain.append((info["name"], pid))
        pid = info["ppid"]
    return pick_session_pid(chain)


def session_input():
    try:
        if _STDIN[0] is not None:
            d = json.loads(_STDIN[0] or "{}")
        elif sys.stdin.isatty():
            return {}
        else:
            d = json.loads(sys.stdin.read(65536) or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def claude_pid(args):
    if "--claude-pid" in args:
        v = args[args.index("--claude-pid") + 1] if args.index("--claude-pid") + 1 < len(args) else ""
        return int(v) if PID_RE.match(v) and int(v) > 1 else None
    return find_claude_pid()


LEDGER_DIRS = (".budgit", ".studio")      # the project folder's ledger lives in one of these (the old name still counts)


def find_project(cwd, home, levels=8):
    """The Budgit project a working folder belongs to: itself or up to 8 folders above, never the home folder or `/` (the rule of
    `budgit status`). None when there is none. Read-only."""
    try:
        here = os.path.realpath(cwd)
    except (OSError, ValueError, TypeError):
        return None
    for _ in range(levels + 1):
        parent = os.path.dirname(here)
        if parent == here or here == home:
            return None
        if any(os.path.isfile(os.path.join(here, d, "ledger.json")) for d in LEDGER_DIRS):
            return here
        here = parent
    return None


def register_session(ctx, pid, sid, cwd=None):
    info = bsdinfo(pid) if pid else None
    if not info or info["uid"] != os.getuid() or not isinstance(sid, str) or not SID_RE.match(sid):
        return
    d = p(ctx, "sessions")
    st = lstat(d)
    if st is None:
        os.mkdir(d, 0o700)
    elif not mine_dir(d):
        return
    else:
        os.chmod(d, 0o700)
    entry = {"pid": pid, "start": info["start"], "sid": sid[:12]}
    project = find_project(cwd, ctx.home) if isinstance(cwd, str) and os.path.isabs(cwd) else None
    if project:
        entry["project"] = project                # the app's data line follows the newest live session's project
    write_private(os.path.join(d, f"{pid}.json"), json.dumps(entry))
    prune_sessions(ctx)


def live_claude_pids(ps_text, uid):
    """pids of this user's Claude Code session processes, from `ps -axo pid=,uid=,comm=`: the program file is named claude."""
    out = []
    for line in ps_text.splitlines():
        m = re.match(r"^\s*([0-9]+)\s+([0-9]+)\s+(.+)$", line)
        if m and int(m.group(2)) == uid and os.path.basename(m.group(3).strip()) == "claude" and int(m.group(1)) > 1:
            out.append(int(m.group(1)))
    return out[:30]


def register_open_chats(ctx, ps_text=None):
    """`on` can run outside any session (the desktop app's /budgit-brote): the chats already open count as sessions, or he would leave
    after two minutes. A chat the SessionStart hook already registered keeps its own file."""
    if ps_text is None:
        try:
            ps_text = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,comm="], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                     timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return
    for pid in live_claude_pids(ps_text, os.getuid()):
        info = bsdinfo(pid)
        have = read_json_file(os.path.join(ctx.bw, "sessions", f"{pid}.json"))
        if info and have and have.get("start") == info["start"]:
            continue
        register_session(ctx, pid, f"on-{pid}")


def prune_sessions(ctx):
    d = p(ctx, "sessions")
    for name in os.listdir(d):
        m = re.match(r"^([0-9]+)\.json$", name)
        if not m:
            continue
        data = read_json_file(os.path.join(d, name))
        info = bsdinfo(int(m.group(1)))
        if data is None or not info or data.get("start") != info["start"]:
            try:
                os.unlink(os.path.join(d, name))
            except OSError:
                pass


# ---- commands ------------------------------------------------------------------------------------------------------------------------

def cmd_on(ctx, args):
    if not is_mac():
        print(say("mac_only"))
        return 4
    try:
        version = installed_version(ctx)
        bad = plugin_bundle_problems(ctx)
        if bad:
            print(say("refused", why="; ".join(bad)))
            return 4
        bw_create(ctx)
        before = read_json_file(p(ctx, "float.json"))
        write_private(p(ctx, "float.json"), json.dumps({"float": "on", "version": (before or {}).get("version") or version, "turned_on": int(time.time())}))
        try:
            if os.environ.get("CLAUDE_CODE_SESSION_ID"):            # run from inside a session: that session counts, or he would leave after 2 minutes
                register_session(ctx, claude_pid(args), os.environ["CLAUDE_CODE_SESSION_ID"], os.getcwd())
            register_open_chats(ctx)                                 # and so do the chats already open (the desktop app runs this outside any session)
        except OSError:
            pass
        bad = bring_up(ctx, version)
        if bad:
            if before is None:
                os.unlink(p(ctx, "float.json"))
            else:
                write_private(p(ctx, "float.json"), json.dumps(before))
            print(say("refused", why="; ".join(bad)))
            return 4
        kept = (before or {}).get("version")
        newest = kept if kept and version_tuple(kept) > version_tuple(version) else version       # the downgrade guard never moves back
        write_private(p(ctx, "float.json"), json.dumps({"float": "on", "version": newest, "turned_on": int(time.time())}))
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    print(say("on_done"))
    return 0


def cmd_off(ctx, args):
    try:
        state = bw_state(ctx)
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    if state is not None and lstat(p(ctx, "float.json")) is not None and state.get("float") != "on":
        stop_app(ctx)
        print(say("off_already"))
        return 0
    try:
        bw_create(ctx)                                            # off is remembered even before the first start, or he would come on by himself
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    try:
        fd = lock(ctx)                                            # a start or an install that is deciding right now finishes first, so off is never overwritten
    except (Refuse, OSError) as e:
        print(say("refused", why=str(e)))
        return 4
    try:
        state = dict(read_json_file(p(ctx, "float.json")) or {})
        state["float"] = "off"
        write_private(p(ctx, "float.json"), json.dumps(state))
    except OSError as e:
        print(say("refused", why=e.__class__.__name__))
        return 4
    finally:
        os.close(fd)
    stop_app(ctx)                                                 # off means gone now, not "when the last chat ends"
    print(say("off_done"))
    return 0


def cmd_stop(ctx, args):
    try:
        bw_state(ctx)
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    print(say("stopped") if stop_app(ctx) else say("not_running"))
    return 0


def cmd_status(ctx, args):
    if not is_mac():
        print(say("mac_only"))
        return 4
    try:
        state = bw_state(ctx)
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    on = bool(state) and state.get("float") == "on"
    build = "-"
    try:
        build = (read_small(os.path.join(ctx.bw, "Brote.app", "Contents", "Resources", "build.txt"), 64) or "").strip() or "-"
    except OSError:
        pass
    app = say("running") if running_pid(ctx) else (say("stopped_state") if lstat(ctx.exe) else say("none"))
    print(say("status", state="on" if on else "off", build=build, app=app))
    return 0


def cmd_uninstall(ctx, args):
    if not is_mac():
        print(say("mac_only"))
        return 4
    try:
        bw_state(ctx)
    except Refuse as e:
        print(say("refused", why=str(e)))
        return 4
    stop_app(ctx)
    if ctx.real_home:                       # the system's own settings and Launch Services only when this really is the user's home
        for cmd in (["/usr/bin/defaults", "delete", BUNDLE_ID],
                    ["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-u", p(ctx, "Brote.app")]):
            try:
                if os.path.exists(cmd[0]):
                    subprocess.run(cmd, capture_output=True, env={"PATH": "/usr/bin:/bin"}, timeout=20)
            except (OSError, subprocess.SubprocessError):
                pass
    if lstat(ctx.bw) is None:
        try:
            bw_create(ctx)                                        # nothing was ever written, but he must not start by himself after this
        except (Refuse, OSError):
            pass
    if lstat(ctx.bw) is not None and mine_dir(ctx.bw):
        try:
            fd = lock(ctx)                                        # a start that is deciding right now finishes first and finds "off" afterwards
        except (Refuse, OSError):
            fd = None
        try:
            try:
                write_private(p(ctx, "float.json"), json.dumps({"float": "off", "removed": int(time.time())}))     # first: from this instant he does not come back by himself
            except OSError:
                pass
            for name in os.listdir(ctx.bw):                       # ensure.lock stays: a hook waiting on it must still find the same lock
                if name in ("Brote.app", "Brote.app.prev", "Brote.app.sha256", "Brote.app.sha256.new", "brote.lock",
                            "ensure.log", "events.log", "probe.log", "sessions") or name.startswith((".stage-", "float.json.tmp", "ensure.log.tmp")):
                    rm_tree(p(ctx, name))
        finally:
            if fd is not None:
                os.close(fd)
    for path in (os.path.join(ctx.lib, "Logs", "BroteWindow"),
                 os.path.join(ctx.lib, "Preferences", BUNDLE_ID + ".plist"),
                 os.path.join(ctx.lib, "Saved Application State", BUNDLE_ID + ".savedState")):
        rm_tree(path)
    print(say("uninstalled"))
    return 0


def cmd_ensure(ctx, args):
    if not is_mac():
        return 0
    try:
        choice = float_choice(ctx)
    except Refuse:
        return 0
    if choice == "off":
        return 0
    first = choice == "unset"
    if first and not auto_allowed():
        return 0
    inp = session_input()
    try:
        version = installed_version(ctx)
    except Refuse as e:
        if not first:
            try:
                register_session(ctx, claude_pid(args), inp.get("session_id"), inp.get("cwd"))
            except OSError:
                pass
            log(ctx, f"not launched: {e}")
        return 0
    if first:
        if not ctx.root.startswith(os.path.join(ctx.cfg, "plugins", "cache") + os.sep):
            return 0                                             # a folder somebody registered by hand is never turned on without the user typing `on`
        try:
            if plugin_bundle_problems(ctx):
                return 0                                         # nothing is written for a bundle that is not the reviewed one
            bw_create(ctx)
            fd = lock(ctx, 2.0)                                  # a hook never waits out its 5 s timeout
            try:
                now = float_choice(ctx)
                if now == "off":                                 # an `off` that landed first wins
                    return 0
                if now == "on":                                  # a concurrent first start decided: it says the one line, this one just joins
                    first = False
                else:
                    write_private(p(ctx, "float.json"), json.dumps({"float": "on", "auto": True, "version": version, "turned_on": int(time.time())}))
            finally:
                os.close(fd)
        except (Refuse, OSError):
            return 0
    try:
        register_session(ctx, claude_pid(args), inp.get("session_id"), inp.get("cwd"))
        if first:
            register_open_chats(ctx)                             # the chats open since before the update have no hook of ours: count them too
    except OSError as e:
        log(ctx, f"session file not written ({e.__class__.__name__})")
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        log(ctx, "not launched: remote login, there is no screen to show it on")
        return 0
    try:
        bad = bring_up(ctx, version, wait=0.5)               # a session start never waits for another one
    except Refuse as e:
        log(ctx, f"not launched: {e}")
        return 0
    if bad:
        log(ctx, "not launched: " + "; ".join(bad))
    elif first:
        print(json.dumps({"systemMessage": say("auto_on")}))     # the one line the chat gets, once
    return 0


def cmd_session_end(ctx, args):
    if not is_mac() or not mine_dir(ctx.bw):
        return 0
    pid = claude_pid(args)
    if pid:
        f = os.path.join(ctx.bw, "sessions", f"{pid}.json")
        st = lstat(f)
        if st is not None and stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
            os.unlink(f)
    return 0


COMMANDS = {"on": cmd_on, "off": cmd_off, "stop": cmd_stop, "status": cmd_status, "uninstall": cmd_uninstall,
            "ensure": cmd_ensure, "session-end": cmd_session_end}
HOOKS = ("ensure", "session-end")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd not in COMMANDS:
        print("usage: budgit brote on | off | stop | status | uninstall   (ensure and session-end are the hooks)", file=sys.stderr)
        return 2
    try:
        ctx = Ctx()
        return COMMANDS[cmd](ctx, argv[2:])
    except Exception as e:                       # a hook never fails a session; a command says why
        if cmd in HOOKS:
            try:
                log(Ctx(), f"{cmd} failed: {e.__class__.__name__}: {e}")
            except Exception:
                pass
            return 0
        if isinstance(e, Refuse):
            print(say("refused", why=str(e)))
            return 4
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv))
