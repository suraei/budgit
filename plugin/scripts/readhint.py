"""R-940: a whole read of a large ledger.md gets a context hint (never a denial)."""
import os

HINT = "R-940: the ledger is large; use `budgit ledger show R-n` or grep for the id instead of reading it whole."
LIMIT_BYTES = 50 * 1024


def hint(tool, tool_input):
    """The hint text for a Read of a ledger.md over 50 KB with no offset or limit, else None."""
    if tool != "Read" or not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    if not isinstance(path, str) or os.path.basename(path) != "ledger.md":
        return None
    if tool_input.get("offset") or tool_input.get("limit"):
        return None
    try:
        return HINT if os.path.getsize(path) > LIMIT_BYTES else None
    except OSError:
        return None
