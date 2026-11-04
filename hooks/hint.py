#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Optional PreToolUse hook: when an agent is about to start something heavy without
dingo-pack, add a one-line hint for the model. Never blocks, never fails.

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
HEAVY_WORDS = {"pytest", "tox", "nox", "bazel", "gradle", "mvn"}
HEAVY_SUB = {("cargo", "build"), ("cargo", "test"), ("npm", "test"), ("npm", "run"), ("make", None),
             ("go", "test"), ("go", "build")}
HEAVY_SCRIPT = re.compile(r"(^|/)(train\w*|selftest\w*|test_\w+|bench\w*)\.py$")
HEAVY_INLINE = re.compile(r"from_pretrained|import torch|torch\.load|import tensorflow|import jax")
IO_WORDS = {"tar", "cp", "rsync", "du", "find", "zip", "unzip", "7z", "gzip", "zstd", "dd", "mv"}
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


def segment_io(w, mounts):
    """A bulk copy / archive / scan whose arguments touch a network or 9p mount."""
    if not w:
        return False
    cmd = os.path.basename(w[0])
    is_io = cmd in IO_WORDS or (cmd == "git" and w[1:2] == ["archive"])
    if not is_io:
        return False
    cwd = os.getcwd().rstrip("/") + "/"
    on_net = lambda a: any((a if a.startswith("/") else cwd + a).startswith(m) for m in mounts)
    return any(on_net(a) for a in w[1:] if not a.startswith("-"))
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


def segments(cmd):
    """Split a shell command into simple commands (lists of words), honouring quotes."""
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
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def segment_heavy(w):
    while w and (ENV_ASSIGN.match(w[0]) or w[0] in ("sudo", "nice", "time", "env", "exec", "prlimit")):
        w = w[1:]
        while w and w[0].startswith("-"):         # options of nice / prlimit / env
            w = w[1:]
    if not w:
        return False
    cmd = os.path.basename(w[0])
    if cmd == "dingo-pack" or (len(w) > 1 and cmd.startswith("python") and w[1].endswith("dingo-pack")):
        return False
    if cmd in HEAVY_WORDS:
        return True
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
        return any(HEAVY_SCRIPT.search(a) for a in rest if not a.startswith("-"))
    return False


def hint(data):
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
        segs = segments(cmd)
        if any(segment_heavy(w) for w in segs):
            kind, what = "heavy", "a heavy-looking shell command"
        elif any(segment_io(w, net_mounts()) for w in segs if not (w and os.path.basename(w[0]) == "dingo-pack")):
            kind, what = "io", "a big read / write on a network or 9p disk"
    if kind is None:
        return None
    import shlex
    exe = dingo_cmd()
    d = " ".join(shlex.quote(x) for x in exe)
    how = {"lease": f"{d} lease --mem <est> --for <time> --note '<what>'  (then {d} release)",
           "heavy": f"{d} run --heavy --mem <peak> -- <command>",
           "io": f"{d} run --io -- <command>"}[kind]
    msg = f"dingo-pack: you are starting {what}. On this shared machine it takes a slot: {how}."
    try:
        info = status(exe)
        L = info["lanes"]
        gb = 1024 ** 3
        free = (info["avail"] - info["promised"]) / gb if info.get("avail") is not None else None
        g = info.get("ghosts") or {}
        cpu = info.get("cpu") or {}
        msg += (f" Now: heavy slots {L['heavy']['held']}/{L['heavy']['n']}, io {L['io']['held']}/{L['io']['n']}"
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
        if msg:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:   # noqa: BLE001 - a hint hook must never get in the way
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
