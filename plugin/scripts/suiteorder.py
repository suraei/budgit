"""R-402: the order in which waiting full suites get a slot, fair across projects.

Without this the slots (suitequeue.py) went to whoever polled first, so a project with many lanes could fill the queue and
keep another project's one suite waiting. Now every waiting suite is a ticket in `suite-queue.json` under the Budgit home
(changed only under an flock on `suite-queue.lock`), and only the ticket at the head of the order may take a free slot:

  1. a release suite (`--release`, BUDGIT_SUITE_RELEASE=1) goes before every ordinary one;
  2. among ordinary suites, projects take turns: the project served least recently goes first (round robin), and inside
     one project the oldest ticket goes first;
  3. one waiting suite per checkout: a second request from a checkout that already waits is refused, because the suite
     already queued from there tests the same tree.

A ticket belongs to a process: when its pid is gone (or the ticket is a day old) it is dropped, so a killed run never
blocks the queue. The pure part (`order`) takes plain data so it is tested without files or processes."""
import fcntl
import json
import os
import time

import studiolib as sl

QUEUE = "suite-queue.json"
LOCK = "suite-queue.lock"
MAX_AGE = 86400
KEEP_SERVED = 200


class AlreadyQueued(sl.StudioError):
    def __init__(self, top, position):
        super().__init__("a suite from %s is already waiting (place %d)" % (top, position))
        self.top, self.position = top, position


def project_key(path):
    """The project a checkout belongs to: the git folder all its worktrees share (so a project's lanes count as one), else
    the folder itself."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                           text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.realpath(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.realpath(path)


def _paths():
    base = sl.home_state()
    return os.path.join(base, QUEUE), os.path.join(base, LOCK)


class _Locked:
    def __enter__(self):
        path, lock = _paths()
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        self.fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _load():
    try:
        data = json.loads(sl.read_text(_paths()[0]))
    except (OSError, ValueError):
        data = {}
    waiting = [t for t in data.get("waiting", []) if isinstance(t, dict) and isinstance(t.get("id"), str)] if isinstance(data, dict) else []
    served = data.get("served") if isinstance(data, dict) and isinstance(data.get("served"), dict) else {}
    return {"waiting": waiting, "served": {k: v for k, v in served.items() if isinstance(v, (int, float))}}


def _save(data):
    served = data["served"]
    if len(served) > KEEP_SERVED:
        data["served"] = dict(sorted(served.items(), key=lambda kv: kv[1])[-KEEP_SERVED:])
    sl.atomic_write(_paths()[0], json.dumps(data))


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def _prune(data, now):
    data["waiting"] = [t for t in data["waiting"] if alive(t.get("pid")) and now - float(t.get("since") or 0) < MAX_AGE]


def order(waiting, served):
    """The tickets in the order they will be served: releases first by age, then projects in turn (least recently served
    first, a project never served before anyone served), the oldest ticket of each project first."""
    releases = sorted((t for t in waiting if t.get("release")), key=lambda t: (t.get("since") or 0, t["id"]))
    queues = {}
    for t in sorted((t for t in waiting if not t.get("release")), key=lambda t: (t.get("since") or 0, t["id"])):
        queues.setdefault(t.get("project") or "", []).append(t)
    last = {p: served.get(p, 0) for p in queues}
    out = list(releases)
    while queues:
        project = min(queues, key=lambda p: (last[p], queues[p][0].get("since") or 0, p))
        out.append(queues[project].pop(0))
        last[project] = max(last.values()) + 1                      # it has had its turn; the others come before it
        if not queues[project]:
            del queues[project]
    return out


def join(ticket_id, project, top, release=False, label=""):
    """Take a place in the queue. Raises AlreadyQueued when a suite from the same checkout already waits."""
    with _Locked():
        data = _load()
        now = time.time()
        _prune(data, now)
        ranked = order(data["waiting"], data["served"])
        for place, t in enumerate(ranked):
            if t.get("top") == top and t.get("pid") != os.getpid():
                raise AlreadyQueued(top, place)
        data["waiting"].append({"id": ticket_id, "pid": os.getpid(), "project": project, "top": top, "release": bool(release),
                                "label": label, "since": now})
        _save(data)


def position(ticket_id):
    """-> (places ahead of this ticket, tickets waiting). A ticket that is not in the queue is at place 0."""
    with _Locked():
        data = _load()
        _prune(data, time.time())
        ranked = order(data["waiting"], data["served"])
        for place, t in enumerate(ranked):
            if t["id"] == ticket_id:
                return place, len(ranked)
        return 0, len(ranked)


def leave(ticket_id, project=None):
    """Drop the ticket; with `project`, also note that project was just served (its next ticket goes after the others')."""
    with _Locked():
        data = _load()
        data["waiting"] = [t for t in data["waiting"] if t["id"] != ticket_id]
        if project is not None:
            data["served"][project] = max(list(data["served"].values()) + [time.time()]) + 1e-3
        _save(data)


def snapshot():
    """For `budgit` tools and tests: the order now as [{project, release, top, label}]."""
    with _Locked():
        data = _load()
        _prune(data, time.time())
        return [{k: t.get(k) for k in ("project", "release", "top", "label")} for t in order(data["waiting"], data["served"])]
