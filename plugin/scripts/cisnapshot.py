"""R-1366: `budgit ci snapshot`. One clean commit of local main's committed tree on the snapshot branch's OWN chain: the first
snapshot is parentless, later ones take the previous snapshot as parent after the whole first-parent chain is checked to be
snapshot commits down to a parentless root. origin/main or any remote-tracking ref is never a parent and never trusted. The new
commit is scanned in full with leak.push_check (everything reachable from it) before any ref is written. Never pushes: it prints
the one push command for the user to run. Does not run the secrets scan (the caller runs `budgit secrets` on main)."""
import os
import re
import shlex
import subprocess

import leak
import studiolib as sl

DEFAULT_BRANCH = "ci-snapshot"
SUBJECT_PREFIX = "CI snapshot of main "
REFUSED_BRANCHES = ("main", "master")


def _git(repo, *args, env=None):
    r = subprocess.run(sl.safe_git_argv(repo, *args), capture_output=True, text=True, timeout=60,
                       env=env if env is not None else sl.safe_git_env())
    return r.returncode, r.stdout.strip()


def _refuse(why):
    return "ci snapshot: REFUSED: %s" % why, 1


def _identity(config):
    """(name, noreply email) from the configured default GitHub account, or None. Never the git config identity."""
    try:
        config = config if config is not None else sl.load_config()
        account = config.get("default_github_account")
        email = (config.get("github_noreply") or {}).get(account) if account else None
    except Exception:
        return None
    if not account or not email or sl._noreply_account(email) != str(account).lower():
        return None
    return str(account), str(email)


def _chain_ok(repo, branch):
    """True when refs/heads/<branch> is a first-parent chain of snapshot commits (one parent at most) ending at a parentless root."""
    code, out = _git(repo, "log", "--first-parent", "--format=%P|%s", "refs/heads/" + branch)
    if code or not out:
        return False
    lines = out.split("\n")
    for line in lines:
        parents, _, subject = line.partition("|")
        if not subject.startswith(SUBJECT_PREFIX) or len(parents.split()) > 1:
            return False
    return not lines[-1].partition("|")[0].split()


_URL_FORMS = (re.compile(r"git@([^:/]+):([^/]+)/[^/]+"), re.compile(r"https://([^/@]+)/([^/]+)/[^/]+"),
              re.compile(r"ssh://git@([^/:]+)/([^/]+)/[^/]+"))


def _url_owner(url):
    """(host, owner) from a git@, https:// or ssh://git@ remote URL (with or without .git), else None."""
    for form in _URL_FORMS:
        m = form.fullmatch((url or "").strip().rstrip("/"))
        if m:
            if m.group(1) != "github.com":    # exact lowercase github.com only: no port, no uppercase, no other host
                return None
            return m.group(1), m.group(2)
    return None


def _all_urls(repo, remote, account):
    """{"push": [urls], "fetch": [urls]} when EVERY url of the remote (get-url --all, with and without --push, so pushurl,
    insteadOf, pushInsteadOf and a second url line all count) is github.com under the account; else None (fail closed)."""
    found = {}
    for key, extra in (("push", ("--push",)), ("fetch", ())):
        code, out = _git(repo, "remote", "get-url", *extra, "--all", remote)
        urls = [u.strip() for u in out.split("\n") if u.strip()] if not code else []
        if not urls:
            return None
        for u in urls:
            owner = _url_owner(u)
            if not owner or owner[0] != "github.com" or owner[1].lower() != str(account).lower():
                return None
        found[key] = urls
    return found


def _real_history(repo):
    """None when the history is real, else the reason to refuse: replace refs, a shallow repo or a grafts file."""
    code, out = _git(repo, "for-each-ref", "refs/replace")
    if code or out:
        return "replace refs present"
    code, out = _git(repo, "rev-parse", "--is-shallow-repository")
    if code or out != "false":
        return "shallow repository"
    code, common = _git(repo, "rev-parse", "--git-common-dir")
    if code or not common or os.path.exists(os.path.join(repo, common, "info", "grafts")):
        return "grafts file present"
    return None


def snapshot(repo, branch=None, remote=None, config=None):
    """-> (text, exit code): 0 made or nothing new, 1 refused. Text carries aliases, never a protected name."""
    branch = branch or DEFAULT_BRANCH
    remote = remote or "origin"
    repo = os.path.abspath(repo)
    if branch.startswith("-") or remote.startswith("-"):
        return _refuse("bad branch or remote name")
    if branch.lower() in REFUSED_BRANCHES:
        return _refuse("the branch name is main or master")
    try:
        names = leak._push_names(config)
        folder = os.path.basename(repo.rstrip(os.sep))
        if names and leak.hide(branch, names) != branch:
            return _refuse("the branch name holds a protected name")
        if folder and folder.lower() in branch.lower():
            return _refuse("the branch name holds the project folder name")
    except ValueError:
        return _refuse("the protected-names list cannot be read")
    code, ok = _git(repo, "check-ref-format", "--branch", branch)
    if code:
        return _refuse("not a valid branch name")
    code, ok = _git(repo, "check-ref-format", "refs/remotes/%s/x" % remote)
    if code:
        return _refuse("not a valid remote name")
    who = _identity(config)
    if not who:
        return _refuse("no noreply identity")
    name, email = who
    urls = _all_urls(repo, remote, name)
    if not urls:
        return _refuse("remote is not in your GitHub account")
    url = "\n".join(urls["push"])
    bad = _real_history(repo)
    if bad:
        return _refuse(bad)
    code, tree = _git(repo, "rev-parse", "--verify", "-q", "refs/heads/main^{tree}")
    if code or not tree:
        return _refuse("no local main")
    code, main_sha = _git(repo, "rev-parse", "--verify", "-q", "refs/heads/main")
    if code or not main_sha:
        return _refuse("no local main")
    code, old = _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
    parent = None
    if not code and old:
        if not _chain_ok(repo, branch):
            return _refuse("branch is not a snapshot chain")
        parent = old
        code, parent_tree = _git(repo, "rev-parse", "--verify", "-q", parent + "^{tree}")
        if not code and parent_tree == tree:
            return "ci snapshot: nothing new (main's tree equals the snapshot's)", 0
    else:
        old = ""
    env = dict(sl.safe_git_env())
    env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
    args = ["-c", "user.name=" + name, "-c", "user.email=" + email, "-c", "commit.gpgsign=false", "commit-tree", tree]
    if parent:
        args += ["-p", parent]
    args += ["-m", SUBJECT_PREFIX + main_sha[:8]]
    code, new = _git(repo, *args, env=env)
    if code or not new:
        return _refuse("could not create the commit")
    hits = leak.push_check(repo, config, rev=new, exclude=[])    # range mode, no --not: a full scan of everything reachable
    if hits:
        return leak.names_report(hits).replace("names-check:", "ci snapshot:", 1) + "\nno ref was written", 1
    code, _ = _git(repo, "update-ref", "refs/heads/" + branch, new, old)
    if code:
        return _refuse("the branch moved or could not be updated")
    # If the remote branch holds anything else, a non-fast-forward push is refused by the remote. Never print a force push.
    text = ("ci snapshot: clean (%d files), refs/heads/%s is %s\nremote %s pushes to %s\nrun after your yes:\n"
            "git -C %s push %s %s" % (hits.files, branch, new[:8], remote, url, shlex.quote(repo), shlex.quote(remote),
                                      shlex.quote("refs/heads/%s:refs/heads/%s" % (branch, branch))))
    return leak.hide(text, names), 0
