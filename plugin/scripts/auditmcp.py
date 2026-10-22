"""budgit audit-mcp — static supply-chain check of MCP server configs and broad permission rules (P6).

Reads a FIXED list of config files, never executes or contacts anything, and never keeps or prints a value from
env, headers, URLs or args: only key names. A verdict says how the server is SELECTED (pinned or floating), not
whether the package is trustworthy: SAFE means pinned, not verified. Deterministic, stdlib only.
"""
import json
import os
import re
from collections import namedtuple
from urllib.parse import urlsplit

MAX_BYTES = 1024 * 1024
MAX_ROWS = 54                      # + header, "+N more", summary, footer  <=  60 lines
RANK = {"SAFE": 0, "REVIEW": 1, "MEDIUM": 2, "HIGH": 3}
FOOTER = "SAFE means pinned, not verified (no integrity/provenance check)"

Finding = namedtuple("Finding", "file server command token verdict why")

EXACT_NPM = re.compile(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?")
EXACT_PY = re.compile(r"v?\d+(?:\.\d+)*(?:[.\-+]?[A-Za-z0-9]+)*")
SHA40 = re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{40}(?![0-9A-Fa-f])")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}(?![0-9a-f])")
ENV_REF = re.compile(r"\$\{?[A-Za-z_]|\{\{|%[A-Za-z_]+%")
SECRET_NAME = re.compile(r"(?i)(api[_-]?key|apikey|secret|token|passw(?:or)?d|passwd|pwd|credential|auth|bearer"
                         r"|private[_-]?key|access[_-]?key|cookie|signature)")
NOT_A_VALUE = re.compile(r"(?i)(file|path|dir|env|var|name|url|uri|endpoint|type|mode|limit|size|ttl|timeout"
                         r"|expir\w*|header|scheme)$")
SECRET_SHAPES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk)[-_][A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
]
TRIVIAL = {"", "true", "false", "0", "1", "yes", "no", "none", "null"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

NODE_FLAGS = {"--registry", "--prefix", "--cache", "--userconfig", "-c", "--call", "-w", "--workspace"}
PY_FLAGS = {"--with", "--with-editable", "--with-requirements", "-p", "--python", "--index-url", "-i",
            "--extra-index-url", "--index", "--default-index", "--env-file", "--directory", "--project",
            "--config-file", "--constraints", "-c", "--pip-args", "--python-preference", "--refresh-package",
            "--no-binary", "--only-binary"}
DOCKER_FLAGS = {"-e", "--env", "-v", "--volume", "--mount", "-p", "--publish", "--name", "--network", "--net",
                "-w", "--workdir", "-u", "--user", "--entrypoint", "--env-file", "-l", "--label", "--platform",
                "--add-host", "--cap-add", "--cap-drop", "--device", "-m", "--memory", "--cpus", "--restart",
                "--pull", "--hostname", "-h", "--ulimit", "--tmpfs", "--security-opt", "--group-add",
                "--log-driver", "--ipc", "--pid", "--runtime", "--gpus", "--dns", "--expose", "--shm-size",
                "-a", "--attach", "--cidfile", "--label-file", "--cgroupns", "--userns", "--log-opt", "--storage-opt", "--sysctl",
                "--volumes-from", "--link", "--ip", "--mac-address", "--stop-signal", "--stop-timeout",
                "--pids-limit", "--memory-swap", "--cpuset-cpus", "--health-cmd", "--cpu-shares", "--domainname"}
DOCKER_BOOL = {"--rm", "--init", "-i", "--interactive", "-t", "--tty", "-d", "--detach", "--read-only",
               "--privileged", "--sig-proxy", "--no-healthcheck", "--oom-kill-disable", "-P",
               "--publish-all"}
UNPARSED = "<unparsed>"
NODE_BOOL = {"-y", "--yes", "--no", "--no-install", "--ignore-existing", "-q", "--quiet", "--silent",
             "--prefer-offline", "--prefer-online", "--offline", "--bun", "--no-update-notifier", "--"}
PY_BOOL = {"--isolated", "--no-cache", "-q", "--quiet", "-v", "--verbose", "--offline", "--no-progress",
           "--native-tls", "--refresh", "--no-config", "--managed-python", "--no-managed-python",
           "--no-python-downloads", "-U", "--upgrade", "--reinstall", "--system-site-packages", "--pypackages"}
BROAD_PATHS = {"*", "**", "/**", "//**", "~/**", "/*", "//*", "~/*", "**/*", "/", "//", "~"}
FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit")


def _show(text, limit=None):
    """Printable ASCII only (no hidden/bidi chars, no newlines), optionally cut to limit."""
    text = re.sub(r"[^\x20-\x7e]", "?", str(text))
    if limit and len(text) > limit:
        text = text[:limit - 1] + "~"
    return text


def _flag_name(arg):
    """The flag's own name and nothing after it: `--zz=V` -> `--zz`, `-eK=V` / `-ZV` -> `-e` / `-Z`. A reason or
    token never carries argument text (round 2, N1)."""
    if arg.startswith("--"):
        return _show(arg.split("=", 1)[0], 30)
    return _show(arg[:2], 2)


# ---------------------------------------------------------------- secrets (names only, values never kept)

def _is_ref(value):
    return bool(ENV_REF.search(value)) or (value.startswith("<") and value.endswith(">"))


def _plain(value):
    return value.strip().lower() in TRIVIAL or _is_ref(value)


def _shape(value):
    return any(p.search(value) for p in SECRET_SHAPES)


def _secret_name(name):
    base = name.lstrip("-")
    return bool(SECRET_NAME.search(base)) and not NOT_A_VALUE.search(base)


def _url_secret(value):
    """Why a string carrying a URL holds credentials, or None. Names only."""
    if "://" not in value:
        bare = re.match(r"^[^\s/@:]+:([^\s/@]+)@(?!sha256:)[^\s/@]+/", value)      # registry user:pass@host/image
        return "url userinfo" if bare and not _plain(bare.group(1)) else None
    m = re.search(r"://([^/@\s:]*):([^/@\s]+)@", value)
    if m and not _plain(m.group(2)):
        return "url userinfo"
    m = re.search(r"://([^/@\s:]+)@", value)
    if m and _shape(m.group(1)):
        return "url userinfo"
    for m in re.finditer(r"[?&;]([^=&#\s]+)=([^&#\s]*)", value):
        key, val = m.group(1), m.group(2)
        if (_secret_name(key) or key.lower() in ("key", "sig", "sas")) and not _plain(val):
            return "url ?%s=" % re.sub(r"[^\w.-]", "?", key)[:30]
    return None


def _value_secret(label, name, value):
    if not isinstance(value, str) or _plain(value):
        return None
    if _url_secret(value):
        return "%s (credentials in URL)" % label
    if _shape(value) or (name and _secret_name(name) and not value.strip().lower() in TRIVIAL):
        return label
    return None


def _scalar(v):
    """Strings as is; numbers (not bools) as text, so a numeric secret is still seen."""
    return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _secrets(cfg, args):
    """(reasons, touched): reasons name WHERE a literal secret sits; touched holds the raw strings the check
    flagged, kept only so the caller can refuse to print them. Neither is ever printed as a value."""
    out, touched = [], set()

    def hit(why, *values):
        if why:
            out.append(why)
            touched.update(v for v in values if isinstance(v, str))

    for k, v in (cfg.get("env") if isinstance(cfg.get("env"), dict) else {}).items():
        v = _scalar(v)
        hit(_value_secret("env %s" % _show(k, 40), str(k), v), v)
    for k, v in (cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}).items():
        v = _scalar(v)
        hit(_value_secret("header %s" % _show(k, 40), str(k), v), v)
    url = cfg.get("url")
    if isinstance(url, str):
        hit(_url_secret(url) or ("url (secret shape)" if _shape(url) else None), url)
    cmd = cfg.get("command")
    if isinstance(cmd, str) and _shape(cmd):
        hit("command (secret shape)", cmd)
    i = 0
    while i < len(args):
        a, prev = args[i], args[i - 1] if i else ""
        m = re.match(r"^(-{0,2}[A-Za-z0-9_.-]+)=(.*)$", a, re.S)
        env = re.match(r"^(?:--env=|-e=?)([^=\s]+)=(.*)$", a, re.S)       # --env=K=V, -e=K=V, -eK=V
        if env:
            hit(_value_secret("arg env %s" % _show(env.group(1), 40), env.group(1), env.group(2)), a, env.group(2))
        elif prev in ("-e", "--env") and "=" in a:
            name, _, val = a.partition("=")
            hit(_value_secret("arg env %s" % _show(name, 40), name, val), a, val)
        elif m:
            label = ("arg %s" % _flag_name(m.group(1))) if m.group(1).startswith("-") else "positional arg #%d" % i
            hit(_value_secret(label, m.group(1), m.group(2)), a, m.group(2))
        elif a.startswith("-") and _secret_name(a) and i + 1 < len(args) and not args[i + 1].startswith("-"):
            why = _value_secret("arg %s" % _flag_name(a), a, args[i + 1])
            hit(why, args[i + 1])
            i += 1 if why else 0
        else:
            hit(_value_secret("positional arg #%d" % i, None, a), a)
        i += 1
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq, touched


_DIST_TAGS = {"latest", "next", "beta", "alpha", "canary", "rc", "stable", "dev", "nightly", "main", "master", "edge"}


def _token_text(tok):
    """A package/image token safe to print: no URL userinfo, query or fragment; never a secret shape."""
    tok = re.sub(r"(://)[^/@\s]*@", r"\1", tok).split("?")[0]
    tok = re.sub(r"^[^\s/@:]+:[^\s/@]+@(?!sha256:)", "", tok)               # registry user:pass@host/image
    if "://" in tok:
        tok = tok.split("#")[0]
    m = re.fullmatch(r"(@?[^@\s/]+(?:/[^@\s/]+)?)@([^@\s/:]+)", tok)         # pkg@tag: only well-known dist-tags print
    if m and not re.match(r"[vV]?\d|[\^~><=*]", m.group(2)) and m.group(2).lower() not in _DIST_TAGS:
        tok = m.group(1) + "@<tag>"
    return "<redacted>" if _shape(tok) else tok


# ---------------------------------------------------------------- selector verdicts

def _git_rule(tok):
    if SHA40.search(tok):
        return "SAFE", "git/tarball pinned to a commit sha"
    return "HIGH", "git/tarball source without a 40-hex commit sha (moves)"


def _is_local_path(tok):
    return tok.startswith((".", "/", "~", "file:", "link:")) or bool(re.match(r"^[A-Za-z]:[\\/]", tok))


def _is_remote(tok):
    return ("://" in tok or tok.startswith(("git+", "git:", "github:", "gitlab:", "bitbucket:", "gist:", "git@"))
            or re.search(r"\.(tgz|tar\.gz|zip|whl)$", tok) is not None
            or re.match(r"^[\w.-]+/[\w.-]+(#\S*)?$", tok) is not None)


def _tag_or_range(ver, exact):
    if ver == "":
        return "HIGH", "empty version selector (floats)"
    if exact.fullmatch(ver):
        return "SAFE", "exact version"
    if (ver[0] in "^~><=*" or ver.lower() == "x" or "||" in ver or " " in ver
            or re.search(r"(^|\.)[xX*]($|\.)", ver) or re.fullmatch(r"v?\d+(\.\d+)?", ver)):
        return "MEDIUM", "version range"
    return "HIGH", "floating tag"


def _node_spec(tok):
    if _is_local_path(tok):
        return "REVIEW", "local path"
    if _is_remote(tok) and not tok.startswith("@"):
        return _git_rule(tok)
    i = tok.rfind("@")
    if i <= 0:
        return "HIGH", "no version pin (resolves to latest)"
    return _tag_or_range(tok[i + 1:], EXACT_NPM)


def _py_spec(tok):
    if _is_local_path(tok):
        return "REVIEW", "local path"
    if _is_remote(tok) or " @ " in tok:
        return _git_rule(tok)
    m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", tok)
    if not m:
        return "REVIEW", "unrecognised package spec"
    rest = m.group(3).strip()
    if not rest:
        return "HIGH", "no version pin (resolves to latest)"
    if rest.startswith("@"):
        return _tag_or_range(rest[1:].strip(), EXACT_PY)
    if rest.startswith("=="):
        ver = rest.lstrip("=")
        return ("MEDIUM", "version range") if ("," in ver or "*" in ver) else ("SAFE", "exact version")
    return "MEDIUM", "version range"


def _docker_spec(args):
    """Image and verdict of `docker/podman run [flags] IMAGE ...`."""
    if args[:1] == ["container"]:
        args = args[1:]
    if args[:1] != ["run"]:
        return None, "REVIEW", "not a `run` invocation"
    i = 1
    while i < len(args):
        a = args[i]
        if a in DOCKER_FLAGS:
            i += 2
        elif a in DOCKER_BOOL or re.fullmatch(r"-[itdP]+", a) or (a.startswith("--") and "=" in a):
            i += 1
        elif len(a) > 2 and a[0] == "-" and a[1] != "-" and a[:2] in DOCKER_FLAGS:
            i += 1                                      # glued value: -eK=V, -e=K=V, -vHOST:CTR, -p80:80
        elif a.startswith("-"):
            return UNPARSED, "REVIEW", "unknown flag %s: cannot tell which word is the image" % _flag_name(a)
        else:
            if DIGEST.search(a):
                return a, "SAFE", "image pinned by sha256 digest"
            return a, "HIGH", "image tag is mutable (pin with @sha256:)"
        continue
    return None, "REVIEW", "no image found"


def _first_package(args, value_flags, bool_flags, package_flags):
    """(package token, unknown flag name) of a runner command line. The token is None when absent and UNPARSED
    when an unknown flag makes the position uncertain (its value could be mistaken for the package, and could be a
    secret); then the second item names that flag, never its value."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in package_flags:
            return (args[i + 1] if i + 1 < len(args) else None), None
        for pf in package_flags:
            if pf.startswith("--") and a.startswith(pf + "="):
                return a.split("=", 1)[1], None
        if a in bool_flags or (a.startswith("-") and "=" in a):
            i += 1
        elif a in value_flags:
            i += 2
        elif a.startswith("-"):
            return UNPARSED, _flag_name(a)
        else:
            return a, None
    return None, None


def _basename(cmd):
    base = os.path.basename(cmd.replace("\\", "/")).lower()
    return re.sub(r"\.(cmd|exe|bat|ps1)$", "", base)


def _runner(base, args):
    if base in ("cmd",) and args[:1] and args[0].lower() in ("/c", "/k") and len(args) > 1:
        return _runner(_basename(args[1]), args[2:])
    if base in ("npx", "bunx", "pnpx"):
        return "node", args
    if base in ("pnpm", "yarn") and args[:1] == ["dlx"]:
        return "node", args[1:]
    if base == "npm" and args[:1] in (["exec"], ["x"]):
        return "node", args[1:]
    if base == "bun" and args[:1] == ["x"]:
        return "node", args[1:]
    if base == "uvx":
        return "py", args
    if base == "pipx" and args[:1] == ["run"]:
        return "py", args[1:]
    if base == "uv" and args[:2] == ["tool", "run"]:
        return "py", args[2:]
    if base in ("docker", "podman"):
        return "docker", args
    return None, args


def _with_packages(args):
    out, i = [], 0
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args):
            out.append(args[i + 1])
            i += 1
        elif args[i].startswith("--with="):
            out.append(args[i].split("=", 1)[1])
        i += 1
    return out


def classify(cfg):
    """(command basename, token, verdict, [reasons]) for one server definition."""
    args = [a for a in cfg["args"] if isinstance(a, str)] if isinstance(cfg.get("args"), list) else []
    cmd, url = cfg.get("command"), cfg.get("url")
    secrets, touched = _secrets(cfg, args)
    verdict, whys, token, base = "REVIEW", [], "-", "-"
    if isinstance(cmd, str) and cmd.strip():
        base = _basename(cmd.strip()) or "-"
        kind, rest = _runner(base, args)
        if kind == "node":
            tok, flag = _first_package(rest, NODE_FLAGS, NODE_BOOL, {"-p", "--package"})
            if tok is None:
                verdict, whys = "HIGH", ["no package found in args"]
            elif tok == UNPARSED:
                token, verdict, whys = UNPARSED, "REVIEW", ["unknown flag %s: cannot tell which word is the package" % flag]
            else:
                token = tok
                verdict, why = _node_spec(tok)
                whys = [why]
        elif kind == "py":
            tok, flag = _first_package(rest, PY_FLAGS, PY_BOOL, {"--from", "--spec"})
            if tok is None:
                verdict, whys = "HIGH", ["no package found in args"]
            elif tok == UNPARSED:
                token, verdict, whys = UNPARSED, "REVIEW", ["unknown flag %s: cannot tell which word is the package" % flag]
            else:
                token = tok
                verdict, why = _py_spec(tok)
                whys = [why]
                for extra in _with_packages(rest):
                    v2, w2 = _py_spec(extra)
                    if RANK[v2] > RANK[verdict]:
                        verdict, whys = v2, ["--with: " + w2]
        elif kind == "docker":
            image, verdict, why = _docker_spec(rest)
            token, whys = image or "-", [why]
        else:
            verdict, whys = "REVIEW", ["local command, review the file"]
    elif isinstance(url, str) and url.strip():
        parts = urlsplit(url.strip())
        token = parts.hostname or "-"
        if parts.port:
            token += ":%d" % parts.port
        if parts.scheme == "http" and (parts.hostname or "") not in LOOPBACK:
            verdict, whys = "HIGH", ["plain http: traffic and tokens readable on the path"]
        elif parts.scheme == "http":
            verdict, whys = "REVIEW", ["plain http to loopback"]
        else:
            verdict, whys = "REVIEW", ["remote server: you trust its operator"]
    else:
        whys = ["no command or url"]
    if secrets:
        verdict = "HIGH"
        whys = ["literal secret in " + ", ".join(secrets)] + whys
    if token in touched or _shape(token) or _url_secret(token):
        token = "<redacted>"              # never print a value the secret check touched
    return ("<redacted>" if _shape(base) else base), _token_text(token), verdict, whys


# ---------------------------------------------------------------- settings

def _broad_rule(rule):
    rule = rule.strip()
    m = re.match(r"^([A-Za-z]+)(?:\((.*)\))?$", rule, re.S)
    if not m:
        return False
    tool, arg = m.group(1), m.group(2)
    arg = arg.strip() if arg is not None else None
    if tool in FILE_TOOLS or tool == "WebFetch":
        return arg is None if tool == "WebFetch" else arg is None or arg in BROAD_PATHS
    if tool == "Bash":
        return arg is None or arg in ("", "*", ":*", "*:*", "**")
    return False


def _settings(path, data):
    out = []
    if data.get("enableAllProjectMcpServers") is True:
        out.append(Finding(path, "-", "-", "enableAllProjectMcpServers", "HIGH",
                           "enableAllProjectMcpServers: true auto-approves every project .mcp.json server"))
    perms = data.get("permissions")
    allow = perms.get("allow") if isinstance(perms, dict) else None
    for rule in allow if isinstance(allow, list) else []:
        if isinstance(rule, str) and _broad_rule(rule):
            out.append(Finding(path, "-", "-", _show(rule.strip(), 40), "MEDIUM",
                               "broad allow rule: no per-call prompt for a whole class of actions"))
    return out


# ---------------------------------------------------------------- reading

def _servers_in(data):
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    vals = [v for v in data.values()]
    if vals and all(isinstance(v, dict) and ("command" in v or "url" in v or "type" in v) for v in vals):
        return data                       # flat form: {name: {command...}}
    return {}


class _Scan:
    def __init__(self, opener):
        self.opener = opener or open
        self.seen = set()
        self.out = []

    def note(self, path, why):
        self.out.append(Finding(path, "-", "-", "-", "REVIEW", "not scanned: " + why))

    def load(self, path):
        """Parsed JSON of a fixed-list file, or None (absent, skipped or unreadable)."""
        try:
            st = os.lstat(path)
        except OSError:
            return None
        key = os.path.realpath(path)
        if key in self.seen:
            return None
        self.seen.add(key)
        if os.path.islink(path):
            self.note(path, "symlink")
            return None
        if not os.path.isfile(path):
            return None
        if st.st_size > MAX_BYTES:
            self.note(path, "over 1 MB")
            return None
        try:
            with self.opener(path, "r", encoding="utf-8") as f:
                return json.loads(f.read(MAX_BYTES + 1))
        except (OSError, ValueError):
            self.note(path, "unreadable or invalid JSON")
            return None

    def servers(self, path, mapping):
        for name, cfg in mapping.items():
            if not isinstance(cfg, dict):
                continue
            base, token, verdict, whys = classify(cfg)
            self.out.append(Finding(path, _show(name, 40), _show(base, 20), _show(token, 80), verdict,
                                    _show("; ".join(whys))))

    def plugin(self, path, root):
        data = self.load(path)
        if not isinstance(data, dict):
            return
        ms = data.get("mcpServers")
        if isinstance(ms, dict):
            self.servers(path, ms)
            return
        refs = [ms] if isinstance(ms, str) else (ms if isinstance(ms, list) else [])
        real_root = os.path.realpath(root)
        for rel in refs:
            if not isinstance(rel, str):
                continue
            target = os.path.join(root, rel)
            real = os.path.realpath(target)
            if real != real_root and not real.startswith(real_root + os.sep):
                self.note(path, "mcpServers path outside the plugin dir")
                continue
            sub = self.load(target)
            if sub is not None:
                self.servers(target, _servers_in(sub))

    def project(self, root):
        data = self.load(os.path.join(root, ".mcp.json"))
        if data is not None:
            self.servers(os.path.join(root, ".mcp.json"), _servers_in(data))
        for rel in (".claude/settings.json", ".claude/settings.local.json"):
            data = self.load(os.path.join(root, rel))
            if isinstance(data, dict):
                self.out.extend(_settings(os.path.join(root, rel), data))
        self.plugin(os.path.join(root, ".claude-plugin", "plugin.json"), root)
        self.plugin(os.path.join(root, "plugin", ".claude-plugin", "plugin.json"), os.path.join(root, "plugin"))

    def home(self):
        home = os.path.expanduser("~")
        data = self.load(os.path.join(home, ".claude", "settings.json"))
        if isinstance(data, dict):
            self.out.extend(_settings(os.path.join(home, ".claude", "settings.json"), data))
        path = os.path.join(home, ".claude.json")
        data = self.load(path)
        if isinstance(data, dict):
            if isinstance(data.get("mcpServers"), dict):
                self.servers(path, data["mcpServers"])
            projects = data.get("projects")
            for proj in projects.values() if isinstance(projects, dict) else []:
                if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
                    self.servers(path, proj["mcpServers"])
        reg = self.load(os.path.join(home, ".claude", "plugins", "installed_plugins.json"))
        plugins = reg.get("plugins", reg) if isinstance(reg, dict) else {}
        for entries in plugins.values() if isinstance(plugins, dict) else []:
            for e in entries if isinstance(entries, list) else [entries]:
                ip = e.get("installPath") if isinstance(e, dict) else None
                if not isinstance(ip, str) or not ip:
                    continue
                data = self.load(os.path.join(ip, ".mcp.json"))
                if data is not None:
                    self.servers(os.path.join(ip, ".mcp.json"), _servers_in(data))
                self.plugin(os.path.join(ip, ".claude-plugin", "plugin.json"), ip)


def scan(paths, home=False, opener=None):
    """Findings for the config files under each path (+ the fixed home list). opener: open() stand-in for tests."""
    s = _Scan(opener)
    for p in paths:
        s.project(p)
    if home:
        s.home()
    return s.out


def high_count(findings):
    return sum(1 for f in findings if f.verdict == "HIGH")


# ---------------------------------------------------------------- report

def _where(path):
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    if path.startswith(cwd + os.sep):
        path = os.path.relpath(path, cwd)
    elif home and path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    return _show(path)


def _cut_left(text, width):
    return text if len(text) <= width else "~" + text[-(width - 1):]


def report(findings):
    fmt = "%-34s %-18s %-8s %-26s %-7s %s"
    lines = []
    if not findings:
        lines.append("audit-mcp: no MCP servers or risky settings found in the fixed file list")
    else:
        rows = sorted(enumerate(findings), key=lambda t: (-RANK[t[1].verdict], t[0]))
        lines.append(fmt % ("FILE", "SERVER", "COMMAND", "PACKAGE/IMAGE", "VERDICT", "WHY"))
        for _, f in rows[:MAX_ROWS]:
            lines.append((fmt % (_cut_left(_where(f.file), 34), _show(f.server, 18), _show(f.command, 8),
                                 _show(f.token, 26), f.verdict, _show(f.why, 70))).rstrip())
        if len(rows) > MAX_ROWS:
            lines.append("+%d more (worst first; same checks, rows not shown)" % (len(rows) - MAX_ROWS))
        counts = {}
        for f in findings:
            counts[f.verdict] = counts.get(f.verdict, 0) + 1
        servers = sum(1 for f in findings if f.server != "-")
        lines.append("audit-mcp: %d servers, %d other findings: %s" % (
            servers, len(findings) - servers,
            ", ".join("%d %s" % (counts[v], v) for v in ("HIGH", "MEDIUM", "REVIEW", "SAFE") if v in counts)))
    lines.append(FOOTER)
    return "\n".join(lines)
