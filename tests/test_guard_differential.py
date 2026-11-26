"""R-335 round 3 / R-302: the regression CLASS. The base real-data guard (commit 820f2cb, before any R-335 work) is
loaded next to the current one and run over a corpus. Anything the base asked about that the current guard passes must be
in ALLOWED below: a short, reviewed list of the three forms the items asked for (R-342 read-only git, R-335 `git
add|commit` by pathspec and readers under the standing yes, R-355 Budgit bookkeeping). Everything else fails the test."""
import importlib.util
import os
import subprocess
import tempfile
import unittest

from helpers import ROOT
from test_guard_trust import GuardCase, CLI, git
import studiolib as sl

BASE_COMMIT = "820f2cb"


def load_base():
    got = subprocess.run(["git", "-C", ROOT, "show", "%s:plugin/scripts/studiolib.py" % BASE_COMMIT],
                         capture_output=True, text=True)
    if got.returncode:
        return None
    path = os.path.join(tempfile.mkdtemp(prefix="base-studiolib-"), "studiolib_base.py")
    with open(path, "w") as fh:
        fh.write(got.stdout)
    spec = importlib.util.spec_from_file_location("studiolib_base", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GIT_WRITERS = [
    "reset --hard", "reset --hard HEAD~1", "reset --soft HEAD~1", "reset HEAD data.json", "checkout -f main",
    "checkout --force main", "checkout -B newb", "checkout .", "checkout -- data.json", "checkout main", "stash",
    "stash pop", "stash -u", "stash drop", "merge feat", "merge --abort", "rebase main", "rebase --continue", "pull",
    "pull --rebase", "apply p.patch", "apply --index p.patch", "revert HEAD", "cherry-pick HEAD", "am p.mbox", "push",
    "push --force", "config user.name x", "config --global core.pager x", "clean -fd", "clean -fdx", "rm data.json",
    "rm -r --cached .", "rm -rf data", "mv data.json x.json", "restore data.json", "restore .", "restore --staged data.json",
    "switch feat", "switch -c newb", "worktree add ../w", "worktree remove ../w", "submodule update --init", "gc",
    "gc --prune=now", "prune", "repack -ad", "update-ref -d refs/heads/feat", "update-ref HEAD HEAD~1", "branch -D feat",
    "branch -m feat other", "branch newb", "branch -f feat HEAD", "tag -d x", "tag newtag", "commit -a -m x",
    "commit --amend -m x", "commit -m x", "commit -m x -- data.json", "commit -m x -- docs/a.md",
    "commit -m x -- data/real/x.csv", "commit -i -m x -- docs/a.md", "add data.json", "add docs/a.md", "add -A", "add .",
    "add -u", "add -f data.json", "add --pathspec-from-file=list.txt", "fetch", "fetch --prune", "remote add o u",
    "remote remove o", "checkout-index -a -f", "read-tree --reset HEAD", "update-index --assume-unchanged data.json",
    "filter-branch --force", "reflog expire --expire=now --all", "fsck", "replace HEAD HEAD~1", "notes add -m x",
    "bisect start", "archive -o data.json HEAD", "archive -o out.tar HEAD", "format-patch -o data HEAD~1",
    "bundle create b HEAD", "diff --output=data.json", "log --output=x", "log --outp=data.json", "diff -o data.json",
    "grep -O'sh x.sh' one", "grep --open-files-in-pager=sh one", "grep -Osh one", "diff --ext-diff", "log -p --ext-diff",
    "show --textconv HEAD:data.json", "cat-file --textconv HEAD:data.json", "diff --textconv", "-c core.fsmonitor='sh x.sh' status",
    "-c diff.external='sh x.sh' diff", "-c core.pager='sh x.sh' log", "--paginate log", "-p log", "--exec-path=/x status",
    "--config-env=core.pager=X log", "-c alias.status='!sh x.sh' status", "--git-dir=.git status", "--work-tree=. status",
    "--no-pager log --oneline", "ls-files -x '*.md'", "ls-files --exclude-from=x", "describe --dirty", "rev-parse --exec-path",
    "status -s", "status --porcelain", "status -sb", "status --ignored", "status --untracked-files=no",
]
GIT_READERS = [
    "status", "log --oneline -3", "log --oneline main..feat", "log --stat", "log -p", "log -5", "log -n 5", "log --graph --all",
    "log --format=%h", "log --oneline -- data.json", "log --follow -- data.json", "diff", "diff --stat", "diff --stat HEAD",
    "diff --cached", "diff --name-only", "diff -- data.json", "diff HEAD~1", "show HEAD", "show --stat HEAD",
    "show HEAD:data.json", "branch --list", "branch -a", "branch -v", "branch -vv", "branch --show-current",
    "branch --contains HEAD", "branch --merged main", "branch -a --list 'f*'", "rev-parse HEAD", "rev-parse --show-toplevel",
    "rev-parse --abbrev-ref HEAD", "ls-files", "ls-files -m", "ls-files --others --exclude-standard", "blame docs/a.md",
    "blame -L 1,2 docs/a.md", "describe --always", "shortlog -sn HEAD", "grep one", "grep -n one", "grep -i -e one",
    "grep -n one -- data.json", "merge-base main feat", "merge-base --is-ancestor main feat", "rev-list --count HEAD",
    "rev-list HEAD", "ls-tree HEAD", "ls-tree -r --name-only HEAD", "cat-file -p HEAD", "cat-file -t HEAD",
    "log --output=data.json --oneline", "log $'--out\\x70ut=data.json'", "log --format=%h $(echo -o x)", "log -- 'd*'",
    "log -- *.json", "log -- data.js?n", "log -- {data.json,x}", "log -- ~/x", "log -- $HOME", "log -- `echo data.json`",
    "log --oneline | tee data.json", "log --oneline > data.json", "log --oneline >| data.json", "log &> data.json",
    "log --oneline 2> data.json", "status; rm data.json", "status && git add data.json", "status; cp x data.json",
    "status | wc -l", "status; echo done", "log --oneline | head -5", "log --oneline && git status || echo no",
]
PLAIN = [
    "cat data.json", "cat data/real/x.csv", "ls data", "ls -la", "head -3 data.json", "grep one data.json", "wc -l data.json",
    "rm data.json", "rm -rf data", "rm -rf .", "rm -rf {R}", "rm -rf {R}/data", "mv data.json x.json", "mv x data.json",
    "cp data.json x.json", "cp x.json data.json", "cp -r data /tmp/z", "cp -t {R} x.json", "cp -t data x.json",
    "cp --target-directory={R} x.json", "cp --target-directory {R} x.json", "cp -t{R} x", "mv -t {R} x", "install -t {R} x",
    "ln -s x data.json", "ln -sf x data.json", "install x data.json", "rsync -a x/ {R}/", "rsync -a x/ data/",
    "rsync --delete -a x/ {R}/", "rsync -a {R}/ /tmp/z/", "dd if=x of=data.json", "dd if=x of={R}/data.json",
    "touch data.json", "touch {R}/new", "touch new", "chmod 600 data.json", "chmod -R 700 {R}", "truncate -s 0 data.json",
    "sed -i s/a/b/ data.json", "sed s/a/b/ data.json", "perl -pi -e 's/a/b/' data.json", "find . -delete",
    "find {R} -delete", "find data -exec rm {} +", "find . -name x", "find {R} -name x", "du -sh {R}", "du -sh .", "tree {R}",
    "df {R}", "realpath {R}", "tree -o out.txt {R}", "ls {R}", "ls {R}/data", "cat {R}/data.json", "stat {R}",
    "tar xf a.tar", "tar xf a.tar -C {R}", "tar xf a.tar -C data", "tar -xzf a.tgz", "tar czf out.tgz docs", "tar cf a.tar data",
    "unzip a.zip", "unzip -o a.zip", "unzip a.zip -d {R}", "unzip a.zip -d data", "7z x a.7z -o{R}", "7z x a.7z", "ditto a {R}",
    "ditto x data", "echo hi > data.json", "echo hi >> data.json", "echo hi >| data.json", "echo hi &> data.json",
    "echo hi 2> data.json", "echo hi 1>| data.json", "echo hi > {R}/data.json", "echo hi >{R}/data.json", "echo hi > docs/a.md",
    "echo hi > /tmp/x", "cat x > data.json", "cat <<EOF > data.json\nx\nEOF", "cat > data.json <<EOF\nx\nEOF",
    "tee data.json", "echo x | tee data.json", "echo x | tee -a data/real/x.csv", "echo x | tee /tmp/x",
    "python3 -c \"open('data.json','w').write('x')\"", "python3 x.py data.json", "python3 x.py", "node -e 'x' data.json",
    "sh -c 'rm data.json'", "bash -c \"rm {R}/data.json\"", "sh -c 'echo hi'", "eval rm data.json", "xargs rm data.json",
    "env rm data.json", "sudo rm data.json", "time rm data.json", "cd {R}", "cd {R} && ls", "cd {R} && rm data.json",
    "cd {R} && tar xf a.tar", "cd {R} && unzip -o a.zip", "cd {R} && cp x.json data.json", "cd {R} && git status",
    "cd {R} && git log --oneline", "cd {R}/data && ls", "cd {R}/docs && rm a.md", "cd {R}/docs && ls", "cd", "cd ~", "cd -",
    "cd && ls", "cd ~ && ls", "pushd {R}", "pushd {R}/data", "popd", "cd /tmp && rm data.json", "cd /tmp; cat x",
    "(rm data.json)", "(cd {R} && rm data.json)", "(ls; rm data.json)", "{ rm data.json; }", "echo x; rm data.json",
    "echo x && rm data.json", "echo x || rm data.json", "echo x | xargs rm data.json", "rm data.json &", "ls & rm data.json",
    "cat $(cp x data.json)", "cat <(cp x data.json)", "echo `rm data.json`", "cat $(ls)", "cat $(echo data.json)",
    "echo $(rm data.json)", "echo ${X}", "cat \"$(cat data.json)\"", "rm $(echo data.json)", "ls >(tee data.json)",
    "X=1 rm data.json", "PATH=/tmp:$PATH rm data.json", "rm data.js*", "rm data.js?n", "rm da*.json", "rm data/re*", "cat data/*",
    "cat data/real/*", "ls data/real", "rm -rf data/real", "rm data/real/x.csv", "mkdir data/real/new", "mkdir newdir",
    "git clone x {R}/y", "make -C {R} clean", "npm --prefix {R} install", "docker run -v {R}:/x img", "open {R}", "code {R}",
    "rm docs/a.md", "cat docs/a.md", "cp docs/a.md docs/c.md", "mv docs/a.md docs/c.md", "echo hi > docs/c.md",
    "pwd", "echo hi", "true", "test -f data.json", "[ -f data.json ]", "basename {R}", "dirname {R}", "readlink {R}",
    "git status; cat data.json", "ls data.json; rm data.json", "cat data.json | wc -l", "wc -l < data.json",
]
BUDGIT = [
    "{B} --project {R} ledger ack I-22 --note x", "{B} ledger ack I-22 --note x", "{B} --project {R} ledger add --text hi",
    "{B} --project={R} ledger done R-1 --evidence data.json", "{B} --project {R} ledger done R-1 --evidence docs/a.md",
    "{B} --project {R} status", "{B} --project {R} status --line", "{B} --project {R} status --limits-json x",
    "{B} --project {R} budget", "{B} --project {R} budget set low", "{B} --project {R} budget record",
    "{B} --project {R} budget auto", "{B} --project {R} denies --since 7d", "{B} --project {R} ledger export x",
    "{B} --project {R} export", "{B} --project {R} migrate", "{B} --project {R} prepare", "{B} --project {R} trust work",
    "{B} --project {R}/data/real ledger ack I-1", "{B} --project {R} ledger ack I-1 && rm data.json",
    "{B} --project {R} ledger ack I-1; cp x data.json", "{B} --project {R} ledger ack I-1 | tee data.json",
    "{B} --project {R} ledger ack I-1 > data.json", "PATH=/tmp:$PATH {B} --project {R} ledger ack I-1",
    "X=1 {B} --project {R} ledger ack I-1", "cd {R} && {B} ledger ack I-1", "xargs {B} --project {R} ledger ack I-1",
    "env {B} --project {R} ledger ack I-1", "sh {B} --project {R} ledger ack I-1", "{B} --project {R} ledger ack $(echo I-1)",
    "budgit --project {R} ledger ack I-1", "budgit() { rm data.json; }; budgit --project {R} ledger ack I-1",
    "/tmp/budgit --project {R} ledger ack I-1", "{B} --project {R} ledger ack I-1 --note data.json",
    "{B} --project {R} --project {R}/data ledger ack I-1", "{B} ledger ack --project {R} I-1",
]


# R-558: copies whose source ends in `/` or `/.` write what the folder HOLDS into the target (ditto always does)
CONTENT_COPIES = ["cp -R {D}/ {R}", "cp -R {D}/. {R}", "cp -a {D}/ {R}", "cp -a {D}/. {R}", "cp -r {D}/ {R}/", "rsync -a {D}/ {R}",
                  "rsync -a {D}/ {R}/", "ditto {D} {R}", "ditto {D}/ {R}", "cp -R {D}/ {R}/data/real"]


# R-558 round 2 / R-562: a harmless-contents source {H}, a source called like protected data {N}, a stage {S} filled by an
# earlier part of the command, a source with a symlinked folder {L}, -T, -Rt, and copies into the folder ABOVE the project {P}
# ({PB} is a backup folder that holds a folder named like the project)
ROUND2_COPIES = [
    "cp -R {H}/ {R}", "cp -R {H}/. {R}", "cp -a {H}/ {R}", "rsync -a {H}/ {R}", "ditto {H} {R}",
    "cp -R {N}/ {R}", "cp -a {N}/ {R}/", "cp -R {N} {R}", "cp -RT {N} {R}", "cp -R {N}/. {R}",
    "cp x {S}/data.json && cp -R {S}/ {R}", "echo x > {S}/data.json && cp -R {S}/ {R}", "mkdir -p {S}/z && cp -a {S}/. {R}",
    "cp -RL {L}/ {R}", "cp -R -L {L}/. {R}", "cp -RH {L}/ {R}", "cp --dereference -R {L}/ {R}",
    "cp -RT {H} {R}", "cp -R -T {D} {R}", "cp --no-target-directory -R {D} {R}",
    "cp -Rt {R} {N}", "cp -aRt {R} {D}/", "cp -Rt{R} {D}/", "cp --target={R} -R {D}/", "cp --target-directory={R} -R {N}",
    "cp --rec {D}/ {R}", "cp --arc {D}/ {R}", "cp --ar {N} {R}",
    "cp -R {H}/ {R}/docs/ {R}", "cp -R {H}/ {H}/. {R}",
    "cp -R {PB}/{NAME} {P}", "cp -R {PB}/{NAME}/ {P}", "cp -R {PB}/ {P}", "cp -R {PB}/. {P}", "cp -Rt {P} {PB}/{NAME}",
    "rsync -a {PB}/{NAME} {P}", "rsync -a {PB}/ {P}", "rsync -a --delete {H}/ {P}", "ditto {PB} {P}", "mv {PB}/{NAME} {P}",
    "cp -R {H} {P}", "cp {H}/notes.txt {P}", "cp -R {H}/ {P}",
    # R-558 round 4 (security review r3, N1-N3): the allow-list for the trusted contents copy, rsync -R / --relative /
    # --files-from and cp --parents, rsync options that write a file in both forms, a folder lost by pushd / cd ~-
    "cp -RT {H} {R}", "cp -RL {H}/ {R}", "cp --recursive {D}/ {R}", "cp -R -- {D}/ {R}", "ditto -rsrc {D} {R}",
    "gcp -R {D}/ {R}", "rsync -aR {PB}/./{NAME}/ {P}", "rsync -a --relative {PB}/./{NAME}/ {P}",
    "rsync -a --files-from={PB}/list {PB}/ {P}", "cp --parents {PB}/{NAME}/data.json {P}", "gcp --parents {H}/notes.txt {R}",
    "rsync -a --log-file {R}/data.json {H}/ /tmp/b-r4", "rsync -a --log-file={R}/data.json {H}/ /tmp/b-r4",
    "rsync -a --write-batch {R}/data.json {H}/ /tmp/b-r4", "rsync -a --only-write-batch {R}/data.json {H}/ /tmp/b-r4",
    "rsync -a --backup-dir {R}/data {H}/ /tmp/b-r4", "rsync -a --partial-dir {R}/data {H}/ /tmp/b-r4",
    "rsync -a --temp-dir {R}/data {H}/ /tmp/b-r4", "rsync -aT {R}/data {H}/ /tmp/b-r4",
    "cd .. && cd /tmp && cd - && cp -R {D}/ {NAME}", "cd .. && pushd /tmp && pushd && cp {D}/data.json {NAME}",
    "cd .. && pushd /tmp && pushd +1 && cp {D}/data.json {NAME}", "cd .. && cd /tmp && cd ~- && cp -R {D}/ {NAME}",
    "pushd -n /tmp && cp x data.json", "cd .. && cd /tmp && cd - && rm data.json",
]


def corpus(root, budgit, drop=None, extra=None):
    cmds = []
    if drop:
        cmds += [c.replace("{R}", root).replace("{D}", drop) for c in CONTENT_COPIES]
    if extra:
        for c in ROUND2_COPIES:
            c = c.replace("{R}", root).replace("{D}", drop or "")
            for key, value in extra.items():
                c = c.replace("{%s}" % key, value)
            cmds.append(c)
    for v in GIT_WRITERS + GIT_READERS:
        cmds += ["git %s" % v, "git -C %s %s" % (root, v)]
    for v in GIT_READERS[:14] + GIT_WRITERS[:6]:
        cmds.append("git -C %s/docs %s" % (root, v))
        cmds.append("git -C . %s" % v)
        cmds.append("git --no-pager %s" % v)
    for spelling in ("Cafe\u0301", "Caf\u00e9"):          # the other Unicode form of a protected folder (NFD, NFC)
        cmds += ["rm %s/x.csv" % spelling, "cp /tmp/x %s/x.csv" % spelling, "echo x > %s/x.csv" % spelling,
                 "git -C %s restore %s/x.csv" % (root, spelling), "cd %s && rm %s/x.csv" % (root, spelling)]
    cmds += [c.replace("{R}", root) for c in PLAIN]
    cmds += [c.replace("{R}", root).replace("{B}", budgit) for c in BUDGIT]
    seen, out = set(), []
    for c in cmds:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# The reviewed exceptions. {R} is the protected project root. Each is one plain command whose verb and options are on an
# allowlist (R-342), a `git add|commit` judged by its pathspec (R-335), or Budgit's own bookkeeping (R-355).
ALLOWED_BY_FAMILY = {
    'R-342 read-only git': [
        'budgit --project {R} ledger ack I-1',
        'git --no-pager log --follow -- data.json',
        'git --no-pager log --oneline -- data.json',
        'git -C . diff',
        'git -C . diff --stat',
        'git -C . diff --stat HEAD',
        'git -C . log --follow -- data.json',
        'git -C . log --format=%h',
        'git -C . log --graph --all',
        'git -C . log --oneline -- data.json',
        'git -C . log --oneline -3',
        'git -C . log --oneline main..feat',
        'git -C . log --stat',
        'git -C . log -5',
        'git -C . log -n 5',
        'git -C . log -p',
        'git -C . status',
        'git -C {R} --no-pager log --oneline',
        'git -C {R} blame -L 1,2 docs/a.md',
        'git -C {R} blame docs/a.md',
        'git -C {R} branch --contains HEAD',
        'git -C {R} branch --list',
        'git -C {R} branch --merged main',
        'git -C {R} branch --show-current',
        'git -C {R} branch -a',
        'git -C {R} branch -v',
        'git -C {R} branch -vv',
        'git -C {R} cat-file -p HEAD',
        'git -C {R} cat-file -t HEAD',
        'git -C {R} describe --always',
        'git -C {R} diff',
        'git -C {R} diff -- data.json',
        'git -C {R} diff --cached',
        'git -C {R} diff --name-only',
        'git -C {R} diff --stat',
        'git -C {R} diff --stat HEAD',
        'git -C {R} diff HEAD~1',
        'git -C {R} grep -i -e one',
        'git -C {R} grep -n one',
        'git -C {R} grep -n one -- data.json',
        'git -C {R} grep one',
        'git -C {R} log --follow -- data.json',
        'git -C {R} log --format=%h',
        'git -C {R} log --graph --all',
        'git -C {R} log --oneline && git status || echo no',
        'git -C {R} log --oneline -- data.json',
        'git -C {R} log --oneline -3',
        'git -C {R} log --oneline main..feat',
        'git -C {R} log --oneline | head -5',
        'git -C {R} log --stat',
        'git -C {R} log -5',
        'git -C {R} log -n 5',
        'git -C {R} log -p',
        'git -C {R} ls-files',
        'git -C {R} ls-files --others --exclude-standard',
        'git -C {R} ls-files -m',
        'git -C {R} ls-tree -r --name-only HEAD',
        'git -C {R} ls-tree HEAD',
        'git -C {R} merge-base --is-ancestor main feat',
        'git -C {R} merge-base main feat',
        'git -C {R} rev-list --count HEAD',
        'git -C {R} rev-list HEAD',
        'git -C {R} rev-parse --abbrev-ref HEAD',
        'git -C {R} rev-parse --show-toplevel',
        'git -C {R} rev-parse HEAD',
        'git -C {R} shortlog -sn HEAD',
        'git -C {R} show --stat HEAD',
        'git -C {R} show HEAD',
        'git -C {R} show HEAD:data.json',
        'git -C {R} status',
        'git -C {R} status && git add data.json',
        'git -C {R} status --ignored',
        'git -C {R} status --porcelain',
        'git -C {R} status --untracked-files=no',
        'git -C {R} status -s',
        'git -C {R} status -sb',
        'git -C {R} status | wc -l',
        'git -C {R} status; echo done',
        'git -C {R}/docs log --follow -- data.json',
        'git -C {R}/docs log --oneline -- data.json',
        'git diff -- data.json',
        'git grep -n one -- data.json',
        'git log --follow -- data.json',
        'git log --oneline -- data.json',
        'git show HEAD:data.json',
    ],
    'R-335 git add/commit judged by pathspec': [
        'git -C {R} add docs/a.md',
        'git -C {R} commit --amend -m x',
        'git -C {R} commit -i -m x -- docs/a.md',
        'git -C {R} commit -m x',
        'git -C {R} commit -m x -- docs/a.md',
        'git add -f data.json',
        'git add data.json',
        'git commit -m x -- data.json',
        'git status && git add data.json',
    ],
    'R-335 readers under the standing yes': [
        'basename {R}',
        'df {R}',
        'dirname {R}',
        'du -sh .',
        'du -sh {R}',
        'readlink {R}',
        'realpath {R}',
        'tree {R}',
    ],
    'R-418 runner or installer pointed at the project folder': [
        'make -C {R} clean',
        'npm --prefix {R} install',
    ],
    'R-355 Budgit bookkeeping': [
        '{B} --project={R} ledger done R-1 --evidence data.json',   # cwd outside the root: data.json is not the protected file
        '{B} --project {R} budget',
        '{B} --project {R} denies --since 7d',
        '{B} --project {R} ledger ack I-1 --note data.json',
        '{B} --project {R} ledger ack I-22 --note x',
        '{B} --project {R} ledger add --text hi',
        '{B} --project {R} ledger done R-1 --evidence docs/a.md',
        '{B} --project {R} status',
        '{B} --project {R} status --line',
        '{B} ledger ack --project {R} I-1',
    ],
    'R-493 naming the root only as a folder to enter (cd/pushd), then a read': [
        'cd /tmp && rm data.json',
        'cd {R} && git log --oneline',
        'cd {R} && git status',
        'cd {R} && ls',
        'cd {R}/data && ls',
    ],
    'R-493 git calls that change refs, the index or stash only, or read with a pattern that cannot match protected data': [
        'git -C . reset --soft HEAD~1',
        'git -C {R} --git-dir=.git status',
        'git -C {R} --work-tree=. status',
        "git -C {R} branch -a --list 'f*'",
        'git -C {R} log --output=x',
        'git -C {R} ls-files --exclude-from=x',
        "git -C {R} ls-files -x '*.md'",
        'git -C {R} reflog expire --expire=now --all',
        'git -C {R} reset --soft HEAD~1',
        'git -C {R} stash drop',
        'git -C {R}/docs reset HEAD data.json',
    ],
    'R-558 a folder copied into the project whose files are all harmless new names (the files are listed, GNU names checked)': [
        'cp -R {T}/harmless/ {R}',
        'cp -R {T}/harmless/. {R}',
        'cp -R {T}/srcs/data/. {R}',
        'cp -a {T}/harmless/ {R}',
    ],
    'R-493 reads and tests that name a file (no in-place write)': [
        '[ -f data.json ]',
        "node -e 'x' data.json",
        'sed s/a/b/ data.json',
        'tar cf a.tar data',
        'test -f data.json',
    ],
}
ALLOWED = {k for v in ALLOWED_BY_FAMILY.values() for k in v}
# R-493: keyed by (command, cwd class). These loosen ONLY with a cwd outside the project (the relative name is not
# the protected file there); a loosening with cwd = project is never covered by them.
ALLOWED_OUTSIDE_ONLY_BY_FAMILY = {
    'R-493 a relative name resolved from a cwd outside the project (data.json there is not the protected file)': [
        '(ls; rm data.json)',
        '(rm data.json)',
        'PATH=/tmp:$PATH rm data.json',
        'X=1 rm data.json',
        'chmod 600 data.json',
        'cp data.json x.json',
        'cp x.json data.json',
        'ditto {T}/harmless {R}',
        'dd if=x of=data.json',
        'echo hi &> data.json',
        'echo hi 1>| data.json',
        'echo hi 2> data.json',
        'echo hi > data.json',
        'echo hi >> data.json',
        'echo hi >| data.json',
        'echo x && rm data.json',
        'echo x | tee data.json',
        'echo x || rm data.json',
        'echo x; rm data.json',
        'env rm data.json',
        'git -C {R} log &> data.json',
        'git -C {R} log --oneline 2> data.json',
        'git -C {R} log --oneline > data.json',
        'git -C {R} log --oneline >| data.json',
        'git -C {R} log --oneline | tee data.json',
        'git -C {R} status; cp x data.json',
        'git -C {R} status; rm data.json',
        'git diff --output=data.json',
        'git log &> data.json',
        'git log --oneline 2> data.json',
        'git log --oneline > data.json',
        'git log --oneline >| data.json',
        'git log --oneline | tee data.json',
        'git log --outp=data.json',
        'git log --output=data.json --oneline',
        'git status; cp x data.json',
        'git status; rm data.json',
        'install x data.json',
        'ln -s x data.json',
        'ln -sf x data.json',
        'ls data.json; rm data.json',
        'mv data.json x.json',
        'mv x data.json',
        "perl -pi -e 's/a/b/' data.json",
        'python3 -c "open(\'data.json\',\'w\').write(\'x\')"',
        'rm data.json',
        'rm data.json &',
        'sed -i s/a/b/ data.json',
        "sh -c 'rm data.json'",
        'sudo rm data.json',
        'tee data.json',
        'time rm data.json',
        'touch data.json',
        'truncate -s 0 data.json',
        '{ rm data.json; }',
    ],
}
ALLOWED_OUTSIDE = {k for v in ALLOWED_OUTSIDE_ONLY_BY_FAMILY.values() for k in v}
# R-1294 (the submodule rule): a work-tree status or diff in a protected repo asks unless it carries --ignore-submodules=all. These
# entries of the lists above passed before that rule and ask now, so they no longer loosen anything. The reviewed list below is
# exactly that set: the test fails if another entry goes stale, or if one of these starts to pass again.
SUBMODULE_RULE_ASKS = {
    'cd {R} && git status', 'git -C . diff', 'git -C . diff --stat', 'git -C . diff --stat HEAD', 'git -C . status',
    'git -C {R} --git-dir=.git status', 'git -C {R} --work-tree=. status', 'git -C {R} diff', 'git -C {R} diff -- data.json',
    'git -C {R} diff --name-only', 'git -C {R} diff --stat', 'git -C {R} diff --stat HEAD', 'git -C {R} diff HEAD~1',
    'git -C {R} log --oneline && git status || echo no', 'git -C {R} status', 'git -C {R} status && git add data.json',
    'git -C {R} status --ignored', 'git -C {R} status --porcelain', 'git -C {R} status --untracked-files=no',
    'git -C {R} status -s', 'git -C {R} status -sb', 'git -C {R} status | wc -l', 'git -C {R} status; cp x data.json',
    'git -C {R} status; echo done', 'git -C {R} status; rm data.json', 'git diff -- data.json', 'git diff --output=data.json',
    'git status && git add data.json',
}


class DifferentialGuardTest(GuardCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.base = load_base()
        if self.base is None:
            self.skipTest("commit %s is not in this checkout" % BASE_COMMIT)
        git(self.project, "branch", "-q", "feat")
        self.drop = os.path.join(self.tmp, "dropsrc")          # R-558: a folder outside the project that holds a protected name
        os.makedirs(self.drop)
        open(os.path.join(self.drop, "data.json"), "w").write("x\n")
        mk = lambda *parts: (os.makedirs(os.path.dirname(os.path.join(self.tmp, *parts)), exist_ok=True),
                             open(os.path.join(self.tmp, *parts), "w").write("x\n"))
        mk("harmless", "notes.txt")                          # R-558 round 2: sources of every kind
        mk("srcs", "data", "notes.txt")
        mk("stage", "notes.txt")
        mk("linked", "notes.txt")
        mk("linktarget", "real", "x.csv")
        os.symlink(os.path.join(self.tmp, "linktarget"), os.path.join(self.tmp, "linked", "data"))
        name = os.path.basename(self.root)
        mk("bak", name, "data.json")
        self.extra = {"H": os.path.join(self.tmp, "harmless"), "N": os.path.join(self.tmp, "srcs", "data"),
                      "S": os.path.join(self.tmp, "stage"), "L": os.path.join(self.tmp, "linked"),
                      "P": os.path.dirname(self.root), "PB": os.path.join(self.tmp, "bak"), "NAME": name}
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.path.dirname(CLI) + os.pathsep + self._path
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)

    def verdict(self, mod, cmd, cwd):
        got = mod.real_data_decision("Bash", {"command": cmd}, self.project, cwd=cwd)
        return got[0] if got else None

    def test_the_new_guard_never_passes_what_the_base_asked_outside_the_reviewed_list(self):
        cmds = corpus(self.root, CLI, self.drop, self.extra)
        self.assertGreaterEqual(len(cmds), 150)
        loosened, offenders = {}, []
        for grant in (False, True):
            if grant:
                self.assertTrue(self.grant())
            for cwd in (self.project, self.tmp):
                for cmd in cmds:
                    old, new = self.verdict(self.base, cmd, cwd), self.verdict(sl, cmd, cwd)
                    if old and not new:
                        key = cmd.replace(self.root, "{R}").replace(CLI, "{B}").replace(os.path.realpath(self.tmp), "{T}").replace(self.tmp, "{T}")
                        inside = cwd == self.project
                        loosened.setdefault(key, set()).add((grant, inside))
                        if key not in ALLOWED and not (not inside and key in ALLOWED_OUTSIDE):
                            offenders.append((cmd, grant, cwd))
        if os.environ.get("DIFF_DUMP"):
            print("\nCORPUS %d, LOOSENED %d" % (len(cmds), len(loosened)))
            for key in sorted(loosened):
                print("  ALLOW", repr(key), sorted(loosened[key]))
        self.assertEqual(offenders, [], "passes now, asked in the base, not in the reviewed list")
        self.assertEqual(sorted((ALLOWED | ALLOWED_OUTSIDE) - set(loosened)), sorted(SUBMODULE_RULE_ASKS),
                         "an ALLOWED entry that no longer loosens anything, other than the reviewed submodule-rule asks")

    def test_the_entries_the_submodule_rule_took_away_still_ask_and_name_the_rule(self):
        for key in sorted(SUBMODULE_RULE_ASKS):
            cmd = key.replace("{R}", self.root)
            got = sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=self.project)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
            # a line that also touches protected data, or a `cd` first (the guard cannot follow it and asks in general), may ask for
            # that reason first; it is still an ask
            if not any(part in key for part in ("data.json", "cd ", "--git-dir", "--work-tree", ";", "&&", "||", "|")):
                self.assertIn("ignore-submodules=all", got[1], cmd)

    def test_the_corpus_is_big_and_covers_every_family(self):
        cmds = corpus(self.root, CLI, self.drop, self.extra)
        self.assertGreaterEqual(len(cmds), 150)
        for needle in ("reset --hard", "checkout -f", "stash", "merge", "rebase", "pull", "apply", "revert", "push",
                       "config", "clean", "rm ", "mv ", "restore", "switch", "worktree", "submodule", "gc", "tar ",
                       "unzip", "cp -t", "--target-directory", "rsync", "ln ", "install", "dd ", ">|", "&>", "2>", "<<EOF",
                       "cd ~", "cd -", "pushd", "$(", "`", "<("):
            self.assertTrue(any(needle in c for c in cmds), needle)


if __name__ == "__main__":
    unittest.main()
