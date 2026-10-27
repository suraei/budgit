"""R-241: tell the user when Budgit has an update, never install one.

Three read-only questions, all answered from files already on disk except `fetch_public_version`, which asks the public
repo once and only when the user runs `budgit update --check`:
  - stale chat: the plugin this chat loaded is not the one now on disk (a restart of the chat picks the new one up);
  - available: the marketplace clone Claude Code keeps holds a newer release than the one installed;
  - the commands the user can run to update (printed, never executed: the update runs only on their click).
Every file read here is the user's own and untrusted: strict patterns, bounded sizes, any problem means "nothing to say"."""
import json
import os
import re
import urllib.request

_HOMEPAGE = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/?$")
DEFAULT_MARKETPLACE = "budgit-hub"
NEW_PLUGIN = "budgit"
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION = re.compile(r"^\d{1,5}(\.\d{1,5}){0,3}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_BYTES = 262144


class UpdateError(Exception):
    pass


def valid_version(value):
    return isinstance(value, str) and bool(_VERSION.match(value))


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
            loc = _market(config, market)
            clone = _clone_version(loc, name) if loc else None
            if clone and installed and _key(clone) > _key(installed):
                return {"kind": "available", "running": installed, "available": clone}
    except Exception:
        pass                    # a notice must never break the line
    return None


def target(config_dir=None, root=None):
    """The two names the floating Brote needs to run the update after the user's click: {"plugin", "market"}, each a safe name."""
    name, _disk = _manifest(root or _plugin_root())
    market = marketplace(config_dir, root)
    return {"plugin": name or NEW_PLUGIN, "market": market if _NAME.match(market) else DEFAULT_MARKETPLACE}


def suffix(state, t):
    """The tail of the status line for `state` (empty when None); `t` gives the reopen text."""
    if not state:
        return ""
    if state["kind"] == "stale-chat":
        return " " + t("status.update_reopen")
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
