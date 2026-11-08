"""R-241: tell the user when Budgit has an update, never install one.

Three read-only questions, all answered from files already on disk except `fetch_public_version`, which asks the public
repo (raw.githubusercontent.com, a plain GET) when the user runs `budgit update --check`, and once a day from a detached
child unless BUDGIT_NO_UPDATE_CHECK=1 or "update_check": "off" in config.json turns it off:
  - stale chat: the plugin this chat loaded is not the one now on disk (a restart of the chat picks the new one up);
  - available: the marketplace clone Claude Code keeps holds a newer release than the one installed;
  - the commands the user can run to update (printed, never executed: the update runs only on their click).
Every file read here is the user's own and untrusted: strict patterns, bounded sizes, any problem means "nothing to say"."""
import fcntl
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request

_HOMEPAGE = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/?$")
DEFAULT_MARKETPLACE = "budgit-hub"
NEW_PLUGIN = "budgit"
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_BYTES = 262144
CHECK_EVERY = 24 * 3600


class UpdateError(Exception):
    pass


def valid_version(value):
    return isinstance(value, str) and bool(_VERSION.fullmatch(value))


def _key(version):
    return tuple(int(p) for p in version.split("."))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.loads(f.read(_MAX_BYTES))
        return data
    except (OSError, ValueError):
        return None


def _plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest(root):
    data = _read_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    if not isinstance(data, dict):
        return None, None
    name, version = data.get("name"), data.get("version")
    return (name if isinstance(name, str) and _NAME.match(name) else None), (version if valid_version(version) else None)


def _config_dir(config_dir):
    return config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def _installs(config_dir, name):
    """[(marketplace, installed version or None)] for this plugin in Claude Code's registry, in file order."""
    data = _read_json(os.path.join(config_dir, "plugins", "installed_plugins.json"))
    plugins = data.get("plugins") if isinstance(data, dict) else None
    out = []
    if not isinstance(plugins, dict):
        return out
    for key, entries in plugins.items():
        plugin, _at, market = str(key).partition("@")
        if plugin != name or not _NAME.match(market):
            continue
        first = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
        version = first.get("version")
        out.append((market, version if valid_version(version) else None))
    return out


def _market(config_dir, market):
    """The known-marketplaces entry for `market` when it is a clone Claude Code can refresh (not a directory source)."""
    known = _read_json(os.path.join(config_dir, "plugins", "known_marketplaces.json"))
    entry = known.get(market) if isinstance(known, dict) else None
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    if isinstance(source, dict) and source.get("source") == "directory":
        return None             # it loads the folder in place: its registry version means nothing
    loc = entry.get("installLocation")
    return loc if isinstance(loc, str) and os.path.isabs(loc) else None


def _clone_version(loc, name):
    """The version the marketplace clone at `loc` carries for plugin `name`."""
    listing = _read_json(os.path.join(loc, ".claude-plugin", "marketplace.json"))
    plugins = listing.get("plugins") if isinstance(listing, dict) else None
    for entry in plugins if isinstance(plugins, list) else ():
        if not isinstance(entry, dict) or entry.get("name") != name:
            continue
        source = entry.get("source")
        sub = source if isinstance(source, str) and source.startswith("./") and ".." not in source.split("/") else "."
        manifest = _read_json(os.path.join(loc, sub, ".claude-plugin", "plugin.json"))
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if valid_version(version):
            return version
        if isinstance(entry.get("version"), str) and valid_version(entry["version"]):
            return entry["version"]
    return None


def _is_directory_source(config_dir, market):
    known = _read_json(os.path.join(config_dir, "plugins", "known_marketplaces.json"))
    entry = known.get(market) if isinstance(known, dict) else None
    source = entry.get("source") if isinstance(entry, dict) else None
    return isinstance(source, dict) and source.get("source") == "directory"


def valid_repo(repo):
    return isinstance(repo, str) and bool(_REPO.match(repo))


def public_repo(root=None):
    """OWNER/NAME of the public repo from the exported manifest's homepage; None for a checkout that was never exported."""
    data = _read_json(os.path.join(root or _plugin_root(), ".claude-plugin", "plugin.json"))
    match = _HOMEPAGE.match(data.get("homepage", "")) if isinstance(data, dict) and isinstance(data.get("homepage"), str) else None
    return match.group(1) if match else None


def migration_plan(repo, config_dir=None, root=None):
    """R-241 / O1: the plugin installed from a live folder (a directory marketplace) moves to the public marketplace.
    -> None when there is nothing to move, else {"old": "<name>@<marketplace>", "commands": [argv, ...]}. Install first, then
    disable the old one, so a failed install changes nothing; the old plugin is disabled, never uninstalled."""
    name, _disk = _manifest(root or _plugin_root())
    config = _config_dir(config_dir)
    olds = [m for m, _v in _installs(config, name) if _is_directory_source(config, m)] if name else []
    if not olds:
        return None
    if not valid_repo(repo):
        raise UpdateError("bad repo")
    old = "%s@%s" % (name, olds[0])
    return {"old": old, "commands": [["claude", "plugin", "marketplace", "add", repo],
                                     ["claude", "plugin", "install", "%s@%s" % (NEW_PLUGIN, DEFAULT_MARKETPLACE)],
                                     ["claude", "plugin", "disable", old]]}


def update_state(loaded=None, config_dir=None, root=None):
    """None when there is nothing to say, else {"kind": "stale-chat"|"available", "running": v, "available": v}."""
    try:
        name, disk = _manifest(root or _plugin_root())
        if not name or not disk:
            return None
        if valid_version(loaded) and _key(loaded) != _key(disk):
            return {"kind": "stale-chat", "running": loaded, "available": disk}
        config = _config_dir(config_dir)
        for market, installed in _installs(config, name):
            if valid_version(loaded) and installed and _key(installed) > _key(loaded):
                return {"kind": "stale-chat", "running": loaded, "available": installed}     # R-353: updated since this chat loaded (a copy kept per version)
            loc = _market(config, market)
            clone = _clone_version(loc, name) if loc else None
            if clone and installed and _key(clone) > _key(installed):
                return {"kind": "available", "running": installed, "available": clone}
        public = _public_cached(root)
        if is_newer(public, disk):
            return {"kind": "available", "running": disk, "available": public}
    except Exception:
        pass                    # a notice must never break the line
    return None


def running_version(root=None):
    """R-301: the version this chat runs, from the manifest under its own hook path (None when unreadable)."""
    return _manifest(root or _plugin_root())[1]


def installed_version(config_dir=None, root=None):
    """R-301: the highest valid version of this plugin in Claude Code's registry (None when there is none)."""
    try:
        name, _disk = _manifest(root or _plugin_root())
        found = [v for _m, v in _installs(_config_dir(config_dir), name) if v] if name else []
        return max(found, key=_key) if found else None
    except Exception:
        return None


def registered_root(root, config_dir=None):
    """R-712: the version Claude Code registered for the plugin folder `root`, or None when that folder is not a trusted install
    (not an installPath of the registry nor a directory-marketplace live folder, a marketplaces/ copy, not owned by the user, or
    writable by group or others). The one predicate `floatctl.installed_version` makes, reused so a scheduled job is never pointed
    at a folder Claude Code did not install."""
    try:
        import floatctl
        ctx = object.__new__(floatctl.Ctx)
        ctx.root = os.path.realpath(root)
        ctx.flt = os.path.join(ctx.root, "float")
        ctx.cfg = os.path.realpath(_config_dir(config_dir))
        version = floatctl.installed_version(ctx)
        if not valid_version(version):
            version = running_version(ctx.root)
        return version if valid_version(version) else None
    except Exception:
        return None


def older(version, than):
    """True when both are valid versions and `version` is lower than `than`."""
    return valid_version(version) and valid_version(than) and _key(version) < _key(than)


def chat_is_stale(config_dir=None, root=None):
    """R-301: this chat runs a lower version than the one installed; a new or resumed chat would load the newer one."""
    return older(running_version(root), installed_version(config_dir, root))


def target(config_dir=None, root=None):
    """The two names the floating Brote needs to run the update after the user's click: {"plugin", "market"}, each a safe name."""
    name, _disk = _manifest(root or _plugin_root())
    market = marketplace(config_dir, root)
    return {"plugin": name or NEW_PLUGIN, "market": market if _NAME.match(market) else DEFAULT_MARKETPLACE}


def suffix(state):
    """The tail of the status line for `state` (empty when None). A stale chat gets none (R-467): a new chat loads the new version,
    so the line never nags the person to type /reload-plugins."""
    if not state or state["kind"] == "stale-chat":
        return ""
    return " ↑" + state["available"]


def marketplace(config_dir=None, root=None):
    """The marketplace name to print in the update commands: the one this plugin came from, else the public default."""
    try:
        name, _disk = _manifest(root or _plugin_root())
        config = _config_dir(config_dir)
        for market, _installed in _installs(config, name):
            if _market(config, market):
                return market
    except Exception:
        pass
    return DEFAULT_MARKETPLACE


def commands(config_dir=None, root=None):
    """The two commands that update the plugin (`budgit update` prints them and runs nothing; the Brote update button runs them only after the user says yes)."""
    name, _disk = _manifest(root or _plugin_root())
    market = marketplace(config_dir, root)
    return ["claude plugin marketplace update %s" % market, "claude plugin update %s@%s" % (name or "budgit", market)]


def update_url(env=None, root=None):
    """Where the public plugin.json lives: derived from the `homepage` the export writes into the manifest (so this source
    names no account), None for a checkout that was never exported. BUDGIT_UPDATE_URL only redirects it to a local file
    (for tests), never to the network."""
    override = (env if env is not None else os.environ).get("BUDGIT_UPDATE_URL") or ""
    if override.startswith("file://"):
        return override
    data = _read_json(os.path.join(root or _plugin_root(), ".claude-plugin", "plugin.json"))
    match = _HOMEPAGE.match(data.get("homepage", "")) if isinstance(data, dict) and isinstance(data.get("homepage"), str) else None
    return "https://raw.githubusercontent.com/%s/main/plugin/.claude-plugin/plugin.json" % match.group(1) if match else None


def fetch_public_version(url=None):
    """One request for the public plugin.json; the version it names. Raises UpdateError on any failure."""
    url = url or update_url()
    if not url:
        raise UpdateError("no public repo known")
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read(_MAX_BYTES))
    except Exception as exc:
        raise UpdateError(type(exc).__name__)
    version = data.get("version") if isinstance(data, dict) else None
    if not valid_version(version):
        raise UpdateError("bad answer")
    return version


def current_version(root=None):
    return _manifest(root or _plugin_root())[1]


def is_newer(candidate, current):
    return valid_version(candidate) and valid_version(current) and _key(candidate) > _key(current)


# R-555: Claude Code never refreshes a third-party marketplace clone by itself, so the clone check in update_state stays blind.
# Once a day a detached child asks the public repo (the one fixed URL, a plain GET) and caches the version; the hook only reads it.
def _cache_path():
    import studiolib
    return os.path.join(studiolib.home_state(), "update-check.json")


def _check_disabled():
    """BUDGIT_NO_UPDATE_CHECK=1 in the environment, or "update_check": "off" in the home config.json (read only)."""
    if (os.environ.get("BUDGIT_NO_UPDATE_CHECK") or "").strip().lower() not in ("", "0", "false", "no"):
        return True
    import studiolib
    return studiolib._home_config().get("update_check") == "off"


def _read_cache():
    """(checked_at, version). A checked_at that is not a finite number, or lies more than a day ahead, reads as 0 (stale)."""
    path = _cache_path()
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):  # a FIFO or device must never be opened
            return 0, None
    except OSError:
        return 0, None
    data = _read_json(path)
    if not isinstance(data, dict):
        return 0, None
    at, version = data.get("checked_at"), data.get("version")
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at) or at > time.time() + CHECK_EVERY:
        at = 0
    return at, (version if valid_version(version) else None)


def _write_cache(at, version):
    import studiolib
    os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
    studiolib.atomic_write(_cache_path(), json.dumps({"checked_at": at, "version": version}) + "\n")


def _spawn_refresh(root=None):
    """Claim the interval first (a burst of status lines starts one child, not many), then fetch in a detached child:
    the hook never waits on the network. A non-blocking lock makes the stale-read-then-claim step atomic: a caller that
    finds the lock busy leaves the work to the holder, and one that finds the cache fresh inside the lock has lost the race."""
    import studiolib
    home = studiolib.home_state()
    os.makedirs(home, exist_ok=True)
    fd = os.open(os.path.join(home, "update-check.lock"),             # O_NONBLOCK: a FIFO here fails fast, never hangs
                 os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        at, version = _read_cache()
        if time.time() - at < CHECK_EVERY:
            return
        _write_cache(time.time(), version)
    finally:
        os.close(fd)  # closing releases the lock, before the child starts
    args = [sys.executable, os.path.abspath(__file__), "--refresh-cache"] + ([root] if root else [])
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _public_cached(root=None):
    """The cached public version (None when unknown or the check is off); starts a refresh when the cache is a day old."""
    if _check_disabled():
        return None
    at, version = _read_cache()
    if time.time() - at >= CHECK_EVERY and update_url(root=root):
        try:
            _spawn_refresh(root)
        except Exception:
            pass
    return version


def refresh_cache(root=None):
    """The child's job: one fetch, then the cache. Any failure keeps the old version; checked_at is written either way."""
    try:
        if _check_disabled():
            return
        url = update_url(root=root)
        if not url:
            return
        version = _read_cache()[1]
        try:
            version = fetch_public_version(url)
        except Exception:
            pass
        _write_cache(time.time(), version)
    except Exception:
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--refresh-cache"]:
    refresh_cache(sys.argv[2] if len(sys.argv) > 2 else None)
