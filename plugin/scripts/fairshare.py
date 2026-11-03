"""R-402: how many lanes each project holds right now, so `budgit admit lane --project DIR` can share the machine fairly.

A lane is a busy, verified Claude session attributed to a project (livework's attribution: the session pointer, else the
folder it works in, a worktree folded into its repo) whose title is not a coordinator's (PM, TM). A project has pending
work when its ledger has an open or doing item. Only counts leave this module: no name or path is returned for a project
other than the one asked about, and nothing here reads a protected project's content beyond the item statuses. Best effort:
when the sessions or a ledger cannot be read the project simply counts as it can be counted (never an exception)."""
import studiolib as sl

COORDINATOR_PREFIXES = ("pm ·", "pm -", "tm ·", "tm -")


def is_coordinator(title):
    t = str(title or "").strip().casefold()
    return any(t.startswith(p) for p in COORDINATOR_PREFIXES)


def has_pending(project):
    try:
        data = sl.load_ledger(project)
    except sl.StudioError:
        return False
    return any(isinstance(i, dict) and i.get("status") in ("open", "doing") for i in data.get("items", []))


def survey(sessions=None):
    """-> {project path: {"lanes": n, "pending": bool}} for every project that has a ledger."""
    import livework
    if sessions is None:
        found = livework.live_sessions()
        sessions = found["sessions"] if found.get("status") == "ok" else []
    projects = livework.all_projects(sessions=sessions, cap=1 << 20)
    lanes = {}
    for s in sessions:
        project = projects["attrib"].get(s["session_id"])
        if project and s.get("status") == "busy" and s.get("verified") and not is_coordinator(s.get("name")):
            lanes[project] = lanes.get(project, 0) + 1
    return {row["path"]: {"lanes": lanes.get(row["path"], 0), "pending": has_pending(row["path"])} for row in projects["projects"]}


def share(project, sessions=None):
    """The fair-share facts about `project` for admit: {lanes, pending_projects, starved, most} or None when the project
    has no ledger (then admit judges the machine alone). `starved` is how many OTHER projects with pending work hold no
    lane; `most` says this project holds the most lanes of the projects with pending work (and another one waits on it)."""
    import livework
    key = livework.canon(project)
    table = survey(sessions)
    if key not in table:
        try:
            sl.load_ledger(project)
        except sl.StudioError:
            return None                                            # no ledger: admit judges the machine alone
        table[key] = {"lanes": 0, "pending": True}                 # a ledger the survey did not see: it holds no lane yet
    table[key]["pending"] = True                                   # it is asking for a lane, so it has work
    pending = {k: v for k, v in table.items() if v["pending"]}
    mine = table[key]["lanes"]
    others = [v["lanes"] for k, v in pending.items() if k != key]
    return {"lanes": mine, "pending_projects": len(pending), "starved": sum(1 for n in others if n == 0),
            "most": bool(others) and mine > 0 and mine >= max(others)}
