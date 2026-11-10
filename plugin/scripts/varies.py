"""R-811: the "what varies" lens as a small checker the plan-critic and verifier can name.

find_local_constants(text) -> list[str]: sentences that turn a figure measured on one machine into a
constant. Narrow on purpose. A sentence is flagged only when ALL hold:
  1. it holds a number next to a unit word (cores, workers, CPUs, threads, GB, MB, ms, s, and
     the Spanish núcleos, hilos, procesos, trabajadores), as "8 workers" or "workers = 8";
  2. it names this machine: "my", "mine", "I have", "I've got", "this Mac/machine/PC/laptop",
     "here" (but not "Here is" or "Here are"), "locally", "on my box", "in my case",
     "en mi caso", "mi máquina";
  3. it has no real reader of the value: os.cpu_count, os.process_cpu_count,
     sched_getaffinity, psutil, "read at run time", "detected at run time". The bare word
     "runtime" does not count as a reader.
Sentences end at ". ", "!", "?", ";" or a newline. Figures with no local marker (a spec limit such
as "timeout 30 s") and sentences that read the value at run time are never flagged.
Known residuals (not flagged): briefs in other languages (German and so on), third-person briefs
("the author's Mac has 8 cores"), and a cap with no marker ("cap at 5 workers"). The agent's own
reading covers those."""
import re

_UNITS = (r"(?:cores?|workers?|cpus?|threads?|gb|mb|ms|s|n[uú]cleos?|hilos?|procesos?|"
          r"trabajadores)")
_NUM_UNIT = re.compile(r"\b\d+(?:\.\d+)?\s*" + _UNITS + r"\b", re.I)
_UNIT_NUM = re.compile(r"\b" + _UNITS + r"\s*[=:]\s*\d+\b", re.I)
_LOCAL = re.compile(
    r"\b(?:my|mine|here(?!\s+(?:is|are)\b)|locally|i\s+have|i've\s+got|"
    r"this\s+(?:mac|machine|laptop|pc)|on\s+my\s+box|"
    r"in\s+my\s+case|en\s+mi\s+caso|mi\s+m[aá]quina)\b", re.I)
_RUNTIME = re.compile(
    r"(?:cpu_count|sched_getaffinity|psutil|read\s+at\s+run[\s-]*time|"
    r"detected\s+at\s+run[\s-]*time)", re.I)
_SPLIT = re.compile(r"(?<=[.!?])\s+|[;\n]+")


def find_local_constants(text):
    found = []
    for sentence in _SPLIT.split(text or ""):
        s = sentence.strip()
        if not s:
            continue
        if not (_NUM_UNIT.search(s) or _UNIT_NUM.search(s)):
            continue
        if not _LOCAL.search(s):
            continue
        if _RUNTIME.search(s):
            continue
        found.append(s)
    return found


if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    hits = find_local_constants(data)
    for h in hits:
        print("LOCAL CONSTANT: " + h)
    sys.exit(1 if hits else 0)
