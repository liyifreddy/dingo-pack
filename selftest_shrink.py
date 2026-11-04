#!/usr/bin/env python3
"""开-013 R2: dingo 0.5 shrink. Moving commands still work and say where they go; bulk close and
opening terminal tabs say they are no longer maintained; the core stays quiet.

Private DINGO_HOME / DINGO_CLAUDE_HOME / DINGO_CONFIG. close-all, stow, boxes and opening tabs are
never run for real here: their notes are checked in-process (boxes would ssh, close-all closes).
  python3 selftest_shrink.py [path/to/bin/dingo-pack]
"""
import contextlib, importlib.machinery, importlib.util, io, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "bin", "dingo-pack"))
BASE = os.environ.get("DINGO_TEST_BASE") or tempfile.mkdtemp(prefix="dingo-selftest-")
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"  [{str(detail)[:500]}]", flush=True)


ch = os.path.join(BASE, "claude")
os.makedirs(os.path.join(ch, "sessions"), exist_ok=True)
cfg = os.path.join(BASE, "config.json")
open(cfg, "w").write("{}")
claims = os.path.join(BASE, "CLAIM_box1.txt")
open(claims, "w").write("")
env = dict(os.environ, DINGO_HOME=os.path.join(BASE, "dh"), DINGO_CLAUDE_HOME=ch, DINGO_CONFIG=cfg)
for k in ("DINGO_NO_MOVE_NOTE", "DINGO_SESSION", "CLAUDE_PID"):
    env.pop(k, None)


def run(*a, extra=None):
    return subprocess.run([sys.executable, TOOL, *a], env=dict(env, **(extra or {})), capture_output=True, text=True,
                          timeout=60, cwd=BASE)


moving = {("board", "--json"): "shepherd-dispatch", ("name", "Q", "x"): "shepherd-dispatch",
          ("lint",): "shepherd-dispatch", ("queue", "list"): "shepherd-dispatch",
          ("claim", "list", "--file", claims): "boobook-watch"}
for argv, home in moving.items():
    r = run(*argv)
    note = [l for l in r.stderr.splitlines() if "is moving to" in l]
    check(f"M1 `{argv[0]}` still works and says it is moving to {home}",
          len(note) == 1 and home in note[0] and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:]))
r = run("board", "--json")
check("M2 the note goes to stderr only: `board --json` stdout is still JSON", r.stdout.strip().startswith("["), r.stdout[:200])
for argv in (("who",), ("list",), ("save",), ("doctor", "--json", "--no-git"), ("resume", "--via", "print", "--dry-run", "nobody")):
    r = run(*argv)
    check(f"M3 core `{' '.join(argv)}` prints no move / unmaintained note",
          "is moving to" not in r.stderr and "no longer maintained" not in r.stderr, r.stderr[-300:])
r = run("lint", extra={"DINGO_NO_MOVE_NOTE": "1"})
check("M4 DINGO_NO_MOVE_NOTE=1 silences the note", "is moving to" not in r.stderr, r.stderr[-200:])
h = run("--help").stdout
check("M5 --help: core listed first, moving and no-longer-maintained named",
      "core:" in h and "shepherd-dispatch" in h and "boobook-watch" in h and "no longer maintained" in h, h[-700:])
sub = {"board": "moving to shepherd-dispatch", "claim": "moving to boobook-watch", "boxes": "moving to boobook-watch",
       "close-all": "no longer maintained", "stow": "no longer maintained", "resume": "no longer maintained"}
bad = [c for c, w in sub.items() if w not in run(c, "--help").stdout.replace("\n", " ")]
check("M6 each subcommand's help says it (board, claim, boxes, close-all, stow, resume)", not bad, bad)

# in-process: commands that must not really run here
loader = importlib.machinery.SourceFileLoader("dingo_shrink", TOOL)
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
os.environ.pop("DINGO_NO_MOVE_NOTE", None)


def said(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            fn(*a)
        except Exception as e:                                 # noqa: BLE001
            return f"raised {e!r}"
    return buf.getvalue()


note = getattr(mod, "moved_note", None)
tabs = getattr(mod, "tabs_note", None)
check("M7 close-all / stow: 'no longer maintained', the roster named as what stays",
      bool(note) and all("no longer maintained" in said(note, c) and "resume --via print" in said(note, c)
                         for c in ("close-all", "stow")), note and said(note, "close-all"))
check("M8 boxes / card: moving notes", bool(note) and "boobook-watch" in said(note, "boxes")
      and "shepherd-dispatch" in said(note, "card"), note and said(note, "boxes"))
check("M9 opening tabs (--via wt / tmux) says no longer maintained; --via print says nothing",
      bool(tabs) and "no longer maintained" in said(tabs, "wt") and "no longer maintained" in said(tabs, "tmux")
      and said(tabs, "print") == "", tabs and said(tabs, "print"))
print(f"{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
