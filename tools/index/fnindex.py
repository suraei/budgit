"""fnindex — a function outline of a big source file, so an agent reads the outline first and then only the lines it needs.

Generated on demand and never stored, so it cannot go stale. No dependencies; Python uses the standard `ast`, Swift and JavaScript use
a line pass (a declaration inside a multi-line string or comment can show up; the outline is a map, not a parser).

Usage: python3 fnindex.py FILE [FILE ...]      (.py .swift .js .mjs)
Prints `path (N lines)`, then `line  declaration` per entry, indented one step per nesting level.
"""
import ast
import os
import re
import sys

MAX_TEXT = 100
SWIFT_MODS = r"(?:(?:public|private|fileprivate|internal|open|final|static|class|override|mutating|convenience|required|lazy|indirect|nonisolated)\s+)*"
SWIFT_DECL = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s+)*" + SWIFT_MODS + r"(?:func\b|class\b|struct\b|enum\b|extension\b|protocol\b|actor\b|init[?!]?\()")
SWIFT_MARK = re.compile(r"^//\s*MARK:")
JS_DECL = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b|^(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>|^(?:export\s+)?class\s+\w+")
LANGS = {".py": "py", ".swift": "swift", ".js": "js", ".mjs": "js"}


def _cut(text):
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def _python(src):
    lines = src.splitlines()
    out = []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return [(i + 1, (len(l) - len(l.lstrip())) // 4, _cut(l.strip())) for i, l in enumerate(lines)
                if re.match(r"\s*(async\s+def|def|class)\s", l)]

    def walk(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append((child.lineno, depth, _cut(lines[child.lineno - 1].strip())))
                if depth < 2:
                    walk(child, depth + 1)
            else:
                walk(child, depth)
    walk(tree, 0)
    return sorted(out)


def _lines(src, decl, mark=None, step=4, max_depth=3):
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        text = line.strip()
        depth = (len(line) - len(line.lstrip())) // step
        if (mark and mark.match(text)) or (decl.match(text) and depth <= max_depth):
            out.append((i, depth, _cut(text)))
    return out


def outline(src, ext):
    lang = LANGS.get(ext)
    if lang == "py":
        return _python(src)
    if lang == "swift":
        return _lines(src, SWIFT_DECL, SWIFT_MARK, step=4)
    if lang == "js":
        return _lines(src, JS_DECL, step=2, max_depth=1)
    raise ValueError("unsupported file type: %s" % (ext or "(none)"))


def render(path, src):
    n = src.count("\n") + (0 if src.endswith("\n") or not src else 1)
    rows = ["%s (%d lines)" % (path, n)]
    rows += ["%5d  %s%s" % (line, "  " * depth, text) for line, depth, text in outline(src, os.path.splitext(path)[1].lower())]
    return "\n".join(rows) + "\n"


def main(argv=None):
    paths = (sys.argv[1:] if argv is None else argv)
    if not paths:
        sys.stderr.write("usage: fnindex.py FILE [FILE ...]\n")
        return 2
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            sys.stdout.write(render(path, src))
        except (OSError, ValueError) as e:
            sys.stderr.write("fnindex: %s: %s\n" % (path, getattr(e, "strerror", None) or e))
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
