#!/usr/bin/python3
"""MANIFEST.sha256 for the prebuilt floating Brote: write it at build time, check it before anything runs.

    floatmanifest.py write <float-dir>              hash Brote.app/**, BUILD-INFO.txt and src/** into <float-dir>/MANIFEST.sha256
    floatmanifest.py record <float-dir> <file>      note the manifest's hash as the reviewed build (the hub's VERIFIED.json)
    floatmanifest.py check <float-dir> [<ref-src>]  exit 0 when the folder is exactly what the manifest lists; each problem is printed;
                                                     with <ref-src> (the development sources) also fail when src/ is out of date with them

Format: "<64 hex>  <path>" per line, sorted by path, paths relative to <float-dir>: Brote.app/..., BUILD-INFO.txt, src/<name>.
It catches corruption, a partial copy, a stale or mixed bundle and a failed swap. It does not make an upstream commit trustworthy:
the manifest travels in the same commit as the binary.
"""
import hashlib
import os
import re
import stat
import sys

MANIFEST = "MANIFEST.sha256"
SRC_FILES = ["brote.swift", "Info.plist", "sprites.js", "say.js", "personality.js", "director.js", "menu.json", "book-icon.json",
             "build-universal.sh"]       # what the bundle is built from; they are copied to <float-dir>/src so the folder can rebuild itself
LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._/+-]+)$")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def bundle_files(float_dir, app_only=False):
    """Every path of the bundle that must be hashed: regular files only. Returns (paths, problems).
    app_only: just Brote.app (the installed copy has no BUILD-INFO.txt or src/)."""
    paths, problems = [], []
    base = os.path.join(float_dir, "Brote.app")
    if not os.path.isdir(base) or os.path.islink(base):
        problems.append("MISSING Brote.app (not a real folder)")
    else:
        for root, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                p = os.path.join(root, name)
                mode = os.lstat(p).st_mode
                rel = os.path.relpath(p, float_dir)
                if stat.S_ISLNK(mode):
                    problems.append(f"SYMLINK {rel}")
                elif stat.S_ISREG(mode):
                    paths.append(rel)
                elif not stat.S_ISDIR(mode):
                    problems.append(f"SPECIAL {rel}")
    if app_only:
        return sorted(paths), problems
    if os.path.isfile(os.path.join(float_dir, "BUILD-INFO.txt")):
        paths.append("BUILD-INFO.txt")
    else:
        problems.append("MISSING BUILD-INFO.txt")
    src = os.path.join(float_dir, "src")
    if os.path.isdir(src) and not os.path.islink(src):
        for name in os.listdir(src):
            p = os.path.join(src, name)
            mode = os.lstat(p).st_mode
            if stat.S_ISREG(mode):
                paths.append("src/" + name)
            else:
                problems.append(f"SPECIAL src/{name}")
    else:
        problems.append("MISSING src (not a real folder)")
    return sorted(paths), problems


def write(float_dir):
    paths, problems = bundle_files(float_dir)
    if problems:
        sys.exit("cannot write the manifest: " + "; ".join(problems))
    with open(os.path.join(float_dir, MANIFEST), "w") as f:
        for rel in paths:
            f.write(f"{sha256(os.path.join(float_dir, rel))}  {rel}\n")


def parse(text, name=MANIFEST):
    """Manifest text -> ({path: sha256}, problems)."""
    listed, problems = {}, []
    for n, line in enumerate(text.split("\n")[:-1], 1):
        m = LINE.match(line)
        if not m or m.group(2).startswith("/") or ".." in m.group(2).split("/") or m.group(2) in listed:
            problems.append(f"BAD {name} line {n}")
            continue
        listed[m.group(2)] = m.group(1)
    return listed, problems


def app_lines(float_dir):
    """The manifest lines of Brote.app only, as text: what the installed copy is checked against."""
    path = os.path.join(float_dir, MANIFEST)
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 64 * 1024:         # a pipe, a link or something huge in its place is not a manifest
        raise ValueError("MANIFEST.sha256 is not a plain small file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as f:
        return "".join(l for l in f.read().splitlines(True) if l[66:].startswith("Brote.app/"))


def check_app(base_dir, text, name="Brote.app.sha256"):
    """Problems of <base_dir>/Brote.app against manifest text that lists only Brote.app files."""
    listed, problems = parse(text, name)
    paths, more = bundle_files(base_dir, app_only=True)
    problems += more
    for rel in paths:
        if rel not in listed:
            problems.append(f"EXTRA {rel}")
        elif sha256(os.path.join(base_dir, rel)) != listed[rel]:
            problems.append(f"MISMATCH {rel}")
    problems += [f"MISSING {rel}" for rel in listed if rel not in paths]
    return problems


def check(float_dir, ref_src=None):
    """Return the list of problems; empty means the folder is exactly what the manifest lists."""
    path = os.path.join(float_dir, MANIFEST)
    if not os.path.isfile(path) or os.path.islink(path):
        return [f"MISSING {MANIFEST}"]
    with open(path, encoding="utf-8") as f:
        listed, problems = parse(f.read())
    paths, more = bundle_files(float_dir)
    problems += more
    for rel in paths:
        if rel not in listed:
            problems.append(f"EXTRA {rel}")
        elif sha256(os.path.join(float_dir, rel)) != listed[rel]:
            problems.append(f"MISMATCH {rel}")
    for rel in listed:
        if rel not in paths:
            problems.append(f"MISSING {rel}")
    if ref_src is not None:
        for n in SRC_FILES:
            p = os.path.join(ref_src, n)
            if "src/" + n not in listed:
                problems.append(f"UNLISTED src/{n}")
            elif not os.path.isfile(p) or sha256(p) != listed["src/" + n]:
                problems.append(f"STALE src/{n} (the source changed after the build)")
    return problems


def record(float_dir, record_path):
    """Write the hub's note of the reviewed build: the hash of MANIFEST.sha256, which covers every file of it."""
    import json
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump({"manifest_sha256": sha256(os.path.join(float_dir, MANIFEST)),
                   "meaning": "the plugin/float build that the security review and the verifier passed; export refuses any other"}, f, indent=2)
        f.write("\n")


def reviewed_problems(float_dir, record_path):
    """Problems when the bundle is not the build the hub recorded as reviewed."""
    import json
    name = os.path.basename(record_path)
    try:
        with open(record_path, encoding="utf-8") as f:
            want = json.load(f).get("manifest_sha256")
        have = sha256(os.path.join(float_dir, MANIFEST))
    except (OSError, ValueError, AttributeError):
        return [f"MISSING {name} (the hub records the reviewed build there; run: floatmanifest.py record <float-dir> <file>)"]
    return [] if want == have else [f"the bundle is not the reviewed build (MANIFEST.sha256 differs from {name})"]


def main(argv):
    if len(argv) == 3 and argv[1] == "write":
        write(argv[2])
        return 0
    if len(argv) == 4 and argv[1] == "record":
        record(argv[2], argv[3])
        return 0
    if argv[1:2] == ["check"] and len(argv) in (3, 4):
        problems = check(argv[2], argv[3] if len(argv) == 4 else None)
        for p in problems:
            print(p)
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
