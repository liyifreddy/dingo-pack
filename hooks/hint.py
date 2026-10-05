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
import shlex
import shutil
import subprocess
import sys

HEAVY_SKILLS = {"code-review", "security-review", "simplify"}
PY = re.compile(r"^(python[0-9.]*|uv|uvx|pipx|poetry|conda)$")
HEAVY_WORDS = {"pytest", "tox", "nox", "bazel", "gradle", "mvn"}
HEAVY_SUB = {("cargo", "build"), ("cargo", "test"), ("npm", "test"), ("npm", "run"), ("make", None),
             ("go", "test"), ("go", "build")}
HEAVY_SCRIPT = re.compile(r"(^|/)(train\w*|selftest\w*|test_\w+|bench\w*)\.py$")
HEAVY_INLINE = re.compile(r"from_pretrained|import torch|torch\.load|import tensorflow|import jax")
OPS = {"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def dingo_cmd():
    """How the agent should call dingo-pack: by name if it is on PATH, else via the plugin."""
    if shutil.which("dingo-pack"):
        return ["dingo-pack"]
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and os.path.exists(os.path.join(root, "bin", "dingo-pack")):
        return ["python3", os.path.join(root, "bin", "dingo-pack")]
    return ["dingo-pack"]


def segments(cmd):
    """Split a shell command into simple commands (lists of words), honouring quotes."""
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
    d = " ".join(shlex.quote(x) for x in dingo_cmd())
    lease = f"{d} lease --mem <est> --for <time> --note '<what>'  (then {d} release)"
    if tool == "Agent":
        what, how = f"a sub-agent ({ti.get('subagent_type') or 'general'})", lease
    elif tool == "Skill" and str(ti.get("skill", "")).split(":")[-1] in HEAVY_SKILLS:
        what, how = f"the {ti.get('skill')} skill", lease
    elif tool == "Bash":
        cmd = str(ti.get("command", ""))
        if not any(segment_heavy(w) for w in segments(cmd)):
            return None
        what, how = "a heavy-looking shell command", f"{d} run --heavy --mem <peak> -- <command>"
    else:
        return None
    msg = f"dingo-pack: you are starting {what}. On this shared machine heavy work takes a slot: {how}."
    try:
        who = subprocess.run(dingo_cmd() + ["who"], capture_output=True, text=True, timeout=2).stdout
        lines = who.splitlines()
        if lines:
            msg += f" Now: {lines[0]}"
            names = [ln.split()[1] for ln in lines[1:] if ln.lstrip().startswith("[") and len(ln.split()) > 1]
            if names:
                msg += " Held by: " + ", ".join(names) + "."
    except (OSError, subprocess.SubprocessError):
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
