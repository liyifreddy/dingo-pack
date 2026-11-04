#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PreToolUse hook: when an agent is about to start something heavy without dingo-pack,
add a one-line hint for the model -- or, with enforcement on, refuse the call and say how
to re-run it through dingo-pack. Never fails: any error in the hook lets the call through.

Enforcement (off by default): DINGO_ENFORCE=1, or "hook": {"enforce": true} in
~/.config/dingo-pack/config.json. It refuses heavy shell commands and bulk reads / writes on
a network or 9p disk; sub-agents and review skills only get the hint (they cannot be wrapped).
Rules can be extended in the same "hook" section: heavy_words, heavy_scripts (regex),
light_scripts (regex, never heavy), enforce_io (default true).

Heavy = a sub-agent (Agent tool), a review skill, or a shell command whose *command word*
looks like a test run / build / training or model-loading script. Each segment of a
compound command (`;`, `&&`, `||`, `|`) is judged on its own; a segment that starts with
`dingo-pack` is already wrapped.
"""
import json
import os
import re
import sys
import time
# shlex and subprocess are imported only when a command has to be parsed / a hint is given:
# this hook runs before every tool call, so its start-up time matters.

HEAVY_SKILLS = {"code-review", "security-review", "simplify"}
PY = re.compile(r"^(python[0-9.]*|uv|uvx|pipx|poetry|conda)$")
HEAVY_WORDS = {"pytest", "tox", "nox", "bazel", "gradle", "mvn", "ninja", "colcon", "catkin_make",
               "mineru", "magic-pdf"}
HEAVY_SUB = {("cargo", "build"), ("cargo", "test"), ("npm", "test"), ("npm", "run"), ("make", None),
             ("go", "test"), ("go", "build"), ("cmake", "--build"), ("docker", "build")}
HEAVY_SCRIPT = re.compile(r"(^|/)(train\w*|selftest\w*|test_\w+|bench\w*|run_all\w*|regress\w*|eval\w*"
                          r"|infer\w*|predict\w*|export\w*|preprocess\w*|convert\w*|build_\w+|mineru\w*)\.(py|sh)$")
SHELLS = {"bash", "sh", "zsh", "dash"}
PREFIX = {"sudo": 0, "nice": 0, "time": 0, "env": 0, "exec": 0, "prlimit": 0, "nohup": 0, "stdbuf": 0,
          "setsid": 0, "ionice": 0, "command": 0, "timeout": 1, "taskset": 1, "chrt": 1}   # word -> positional args it takes
RUNNERS = {"uv", "poetry", "pipenv", "conda", "mamba", "micromamba", "pixi", "hatch"}   # `X run [opts] CMD...`
HEAVY_INLINE = re.compile(r"from_pretrained|import torch|torch\.load|import tensorflow|import jax")
IO_WORDS = {"tar", "cp", "rsync", "du", "find", "zip", "unzip", "7z", "gzip", "zstd", "dd", "mv", "grep", "rg"}
BULK_ALWAYS = {"tar", "rsync", "du", "zip", "unzip", "7z", "gzip", "zstd", "dd"}
SEARCHERS = {"grep", "egrep", "fgrep", "rg"}     # judged by what they actually scan (scan_is_big)
# options of grep / rg that take a value as the next word (so that word is not a path or the pattern)
RG_VALUE = {"-A", "-B", "-C", "-d", "-E", "-e", "-f", "-g", "-j", "-M", "-m", "-r", "-t", "-T",
            "--after-context", "--before-context", "--context", "--max-depth", "--maxdepth", "--encoding", "--regexp",
            "--file", "--glob", "--iglob", "--threads", "--max-columns", "--max-count", "--replace", "--type",
            "--type-not", "--type-add", "--type-clear", "--pre", "--pre-glob", "--sort", "--sortr", "--color",
            "--colors", "--path-separator", "--ignore-file", "--engine", "--max-filesize", "--context-separator",
            "--field-match-separator", "--field-context-separator", "--dfa-size-limit", "--regex-size-limit"}
GREP_VALUE = {"-A", "-B", "-C", "-e", "-f", "-m", "-d", "-D", "--include", "--exclude", "--exclude-dir",
              "--regexp", "--file", "--max-count", "--context", "--after-context", "--before-context",
              "--label", "--color", "--colour", "--binary-files", "--devices", "--directories"}
SMALL_SCAN = 500          # a directory with at most this many entries (all levels) is a small scan
RECURSIVE = {"-r", "-R", "-a", "--recursive", "--archive", "-rf", "-fr", "-rp", "-ar", "-av", "-rv"}
NET_FS = ("9p", "drvfs", "nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "v9fs", "virtiofs")


def net_mounts():
    out = []
    try:
        with open(os.environ.get("DINGO_FAKE_MOUNTS", "/proc/mounts")) as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and p[2] in NET_FS and p[1] != "/":
                    out.append(p[1].rstrip("/") + "/")
    except OSError:
        pass
    return out


def here():
    """The working directory, or "/" if it was deleted under us (os.getcwd() raises then)."""
    try:
        return os.getcwd()
    except OSError:
        return "/"


def abspath(a, cwd):
    a = os.path.expanduser(a)
    return os.path.normpath(a if a.startswith("/") else os.path.join(cwd, a))


def search_targets(w):
    """(recursive?, paths) of a grep / rg command; rg searches recursively by default, grep only with -r.
    No path given = the current directory (rg, grep -r)."""
    cmd = os.path.basename(w[0])
    rg = cmd == "rg"
    takes = RG_VALUE if rg else GREP_VALUE
    rec, pattern_given, paths, files_mode, depth = rg, False, [], False, None
    args, i = w[1:], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            paths += args[i + 1:]
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in ("--recursive", "--dereference-recursive"):
                rec = True
            if name == "--files":
                files_mode = True
            if name in ("--regexp", "--file"):
                pattern_given = True
            if name in ("--max-depth", "--maxdepth"):
                depth = val if eq else (args[i + 1] if i + 1 < len(args) else None)
            if name in takes and not eq:
                i += 1
        elif a.startswith("-") and len(a) > 1:
            letters = a[1:]
            for j, ch in enumerate(letters):
                if not rg and ch in "rR":
                    rec = True
                if ch in "ef":
                    pattern_given = True
                if ("-" + ch) in takes:
                    if ch == "d" and rg:
                        depth = letters[j + 1:] or (args[i + 1] if i + 1 < len(args) else None)
                    if j == len(letters) - 1:
                        i += 1                        # its value is the next word
                    break                             # else the rest of the cluster is the value
        elif not pattern_given and not files_mode:
            pattern_given = True                      # the first plain word is the pattern
        else:
            paths.append(a)
        i += 1
    if depth is not None and str(depth).isdigit() and int(depth) <= 1:
        rec = False                                   # rg --max-depth 1: one directory level, not a tree
    return rec, paths or ["."]


def scan_is_big(path, limit):
    """False for a file, a missing path or a directory tree with at most `limit` entries; True as soon as
    more are seen (or listing takes too long - on a slow 9p disk that alone says it is big)."""
    if not os.path.isdir(path):
        return False
    n, stack, t0 = 0, [path], time.time()
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    n += 1
                    if n > limit or time.time() - t0 > 0.8:
                        return True
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                    except OSError:
                        pass
        except OSError:
            pass
    return False


def segment_io(w, mounts, cwd=None):
    """A bulk copy / archive / scan whose arguments touch a network or 9p mount."""
    if not w:
        return False
    cmd = os.path.basename(w[0])
    is_io = cmd in IO_WORDS or cmd in SEARCHERS or (cmd == "git" and w[1:2] == ["archive"])
    if not is_io:
        return False
    cwd = cwd or here()
    on_net = lambda a: any((abspath(a, cwd).rstrip("/") + "/").startswith(m) for m in mounts)
    if cmd in SEARCHERS:
        rec, paths = search_targets(w)
        if not rec:
            return False                              # grep without -r / rg --max-depth 1: files named on the line
        limit = RULES.get("small_scan", SMALL_SCAN)
        return any(on_net(a) and scan_is_big(abspath(a, cwd), limit) for a in paths)
    return any(on_net(a) for a in w[1:] if not a.startswith("-"))


def segment_bulk(w):
    """An io segment that moves or scans a lot (refused under enforcement); a single-file cp / mv only gets the hint."""
    cmd = os.path.basename(w[0])
    if cmd in BULK_ALWAYS or cmd in SEARCHERS or cmd == "git":
        return True                                   # grep / rg reach here only for a big tree (segment_io)
    if cmd in ("cp", "mv"):
        return any(a in RECURSIVE or (cmd != "mv" and a.startswith("-") and not a.startswith("--") and "r" in a.lower())
                   for a in w[1:])
    if cmd == "find":
        return "-maxdepth" not in w
    return False


def user_config():
    path = os.environ.get("DINGO_CONFIG") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "dingo-pack", "config.json")
    try:
        with open(path) as f:
            c = json.load(f)
        return c.get("hook") or {} if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


LAST_DENY = False
RULES = {"enforce": False, "enforce_io": True, "words": HEAVY_WORDS, "extra": None, "light": None,
         "small_scan": SMALL_SCAN}


def rules():
    c = user_config()
    env = os.environ.get("DINGO_ENFORCE")
    enforce = env not in ("", "0", "false", "no") if env is not None else bool(c.get("enforce"))
    words = HEAVY_WORDS | set(c.get("heavy_words") or [])
    try:
        extra = re.compile(c["heavy_scripts"]) if c.get("heavy_scripts") else None
        light = re.compile(c["light_scripts"]) if c.get("light_scripts") else None
    except (re.error, TypeError):
        extra = light = None
    try:
        small = int(c.get("small_scan", SMALL_SCAN))
    except (TypeError, ValueError):
        small = SMALL_SCAN
    return {"enforce": enforce, "enforce_io": c.get("enforce_io", True), "words": words,
            "extra": extra, "light": light, "small_scan": small}
OPS = {"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def on_path(name):
    """shutil.which, minus the Windows directories WSL appends to PATH (slow 9p lookups)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and not d.startswith("/mnt/") and os.access(os.path.join(d, name), os.X_OK):
            return True
    return False


def dingo_cmd():
    """How the agent should call dingo-pack: by name if it is on PATH, else via the plugin."""
    if on_path("dingo-pack"):
        return ["dingo-pack"]
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and os.path.exists(os.path.join(root, "bin", "dingo-pack")):
        return ["python3", os.path.join(root, "bin", "dingo-pack")]
    return ["dingo-pack"]


def state_home():
    d = os.environ.get("DINGO_HOME")
    if d:
        return d
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "dingo-pack")


def status(cmd):
    """`dingo-pack who --json`, cached for a few seconds so most hook calls skip the subprocess."""
    cache = os.path.join(state_home(), "status-cache.json")
    try:
        if time.time() - os.path.getmtime(cache) < float(os.environ.get("DINGO_HINT_CACHE_S", "10")):
            with open(cache) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    import subprocess
    r = subprocess.run(cmd + ["who", "--json"], capture_output=True, text=True, timeout=2)
    info = json.loads(r.stdout)
    try:
        tmp = cache + ".%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(info, f)
        os.replace(tmp, cache)
    except OSError:
        pass
    return info


def segments(cmd, with_ops=False):
    """Split a shell command into simple commands (lists of words), honouring quotes. with_ops: pairs
    (words, operator that ends them)."""
    import shlex
    try:
        lx = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        toks = cmd.split()
    out, cur = [], []
    for t in toks:
        if t in OPS:
            if cur or with_ops:
                out.append((cur, t) if with_ops else cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append((cur, None) if with_ops else cur)
    return out


def segment_heavy(w):
    r = RULES
    w = strip_prefix(w)
    if not w:
        return False
    cmd = os.path.basename(w[0])
    if is_wrapped(w):
        return False
    script_args = [a for a in w[1:] if not a.startswith("-")] if (PY.match(cmd) or cmd in SHELLS) else []
    if r["light"] and any(r["light"].search(a) for a in [w[0]] + script_args):
        return False
    if cmd in r["words"]:
        return True
    if HEAVY_SCRIPT.search(w[0]) or (r["extra"] and r["extra"].search(w[0])):
        return True                                   # ./run_all.sh, scripts/regress.py
    if cmd in SHELLS:
        return any(HEAVY_SCRIPT.search(a) or (r["extra"] and r["extra"].search(a)) for a in script_args[:1])
    if (cmd, w[1] if len(w) > 1 else None) in HEAVY_SUB or (cmd, None) in HEAVY_SUB:
        return True
    if PY.match(cmd):
        rest = w[1:]
        if "-c" in rest:
            i = rest.index("-c")
            return bool(HEAVY_INLINE.search(" ".join(rest[i + 1:i + 2])))
        if "-m" in rest:
            i = rest.index("-m")
            return rest[i + 1:i + 2] in (["pytest"], ["unittest"], ["torch.distributed.run"])
        return any(HEAVY_SCRIPT.search(a) or (r["extra"] and r["extra"].search(a)) for a in rest if not a.startswith("-"))
    return False


def strip_prefix(w):
    """Drop env assignments and wrappers (nohup, timeout 600, nice -n 5, ...) in front of the real command."""
    while w and (ENV_ASSIGN.match(w[0]) or os.path.basename(w[0]) in PREFIX):
        n = 0 if ENV_ASSIGN.match(w[0]) else PREFIX[os.path.basename(w[0])]
        w = w[1:]
        while w and w[0].startswith("-"):         # options of nice / prlimit / env / timeout
            w = w[2:] if w[0] in ("-n", "-s", "-k", "-c", "-u", "-p") and len(w) > 1 and not w[1].startswith("-") else w[1:]
        w = w[n:]
    return w


def is_wrapped(w):
    cmd = os.path.basename(w[0]) if w else ""
    return cmd == "dingo-pack" or (len(w) > 1 and cmd.startswith("python") and w[1].endswith("dingo-pack"))


def shell_command(w):
    """The command string of `bash -c '...'`, also with the c among other options (`bash -lc`, `sh -ec`,
    `bash -l -c`, `bash -o pipefail -c`, `bash --login -c`); None if this shell runs no -c string."""
    has_c, i = False, 1
    while i < len(w):
        a = w[i]
        if a in ("--", "-"):
            i += 1
            break
        if a.startswith("--"):
            if a in ("--rcfile", "--init-file"):
                i += 1
        elif a[:1] in ("-", "+") and len(a) > 1:
            letters = a[1:]
            if "c" in letters:
                has_c = True
            if "o" in letters or "O" in letters:
                i += 1                                # -o pipefail: the option name is the next word
        else:
            break
        i += 1
    return w[i] if has_c and i < len(w) else None


def unwrap(w):
    """`bash -c '...'` -> its segments; `uv run X` / `conda run -n e X` -> [X ...]; else [w]."""
    w = strip_prefix(w)
    if not w:
        return []
    cmd = os.path.basename(w[0])
    if cmd in SHELLS:
        inner = shell_command(w)
        if inner is not None:
            return [s for seg in segments(inner) for s in unwrap(seg)]
    if cmd in RUNNERS and len(w) > 1 and w[1] in ("run", "exec"):
        rest = w[2:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-n", "-p", "--name", "--prefix", "-e", "--env", "--with") and len(rest) > 1 else rest[1:]
        if rest:
            return unwrap(rest)
    return [w]


def flatten(cmd, cwd, depth=0):
    """Simple commands of `cmd` (wrappers opened up) and, for each, the directory it runs in: a
    `cd DIR` moves the following commands of the same list; a subshell `( ... )` does not leak its cd."""
    segs, cwds, stack, here = [], [], [], cwd
    for w, op in segments(cmd, with_ops=True):
        if w and w[0] == "(":
            w = w[1:]
        s = strip_prefix(w)
        if s and s[0] in ("cd", "pushd"):
            dest = [a for a in s[1:] if not a.startswith("-")]
            here = abspath(dest[0], here) if dest else os.path.expanduser("~")
        elif s and not is_wrapped(s) and os.path.basename(s[0]) in SHELLS and shell_command(s) is not None \
                and depth < 4:
            inner, dirs = flatten(shell_command(s), here, depth + 1)
            segs += inner
            cwds += dirs
        elif s:
            for u in ([s] if is_wrapped(s) else unwrap(s)):
                segs.append(u)
                cwds.append(here)
        if op == "(":
            stack.append(here)
        elif op == ")" and stack:
            here = stack.pop()
    return segs, cwds


def hint(data):
    """The message for the model, or None. Sets LAST_DENY when enforcement refuses the call."""
    global RULES, LAST_DENY
    LAST_DENY = False
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        return None
    kind = None                              # decide first: most calls need no hint at all
    if tool == "Agent":
        kind, what = "lease", f"a sub-agent ({ti.get('subagent_type') or 'general'})"
    elif tool == "Skill" and str(ti.get("skill", "")).split(":")[-1] in HEAVY_SKILLS:
        kind, what = "lease", f"the {ti.get('skill')} skill"
    elif tool == "Bash":
        cmd = str(ti.get("command", ""))
        RULES = rules()
        segs, cwds = flatten(cmd, here())
        io_segs = []
        if any(segment_heavy(w) for w in segs):
            kind, what = "heavy", "a heavy-looking shell command"
        else:
            mounts = net_mounts()
            io_segs = [w for w, d in zip(segs, cwds) if w and not is_wrapped(w) and segment_io(w, mounts, d)]
            if io_segs:
                kind, what = "io", "a big read / write on a network or 9p disk"
        if kind == "heavy" and RULES["enforce"]:
            LAST_DENY = True
        elif kind == "io" and RULES["enforce"] and RULES["enforce_io"] and any(segment_bulk(w) for w in io_segs):
            LAST_DENY = True
    if kind is None:
        return None
    import shlex
    exe = dingo_cmd()
    d = " ".join(shlex.quote(x) for x in exe)
    how = {"lease": f"{d} lease --mem <est> --for <time> --note '<what>'  (then {d} release)",
           "heavy": f"{d} run --heavy --mem <peak> -- <command>",
           "io": f"{d} run --io -- <command>"}[kind]
    msg = f"dingo-pack: you are starting {what}. On this shared machine it takes a slot: {how}."
    if LAST_DENY:
        whole = shlex.quote(str(ti.get("command", "")))
        msg = (f"dingo-pack: refused - {what} outside dingo-pack. Heavy work on this machine must wait for memory"
               f" and run under a cap, or one job can take the whole machine (and the /mnt drives) down. Re-run it as"
               + (f" `{d} run --heavy --mem <peak, e.g. 4G> -- bash -c {whole}`" if kind == "heavy" else
                  f" `{d} run --io -- bash -c {whole}`, or copy the data to the local disk (~/ or /tmp) first and work there")
               + (". Small job: --mem 1G --threads 2. Reads a lot from /mnt/...: copy it to ~/ first, or use --io."
                  if kind == "heavy" else ".")
               + " If this is wrong, say so in your log; the rules live in ~/.config/dingo-pack/config.json (\"hook\").")
    try:
        info = status(exe)
        L = info["lanes"]
        gb = 1024 ** 3
        free = (info["avail"] - info["promised"]) / gb if info.get("avail") is not None else None
        g = info.get("ghosts") or {}
        cpu = info.get("cpu") or {}
        pinned = (info.get("capacity") or {}).get("slots_pinned", True)
        heavy = (f"heavy slots {L['heavy']['held']}/{L['heavy']['n']}" if pinned else
                 f"heavy jobs {L['heavy']['held']} running (count not fixed: memory and CPU decide)")
        msg += (f" Now: {heavy}, io {L['io']['held']}/{L['io']['n']}"
                + (f", CPU {cpu.get('used')}/{cpu.get('total')}" if cpu else "")
                + (f", {free:.1f} GB free after promised" if free is not None else "")
                + (f", ghosts {g.get('n')} ({(g.get('pss') or 0) / gb:.1f} GB)" if g else "") + ".")
        names = sorted({h.get("session", "?") for h in info.get("holders", [])})
        if names:
            msg += " Held by: " + ", ".join(names) + "."
        if info.get("overloaded"):
            msg += (f" The machine is overloaded (load {info.get('load')} on {info.get('ncpu')} cores):"
                    f" use fewer threads, e.g. `{d} run --heavy --threads 4 -- ...`.")
        k = info.get("suggest_parallel")
        if k is not None:
            msg += (f" Run at most {k} heavy job(s) in parallel right now." if k > 0 else
                    f" No room right now: wait, `{d} run --heavy` queues for you.")
    except Exception:          # noqa: BLE001 - no numbers is fine; never fail the hook
        pass
    return msg


def main():
    try:
        data = json.load(sys.stdin)
        msg = hint(data) if os.environ.get("DINGO_HINT", "1") != "0" else None
        if msg and LAST_DENY:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": msg}}))
        elif msg:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:   # noqa: BLE001 - a hint hook must never get in the way
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
