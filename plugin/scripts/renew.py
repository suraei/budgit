"""R-834: renew a coordinator in place at about 70 % context (the user's choice: renew in place).
The CLI cannot call app tools: it checks the preconditions, writes the handoff and returns the ordered tool steps the
coordinator runs itself. One visible row at all times: clear the session in place, else swap atomically."""
import studiolib as sl


class Refused(sl.StudioError):
    pass


def check_preconditions(live_subagents, live_tasks):
    """Refuse while a subagent or a background task is live, naming each one."""
    live = ["subagent %s" % n for n in live_subagents] + ["background task %s" % n for n in live_tasks]
    if live:
        raise Refused("renew refused: still running: %s. Wait for them or stop them, then renew." % ", ".join(live))


def steps(title, handoff_path, remote_control=True, keep_awake=True):
    """The exact ordered steps. Remote Control and keep-awake are turned off first when the app allows it;
    when it does not, the one-click step for the user comes back and the atomic swap is the fallback."""
    out = []
    if remote_control or keep_awake:
        out.append("1. Turn off this session's Remote Control and keep-awake (app tools). If the app refuses, give the user "
                   "one one-click step for it and go to step 4.")
    out.append("2. Check the handoff file is there and complete: %s" % handoff_path)
    out.append("3. Clear this session in place (clear_session on \"self\"), then start again from the handoff file; "
               "the name \"%s\" and the sidebar row stay the same. Done." % title)
    out.append("4. If the clear is refused (Remote Control, keep-awake or the app): atomic swap in one step: start the "
               "successor with the exact title \"%s\" and archive this session in the same step; never two visible "
               "rows. The successor starts from the handoff file." % title)
    return out


def prepare(project, session, title, live_subagents=(), live_tasks=(), writer=None,
            remote_control=True, keep_awake=True):
    """-> (handoff path, steps). Raises Refused before anything is written."""
    check_preconditions(live_subagents, live_tasks)
    if writer is None:
        import handoff
        writer = handoff.write
    path, _facts = writer(project, session, None)
    return path, steps(title, path, remote_control, keep_awake)
