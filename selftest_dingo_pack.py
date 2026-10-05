#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Self-test for dingo-pack. Standard library only; runs in a scratch dir on a local disk.

  python3 selftest_dingo_pack.py [--base DIR] [--tool PATH]    all checks (~1 min)
  python3 selftest_dingo_pack.py --mutants                     each mutant must turn a check red

Memory checks never touch the machine's real limits: they run against a *simulated* machine
(a supervisor writes a fake /proc/meminfo and plays OOM killer at a small size, e.g. 700 MB).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bin", "dingo-pack")
PY = sys.executable
MB = 1024 * 1024

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)
    return ok


class Env:
    def __init__(self, base, tool):
        self.base = tempfile.mkdtemp(prefix="dp-", dir=base)
        self.tool = tool
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith("DINGO_") or k in ("CLAUDE_PID", "CLAUDE_CODE_SESSION_ID"):
                del self.env[k]
        self.env.update(DINGO_HOME=os.path.join(self.base, "state"),
                        DINGO_CONFIG=os.path.join(self.base, "config.json"),
                        DINGO_CLAUDE_HOME=os.path.join(self.base, "claude"),
                        DINGO_RESUME_GAP_S="0", DINGO_SAMPLE_S="0.1", DINGO_KILL_GRACE="2",
                        DINGO_LEASE_POLL_S="0.2")

    def run(self, *args, env=None, timeout=60, **kw):
        e = dict(self.env, **(env or {}))
        return subprocess.run([PY, self.tool, *args], env=e, capture_output=True, text=True,
                              timeout=timeout, **kw)

    def popen(self, *args, env=None):
        e = dict(self.env, **(env or {}))
        return subprocess.Popen([PY, self.tool, *args], env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def path(self, *p):
        return os.path.join(self.base, *p)

    def log(self):
        try:
            with open(self.path("state", "log.jsonl")) as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []


# ------------------------------------------------------------ slots

JOB = r"""
import os, sys, time
out, tag, dur = sys.argv[1], sys.argv[2], float(sys.argv[3])
with open(out, "a") as f: f.write(f"in {tag} {time.time()}\n")
time.sleep(dur)
with open(out, "a") as f: f.write(f"out {tag} {time.time()}\n")
"""


def max_overlap(path):
    ev = []
    with open(path) as f:
        for line in f:
            kind, tag, t = line.split()
            ev.append((float(t), 1 if kind == "in" else -1))
    ev.sort(key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best, sum(1 for _, d in ev if d == 1)


def t_slots(E):
    job = E.path("job.py")
    with open(job, "w") as f:
        f.write(JOB)
    out = E.path("events.txt")
    ps = [E.popen("run", "--heavy", "--slots", "3", "--mem", "1M", "--limit", "0", "--",
                  PY, job, out, str(k), "0.6") for k in range(8)]
    rcs = [p.wait(60) for p in ps]
    best, n = max_overlap(out)
    check("slots: 8 jobs, N=3 -> at most 3 at once", best <= 3 and n == 8 and rcs == [0] * 8,
          f"max overlap {best}, started {n}, rcs {rcs}")
    check("slots: N=3 actually runs 3 in parallel (not serialised)", best == 3, f"max overlap {best}")


def t_fifo(E):
    job = E.path("job.py")
    with open(job, "w") as f:
        f.write(JOB)
    out = E.path("fifo.txt")
    first = E.popen("run", "--heavy", "--slots", "1", "--mem", "1M", "--limit", "0", "--", PY, job, out, "first", "1.0")
    time.sleep(0.4)
    ws = []
    for tag in ("a", "b", "c", "d"):
        ws.append(E.popen("run", "--heavy", "--slots", "1", "--mem", "1M", "--limit", "0", "--", PY, job, out, tag, "0.1"))
        time.sleep(0.1)
    for p in [first] + ws:
        p.wait(60)
    order = [l.split()[1] for l in open(out) if l.startswith("in ")]
    check("queue: first come first served", order == ["first", "a", "b", "c", "d"], f"order {order}")


def t_kill_releases(E):
    p = E.popen("run", "--heavy", "--slots", "1", "--mem", "1M", "--limit", "0", "--", "sleep", "30")
    time.sleep(0.8)
    r = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "1s", "--", "true")
    check("timeout: no slot within --timeout -> exit 75", r.returncode == 75, f"rc {r.returncode} {r.stderr[-200:]}")
    w = E.run("who", "--slots", "1")
    check("who: shows the holder", "sleep 30" in w.stdout, w.stdout)
    os.kill(p.pid, signal.SIGKILL)       # dingo-pack itself dies; the job still holds the slot
    p.wait()
    time.sleep(0.3)
    r = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "1s", "--", "true")
    check("slot stays taken while the orphaned job still runs", r.returncode == 75, f"rc {r.returncode}")
    h = json.load(open(E.path("state", "slots", "slot-0.holder")))
    os.kill(h["job_pid"], signal.SIGKILL)     # only our own orphan, found via the holder record
    time.sleep(0.3)
    r = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "3s", "--", "true")
    check("slot is free again once the holder processes are gone (no stale lock)", r.returncode == 0,
          f"rc {r.returncode} {r.stderr[-200:]}")


def t_exitcodes(E):
    r = E.run("run", "--heavy", "--mem", "1M", "--limit", "0", "--", "sh", "-c", "exit 7")
    check("run: passes the job's exit code through", r.returncode == 7, f"rc {r.returncode}")
    r = E.run("run", "--mem", "1M", "--", "true")
    check("run: refuses without --heavy", r.returncode == 2)
    r = E.run("run", "--heavy", "--mem", "1M", "--limit", "0", "--", "definitely-not-a-command-xyz")
    check("run: missing command -> 127", r.returncode == 127, f"rc {r.returncode}")
    lg = E.log()
    check("log: one JSON line per started run (exit 7, missing command) with rc / waits / peak",
          len(lg) == 2 and [x["rc"] for x in lg] == [7, 127] and all(
        k in lg[0] for k in ("rc", "slot_wait_s", "mem_wait_s", "peak_tree_rss", "est", "session")), str(lg[:1]))
    r = E.run("run", "--heavy", "--mem", "1M", "--limit", "0", "--", "sh", "-c", "echo x", "API_TOKEN=abcdef")
    lg = E.log()
    check("log: secret-looking arguments are masked", "abcdef" not in json.dumps(lg[-1]), lg[-1].get("cmd"))


# ------------------------------------------------------------ memory (simulated machine)

class Machine:
    """A pretend machine of `size` bytes: writes a fake meminfo (MemAvailable = size - RSS of the
    processes under `roots`) and plays OOM killer (kills the biggest process when over size)."""

    def __init__(self, E, size):
        self.E, self.size = E, size
        self.meminfo = E.path("meminfo")
        self.roots = []
        self.kills = []
        self.peak = 0
        self.stop = False
        self.write(size)
        self.th = threading.Thread(target=self.loop, daemon=True)
        self.th.start()

    def write(self, avail):
        with open(self.meminfo + ".tmp", "w") as f:
            f.write(f"MemTotal: {self.size // 1024} kB\nMemFree: {max(0, avail) // 1024} kB\n"
                    f"MemAvailable: {max(0, avail) // 1024} kB\n")
        os.replace(self.meminfo + ".tmp", self.meminfo)

    def procs(self):
        table = {}
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    st = open(f"/proc/{d}/stat").read()
                    ppid = int(st[st.rindex(")") + 2:].split()[1])
                    rss = int(open(f"/proc/{d}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                    table[int(d)] = (ppid, rss)
                except (OSError, ValueError, IndexError):
                    pass
        kids = {}
        for pid, (pp, _) in table.items():
            kids.setdefault(pp, []).append(pid)
        out, stack = [], list(self.roots)
        while stack:
            p = stack.pop()
            if p in table:
                out.append(p)
            stack.extend(kids.get(p, []))
        # only the memory hogs count (python workers), not the dingo-pack wrappers
        def is_hog(p):
            try:
                argv = open(f"/proc/{p}/cmdline").read().split("\0")
            except OSError:
                return False
            return len(argv) > 1 and argv[1].endswith("hog.py")
        return {p: table[p][1] for p in out if is_hog(p)}

    def loop(self):
        while not self.stop:
            try:
                ps = self.procs()
            except OSError:
                ps = {}
            used = sum(ps.values())
            self.peak = max(self.peak, used)
            if used > self.size and ps:
                victim = max(ps, key=ps.get)
                try:
                    os.kill(victim, signal.SIGKILL)
                    self.kills.append(victim)
                except OSError:
                    pass
            self.write(self.size - used)
            time.sleep(0.02)

    def close(self):
        self.stop = True
        self.th.join()


HOG = r"""
import sys, time
mb, secs = int(sys.argv[1]), float(sys.argv[2])
step = 25
blocks = []
for _ in range(0, mb, step):          # ramp up like a model load
    blocks.append(bytearray(step * 1024 * 1024))
    for i in range(0, len(blocks[-1]), 4096): blocks[-1][i] = 1
    time.sleep(0.03)
time.sleep(secs)
print("done", mb)
"""


def hog_file(E):
    p = E.path("hog.py")
    with open(p, "w") as f:
        f.write(HOG)
    return p


def t_memory_pack(E):
    """4 jobs x 300 MB on a 700 MB machine. Without dingo-pack: the OOM killer hits them.
    With dingo-pack (memory admission): they take turns, nobody is killed."""
    hog = hog_file(E)
    # negative control: all at once, no dingo-pack
    m = Machine(E, 350 * MB)
    ps = [subprocess.Popen([PY, hog, "150", "0.8"], stdout=subprocess.PIPE) for _ in range(4)]
    m.roots = [p.pid for p in ps]
    rcs = [p.wait(60) for p in ps]
    m.close()
    killed = sum(1 for r in rcs if r != 0)
    check("negative control: 4 x 150 MB at once on a 350 MB machine -> OOM kills", killed >= 1,
          f"rcs {rcs}, peak {m.peak // MB} MB")

    m = Machine(E, 350 * MB)
    env = {"DINGO_FAKE_MEMINFO": m.meminfo, "DINGO_RESERVE": "25M", "DINGO_MEM_POLL_S": "0.2"}
    ps = []
    for _ in range(4):
        ps.append(E.popen("run", "--heavy", "--slots", "6", "--mem", "170M", "--limit", "0",
                          "--tree-limit", "0", "--", PY, hog, "150", "0.8", env=env))
        m.roots.append(ps[-1].pid)
        time.sleep(0.05)
    rcs = [p.wait(120) for p in ps]
    m.close()
    check("dingo-pack: same 4 jobs with memory admission -> all finish, no OOM kill",
          rcs == [0] * 4 and not m.kills and m.peak <= 350 * MB,
          f"rcs {rcs}, kills {len(m.kills)}, peak {m.peak // MB} MB")
    waits = [r.get("mem_wait_s", 0) for r in E.log() if r.get("op") == "run"][-4:]
    check("dingo-pack: later jobs waited for memory (promised-but-unused memory counted)",
          sum(1 for w in waits if w > 0.3) >= 2, f"mem waits {waits}")


def t_memory_liar(E):
    """A job that lies about its size is stopped by its own limit; the honest ones are unharmed."""
    hog = hog_file(E)
    m = Machine(E, 350 * MB)
    env = {"DINGO_FAKE_MEMINFO": m.meminfo, "DINGO_RESERVE": "25M", "DINGO_MEM_POLL_S": "0.2"}
    # tree limit off: this checks the per-process limit alone
    liar = E.popen("run", "--heavy", "--mem", "60M", "--tree-limit", "0", "--", PY, hog, "300", "0.5", env=env)
    m.roots.append(liar.pid)
    time.sleep(0.1)
    honest = []
    for _ in range(2):
        honest.append(E.popen("run", "--heavy", "--mem", "100M", "--", PY, hog, "75", "0.5", env=env))
        m.roots.append(honest[-1].pid)
    rl = liar.wait(120)
    rh = [p.wait(120) for p in honest]
    m.close()
    check("limit: a job over its own limit fails by itself (MemoryError), honest ones finish",
          rl not in (0, 137) and rh == [0, 0] and not m.kills, f"liar rc {rl}, honest {rh}, kills {len(m.kills)}")


def t_tree_limit(E):
    tree = E.path("tree.py")
    with open(tree, "w") as f:
        f.write("import subprocess, sys\n"
                f"ps = [subprocess.Popen([sys.executable, {hog_file(E)!r}, '75', '3']) for _ in range(4)]\n"
                "sys.exit(max(p.wait() for p in ps))\n")
    r = E.run("run", "--heavy", "--mem", "100M", "--limit", "150M", "--tree-limit", "200M", "--", PY, tree)
    lg = [x for x in E.log() if x.get("op") == "run"][-1]
    check("tree limit: 4 workers x 75 MB under a 200 MB tree limit -> stopped, exit 137",
          r.returncode == 137 and lg.get("stopped_by_tree_limit") and lg.get("peak_tree_rss", 0) > 200 * MB,
          f"rc {r.returncode}, log {lg}")
    r = E.run("run", "--heavy", "--mem", "100M", "--limit", "150M", "--tree-limit", "0", "--", PY, tree)
    check("negative control: per-process limit alone does not catch the 300 MB tree", r.returncode == 0,
          f"rc {r.returncode}")


def t_fits(E):
    r = E.run("run", "--heavy", "--mem", "100000G", "--", "true")
    check("run: --mem larger than the machine -> refused at once, not queued forever", r.returncode == 2,
          f"rc {r.returncode}")


# ------------------------------------------------------------ lease

def t_lease(E):
    owner = subprocess.Popen(["sleep", "60"])
    env = {"DINGO_OWNER_PID": str(owner.pid)}
    r = E.run("lease", "--slots", "1", "--mem", "1M", "--for", "10m", "--note", "review", env=env)
    check("lease: returns at once with a lease id", r.returncode == 0 and "lease " in r.stdout, r.stdout + r.stderr)
    r2 = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "1s", "--", "true")
    check("lease: holds the slot", r2.returncode == 75, f"rc {r2.returncode}")
    owner.kill()
    owner.wait()
    time.sleep(1.0)
    r3 = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "3s", "--", "true")
    check("lease: freed when the owning agent process exits", r3.returncode == 0, f"rc {r3.returncode}")

    owner = subprocess.Popen(["sleep", "60"])
    env = {"DINGO_OWNER_PID": str(owner.pid)}
    r = E.run("lease", "--slots", "1", "--mem", "1M", "--for", "10m", env=env)
    lid = r.stdout.split()[1]
    rr = E.run("release", lid, env=env)
    time.sleep(0.8)
    r3 = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "3s", "--", "true")
    check("release: frees the slot", r3.returncode == 0 and "released" in rr.stdout, rr.stdout + rr.stderr)
    r = E.run("lease", "--slots", "1", "--mem", "1M", "--for", "1s", env=env)
    time.sleep(2.0)
    r3 = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "3s", "--", "true")
    check("lease: expires after --for", r3.returncode == 0, f"rc {r3.returncode}")
    owner.kill()
    owner.wait()
    ends = [x.get("why") for x in E.log() if x.get("op") == "lease-end"]
    check("lease: log says why each lease ended", ends == ["owner exited", "released", "expired"], str(ends))


# ------------------------------------------------------------ roster

def proc_start(pid):
    st = open(f"/proc/{pid}/stat").read()
    return st[st.rindex(")") + 2:].split()[19]


def make_claude_home(E, live_pid, cwd):
    """Fake ~/.claude: registry entries shaped like Claude Code's, transcripts, a project log."""
    ch = E.path("claude")
    os.makedirs(os.path.join(ch, "sessions"), exist_ok=True)
    proj = os.path.join(ch, "projects", "-proj")
    os.makedirs(proj, exist_ok=True)
    t = time.time()

    def reg(pid, sid, name, src, status, start, upd):
        with open(os.path.join(ch, "sessions", f"{pid}.json"), "w") as f:
            json.dump({"pid": pid, "sessionId": sid, "cwd": cwd, "procStart": start, "kind": "interactive",
                       "name": name, "nameSource": src, "status": status, "updatedAt": int(upd * 1000)}, f)

    def tr(sid, first, mtime):
        p = os.path.join(proj, f"{sid}.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "summary"}) + "\n")
            f.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": "<command-name>x"}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": first}]}}) + "\n")
        os.utime(p, (mtime, mtime))

    ls = proc_start(live_pid)
    # alive, user-named
    reg(live_pid, "aaaa1111-0000", "A-0101-alive", "user", "busy", ls, t)
    tr("aaaa1111-0000", "A-0101-alive, you are ...", t - 10)
    # dead before "boot", derived name; real name only in the first message; status busy
    reg(900001, "bbbb2222-0000", "work-c4", "derived", "busy", "1", t - 4000)
    tr("bbbb2222-0000", "\n<pasted_content id=\"x\">\nB2-0101-midway，你是 ...", t - 4000)
    # same derived name, different session, idle
    reg(900002, "cccc3333-0000", "work-c4", "derived", "idle", "1", t - 3900)
    tr("cccc3333-0000", "C-0101-idle: start", t - 3900)
    # pid reuse: points at a live pid but the start time does not match -> dead
    reg(os.getpid(), "dddd4444-0000", "D-0101-reused", "user", "waiting", "12345", t - 3800)
    tr("dddd4444-0000", "D-0101-reused hello", t - 3800)
    # the same session twice: an old dead copy and a fresh one (also dead) -> newest wins
    reg(900005, "eeee5555-0000", "E-0101-old", "user", "busy", "1", t - 9000)
    reg(900006, "eeee5555-0000", "E-0101-dup", "user", "idle", "1", t - 3700)
    tr("eeee5555-0000", "E-0101-dup", t - 3700)
    # a session with no transcript (opened, nothing typed)
    reg(900007, "ffff6666-0000", "work-aa", "derived", "idle", "1", t - 3600)
    # project log with a status line
    os.makedirs(os.path.join(cwd, "log"), exist_ok=True)
    with open(os.path.join(cwd, "log", "2026-01-01_B2-0101-midway.md"), "w") as f:
        f.write("# log\n状态：old\nstuff\n状态：【状态 12:00】half way through step 3\nmore\n")
    with open(E.path("config.json"), "w") as f:
        json.dump({"projects": {os.path.realpath(cwd): {
            "log_glob": "log/*_{name}.md", "status_regex": "^状态[:：]", "resume_prompt": "PROJECT PROMPT"}}}, f)
    return t


def t_roster(E):
    cwd = E.path("proj")
    os.makedirs(cwd)
    live = subprocess.Popen(["sleep", "120"])
    try:
        t = make_claude_home(E, live.pid, cwd)
        env = {"DINGO_FAKE_BTIME": str(t - 2000)}      # "the machine rebooted 2000 s ago"
        r = E.run("save", env=env)
        check("save: runs and reports", r.returncode == 0 and "1 alive" in r.stdout, r.stdout + r.stderr)
        ro = json.load(open(E.path("state", "roster", "roster.json")))
        S = ro["sessions"]
        check("save: name from the first user message when the registry name is automatic",
              S["bbbb2222-0000"]["name"] == "B2-0101-midway" and S["cccc3333-0000"]["name"] == "C-0101-idle",
              str({k: v["name"] for k, v in S.items()}))
        check("save: pid reused by another process -> dead (start time checked)",
              S["dddd4444-0000"]["alive"] is False, str(S["dddd4444-0000"]))
        check("save: alive session is alive", S["aaaa1111-0000"]["alive"] is True)
        check("save: same session id twice -> newest registry entry wins",
              S["eeee5555-0000"]["name"] == "E-0101-dup", S["eeee5555-0000"]["name"])
        check("save: dead before boot -> ended=reboot",
              S["bbbb2222-0000"]["ended"] == "reboot", S["bbbb2222-0000"].get("ended"))
        check("save: status line = last matching line of the newest log",
              S["bbbb2222-0000"].get("status_line") == "状态：【状态 12:00】half way through step 3",
              str(S["bbbb2222-0000"].get("status_line")))
        md = open(E.path("state", "roster", "roster.md")).read()
        check("save: human table written", "B2-0101-midway" in md and "claude --resume bbbb2222-0000" in md)

        r = E.run("list", env=env)
        out = r.stdout
        mid = out.split("midway - resume now")[1].split("\n\n")[0] if "midway - resume now" in out else ""
        check("list: busy-at-crash goes to midway (guessed)", "B2-0101-midway" in mid and "guessed" in mid, out)
        check("list: alive sessions are not offered", "A-0101-alive" not in out.split("alive now:")[0], out)

        # self-report
        r = E.run("mark", "done", "--sid", "bbbb2222-0000", "finished", env=env)
        r = E.run("mark", "waiting", "--sid", "cccc3333-0000", "--until", "18:30", "box relay", env=env)
        # waiting without --until: accepted with a tip, stored as null, listed last in its group
        r3 = E.run("mark", "waiting", "--sid", "dddd4444-0000", "no time given", env=env)
        ro3 = json.load(open(E.path("state", "roster", "roster.json")))["sessions"]
        check("mark: waiting without --until -> accepted with a tip, until stored as null",
              r3.returncode == 0 and "tip" in r3.stderr and ro3["dddd4444-0000"]["mark"]["until"] is None,
              r3.stdout + r3.stderr)
        r2 = E.run("mark", "running", "--sid", "eeee5555-0000", "step 3", env=env)
        ro2 = json.load(open(E.path("state", "roster", "roster.json")))["sessions"]
        check("mark: 'running' is accepted as midway", r2.returncode == 0 and
              ro2["eeee5555-0000"]["mark"]["state"] == "midway", r2.stdout + r2.stderr)
        out = E.run("list", env=env).stdout
        E.run("mark", "waiting", "--sid", "eeee5555-0000", "--until", "9:05 tomorrow", env=env)
        out = E.run("list", env=env).stdout
        wgrp = out.split("waiting for a time")[1].split("\n\n")[0] if "waiting for a time" in out else ""
        order = [n for n in ("E-0101-dup", "C-0101-idle", "D-0101-reused") if n in wgrp]
        order.sort(key=wgrp.index)
        check("list: waiting sorted by time of day (9:05 before 18:30), no time last",
              order == ["E-0101-dup", "C-0101-idle", "D-0101-reused"], f"{order}\n{wgrp}")
        E.run("mark", "running", "--sid", "eeee5555-0000", env=env)
        out = E.run("list", env=env).stdout
        check("list: self-report beats the guess (done / waiting until)",
              "done - no need" in out and "B2-0101-midway" in out.split("done - no need")[1]
              and "until 18:30" in out, out)

        # resume
        r = E.run("resume", "--midway", "--via", "print", env=env)
        check("resume --midway: B2 (now done) and D (waiting) left out, E (marked running) in",
              "B2-0101-midway" not in r.stdout and "D-0101-reused" not in r.stdout
              and "E-0101-dup" in r.stdout, r.stdout)
        r = E.run("resume", "C-0101-idle", "A-0101-alive", "ZZ-nope", "--via", "print", env=env)
        sh = E.path("state", "resume", "cccc3333-0000.sh")
        body = open(sh).read() if os.path.exists(sh) else ""
        check("resume: named session -> launcher with cd + claude --resume <id> + project prompt",
              "cd " in body and "--resume cccc3333-0000" in body and "'PROJECT PROMPT'" in body, body)
        check("resume: already running session is skipped", "skip A-0101-alive: already running" in r.stdout,
              r.stdout)
        check("resume: unknown name is reported", "ZZ-nope: not in the roster" in r.stderr, r.stderr)
        r = E.run("resume", "--lost", "--via", "print", "--batch", "1", env=env, stdin=subprocess.DEVNULL)
        check("resume --lost: stops after one batch when nobody can press Enter",
              "stopped after a batch of 1" in r.stdout, r.stdout)
        check("resume: no transcript -> not offered",
              "work-aa" not in r.stdout, r.stdout)

        # crash simulation: the live one dies with the machine
        live.kill()
        live.wait()
        r = E.run("list", env=dict(env, DINGO_FAKE_BTIME=str(time.time() + 1)))
        check("crash: a session alive at the last save and gone after reboot -> lost (reboot)",
              "A-0101-alive" in r.stdout and "ended reboot" in r.stdout.split("A-0101-alive")[1][:80], r.stdout)

        # closed while the machine was up
        live2 = subprocess.Popen(["sleep", "120"])
        reg = os.path.join(E.path("claude"), "sessions", f"{live2.pid}.json")
        with open(reg, "w") as f:
            json.dump({"pid": live2.pid, "sessionId": "gggg7777-0000", "cwd": cwd, "procStart": proc_start(live2.pid),
                       "name": "G-0101-closed", "nameSource": "user", "status": "idle", "updatedAt": 0}, f)
        with open(os.path.join(E.path("claude"), "projects", "-proj", "gggg7777-0000.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "G-0101-closed"}}) + "\n")
        E.run("save", env=env)
        live2.kill()
        live2.wait()
        out = E.run("list", env=env).stdout
        out_c = E.run("list", "--closed", env=env).stdout
        check("closed while up: hidden by default, shown with --closed",
              "G-0101-closed" not in out and "G-0101-closed" in out_c, out_c)

        # snapshots
        snaps = os.listdir(E.path("state", "roster", "snapshots"))
        check("save: keeps snapshots only when something changed", 2 <= len(snaps) <= 12, str(len(snaps)))

        # registry format changed -> fallback, flagged
        for p in os.listdir(os.path.join(E.path("claude"), "sessions")):
            with open(os.path.join(E.path("claude"), "sessions", p), "w") as f:
                json.dump({"proc": 1, "id": "x"}, f)
        E2 = Env(os.path.dirname(E.base), E.tool)
        E2.env.update(DINGO_CLAUDE_HOME=E.path("claude"), DINGO_CONFIG=E.path("config.json"))
        r = E2.run("list")
        check("registry not recognised -> falls back to transcripts and says so",
              "registry not recognised" in r.stdout, r.stdout + r.stderr)
    finally:
        live.kill()


def t_hook(E):
    cwd = E.path("proj")
    os.makedirs(os.path.join(cwd, "log"))
    live = subprocess.Popen(["sleep", "60"])
    try:
        make_claude_home(E, live.pid, cwd)
        os.unlink(os.path.join(cwd, "log", "2026-01-01_B2-0101-midway.md"))
        os.mkfifo(os.path.join(cwd, "log", "2026-01-01_B2-0101-midway.md"))   # open() would hang
        t0 = time.time()
        r = E.run("save", "--hook", input='{"session_id": "x"}', env={"DINGO_HOOK_MAX_S": "2"})
        dt = time.time() - t0
        check("hook: returns at once even if reading a log would hang", r.returncode == 0 and dt < 1.0
              and r.stdout == "" and r.stderr == "", f"{dt:.2f} s rc {r.returncode} {r.stderr}")
        time.sleep(3)
        errs = [x for x in E.log() if x.get("op") == "save-hook-error"]
        check("hook: the hung background save is stopped and logged", len(errs) == 1, str(errs))
        os.unlink(os.path.join(cwd, "log", "2026-01-01_B2-0101-midway.md"))
        r = E.run("save", "--hook", input="{}", env={"DINGO_HOOK_FOREGROUND": "1"})
        check("hook: foreground save works and is silent", r.returncode == 0 and r.stdout == "" and
              os.path.exists(E.path("state", "roster", "roster.json")), r.stderr)
        r = E.run("save", "--hook", input="{}", env={"DINGO_HOOK_FOREGROUND": "1",
                                                     "DINGO_CLAUDE_HOME": "/nonexistent/x"})
        check("hook: never fails", r.returncode == 0)
    finally:
        live.kill()


def t_local_state(E):
    if not os.path.isdir("/proc"):
        return
    nonlocal_dir = None
    best = ""
    for line in open("/proc/mounts"):        # is this self-test's own directory on a network mount?
        parts = line.split()
        if len(parts) > 2 and (HERE == parts[1] or HERE.startswith(parts[1].rstrip("/") + "/")) \
                and len(parts[1]) > len(best):
            best = parts[1]
            nonlocal_dir = HERE if parts[2] in ("9p", "drvfs", "nfs", "nfs4", "cifs") else None
    if not nonlocal_dir:
        check("state dir on a network mount is refused (skipped: no such mount here)", True)
        return
    d = tempfile.mkdtemp(prefix="dp-nonlocal-", dir=nonlocal_dir)
    try:
        r = E.run("run", "--heavy", "--mem", "1M", "--", "true", env={"DINGO_HOME": d})
        check("state dir on a network / 9p mount is refused", r.returncode == 2 and "local disk" in r.stderr,
              r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_hint(E):
    hint = os.environ.get("SELFTEST_HINT") or os.path.join(HERE, "hooks", "hint.py")
    cases = [({"tool_name": "Agent", "tool_input": {"subagent_type": "verifier"}}, True),
             ({"tool_name": "Skill", "tool_input": {"skill": "code-review"}}, True),
             ({"tool_name": "Skill", "tool_input": {"skill": "dataviz"}}, False),
             ({"tool_name": "Bash", "tool_input": {"command": "python -c 'import torch; m=torch.load(p)'"}}, True),
             ({"tool_name": "Bash", "tool_input": {"command": "dingo-pack run --heavy --mem 4G -- pytest -x"}}, False),
             ({"tool_name": "Bash", "tool_input": {"command": "git status -- docs/"}}, False),
             ({"tool_name": "Read", "tool_input": {"file_path": "/x"}}, False),
             # review findings: light commands that only mention heavy words must stay silent ...
             ({"tool_name": "Bash", "tool_input": {"command": "git commit -m 'make it work' -- a.py"}}, False),
             ({"tool_name": "Bash", "tool_input": {"command": "cat selftest_x.py"}}, False),
             ({"tool_name": "Bash", "tool_input": {"command": "wombat-gate commit -m 'x; pytest' -- t/selftest_a.py"}}, False),
             # ... and a heavy segment next to a dingo-pack mention must still be caught
             ({"tool_name": "Bash", "tool_input": {"command": "dingo-pack who && pytest -x"}}, True),
             ({"tool_name": "Bash", "tool_input": {"command": "grep -n dingo-pack README.md; python3 train.py"}}, True),
             ({"tool_name": "Bash", "tool_input": {"command": "python3 -c 'import torch; print(1)'"}}, True),
             ({"tool_name": "Bash", "tool_input": {"command": "prlimit --data=6G python3 selftest_x.py"}}, True)]
    ok = True
    for data, want in cases:
        r = subprocess.run([PY, hint], input=json.dumps(data), capture_output=True, text=True, env=E.env)
        got = bool(r.stdout.strip())
        if got:
            j = json.loads(r.stdout)
            got = j["hookSpecificOutput"]["hookEventName"] == "PreToolUse" and \
                "dingo-pack" in j["hookSpecificOutput"]["additionalContext"]
        ok &= (got == want) and r.returncode == 0
    check("hint hook: hints on sub-agents / review skills / heavy shell, silent otherwise", ok)
    # plugin install, nothing on PATH: the suggested command must use the plugin's copy
    root = E.path("plug")
    os.makedirs(os.path.join(root, "bin"))
    shutil.copy(E.tool, os.path.join(root, "bin", "dingo-pack"))
    env = dict(E.env, CLAUDE_PLUGIN_ROOT=root, PATH="/usr/bin:/bin")
    r = subprocess.run([PY, hint], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}}),
                       capture_output=True, text=True, env=env)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""
    check("hint hook: without dingo-pack on PATH, suggests the plugin's copy",
          os.path.join(root, "bin", "dingo-pack") + " run --heavy" in ctx, ctx)
    r = subprocess.run([PY, hint], input="not json", capture_output=True, text=True, env=E.env)
    check("hint hook: bad input -> silent, exit 0", r.returncode == 0 and r.stdout == "")


SHARED = r"""
import os, sys, time
buf = bytearray(150 * 1024 * 1024)
for i in range(0, len(buf), 4096): buf[i] = 1
kids = []
for _ in range(3):
    pid = os.fork()
    if pid == 0:
        time.sleep(2.0); os._exit(0)
    kids.append(pid)
for k in kids: os.waitpid(k, 0)
print("ok")
"""


def t_shared_pages(E):
    """Regression (verifier bug 1): forked workers share their parent's pages; the tree limit
    must count them once (PSS), not once per process (RSS)."""
    p = E.path("shared.py")
    with open(p, "w") as f:
        f.write(SHARED)
    r = E.run("run", "--heavy", "--mem", "200M", "--limit", "0", "--tree-limit", "400M", "--", PY, p)
    lg = [x for x in E.log() if x.get("op") == "run"][-1]
    check("tree limit: parent + 3 forked children sharing 150 MB is not stopped (counted once)",
          r.returncode == 0 and not lg.get("stopped_by_tree_limit"),
          f"rc {r.returncode}, peak {lg.get('peak_tree_rss', 0) // MB} MB, {r.stderr[-200:]}")


def t_grandchild(E):
    """Regression (verifier bug 2): a background grandchild that outlives the job must not keep
    the slot."""
    pidf = E.path("grandchild.pid")
    r = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--limit", "0", "--",
              "bash", "-c", f"setsid sleep 20 >/dev/null 2>&1 </dev/null & echo $! > {pidf}; echo job-done")
    r2 = E.run("run", "--heavy", "--slots", "1", "--mem", "1M", "--timeout", "3s", "--", "true")
    check("slot is released when the job exits, even if it left a background grandchild",
          r.returncode == 0 and r2.returncode == 0, f"rc {r.returncode} / {r2.returncode}")
    try:
        os.kill(int(open(pidf).read().strip()), signal.SIGKILL)   # only the grandchild we started
    except (OSError, ValueError):
        pass


def t_fallback_roster(E):
    """Regression (verifier bug 5): registry not recognised -> roster built from transcripts is not empty."""
    ch = E.path("claude")
    os.makedirs(os.path.join(ch, "sessions"))
    os.makedirs(os.path.join(ch, "projects", "-p"))
    with open(os.path.join(ch, "sessions", "1.json"), "w") as f:
        json.dump({"proc": 1, "id": "x"}, f)
    with open(os.path.join(ch, "projects", "-p", "abcd0000-1111.jsonl"), "w") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "Z-0101-fallback hello"}}) + "\n")
    r = E.run("list", "--json")
    j = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    names = [s.get("name") for s in j.get("lost", [])] + [s.get("name") for s in j.get("alive", [])]
    check("registry not recognised -> roster still lists sessions from transcripts",
          "Z-0101-fallback" in names, r.stdout[-300:] + r.stderr[-200:])


TESTS = [t_shared_pages, t_grandchild, t_fallback_roster, t_hint, t_slots, t_fifo, t_kill_releases, t_exitcodes, t_fits, t_memory_pack, t_memory_liar, t_tree_limit,
         t_lease, t_roster, t_hook, t_local_state]


def run_all(base, tool, only=None):
    del RESULTS[:]
    for t in TESTS:
        if only and t.__name__ not in only:
            continue
        print(f"[{t.__name__}]", flush=True)
        E = Env(base, tool)
        try:
            t(E)
        except Exception as e:      # noqa: BLE001
            check(f"{t.__name__} crashed", False, repr(e))
        finally:
            shutil.rmtree(E.base, ignore_errors=True)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return not bad


# Each mutant breaks one property; the named test must go red.
MUTANTS = [
    ("slot lock result ignored", "                if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):\n                    continue",
     "                if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):\n                    self.fd, self.index = os.open(self.slot_path(i), os.O_RDWR), i\n                    return True", ["t_slots"]),
    ("off-by-two slot count", "        for i in range(self.n):", "        for i in range(self.n + 2):", ["t_slots"]),
    ("no queue lock (no FIFO)", "            self._lock_queue(deadline)\n", "", ["t_fifo"]),
    ("promised memory not counted", "        unmet += max(0, e - used)", "        unmet += 0", ["t_memory_pack"]),
    ("no memory admission", "                ok, why = admit(est, reserve, exclude=self.index, n=self.n)",
     "                ok, why = True, ''", ["t_memory_pack"]),
    ("no per-process limit", "                resource.setrlimit(kind, (limit, limit))", "                pass",
     ["t_memory_liar"]),
    ("no tree limit", "        if tree and r > tree and not stopped:", "        if False:", ["t_tree_limit"]),
    ("lease ignores owner death", "        elif owner_pid and not pid_alive(owner_pid, owner_start):",
     "        elif False:", ["t_lease"]),
    ("pid reuse not checked", "        if cur is not None and str(cur) != str(start):", "        if False:",
     ["t_roster"]),
    ("reboot not detected", '                s["ended"] = "reboot" if (bt and ls and ls < bt) else "closed"',
     '                s["ended"] = "closed"', ["t_roster"]),
    ("hook runs in foreground", '        if os.environ.get("DINGO_HOOK_FOREGROUND") != "1":\n            try:\n                if os.fork() > 0:',
     '        if False:\n            try:\n                if os.fork() > 0:', ["t_hook"]),
    ("job does not inherit slot", "pass_fds=(slots.fd,))", "pass_fds=())", ["t_kill_releases"]),
    ("until sorted as raw text", '        grp.sort(key=lambda s: (until_key((s.get("mark") or {}).get("until")),',
     '        grp.sort(key=lambda s: (((s.get("mark") or {}).get("until") or ""),', ["t_roster"]),
    ("hint: any mention of dingo-pack counts as wrapped",
     "        if not any(segment_heavy(w) for w in segments(cmd)):",
     '        if "dingo-pack" in cmd or not any(segment_heavy(w) for w in segments(cmd)):', ["t_hint"], "hint"),
    ("hint: split before quoting", "    return out\n\n\ndef segment_heavy(w):",
     "    return [x.split() for x in re.split(r'&&|;|\\|', cmd)]\n\n\ndef segment_heavy(w):", ["t_hint"], "hint"),
    ("hint: suggests bare name from the plugin",
     '        return ["python3", os.path.join(root, "bin", "dingo-pack")]',
     '        return ["dingo-pack"]', ["t_hint"], "hint"),
    ("tree memory counted as RSS (shared pages repeated)", "        total += v if v is not None else table.get(p, (0, 0))[1]",
     "        total += table.get(p, (0, 0))[1]", ["t_shared_pages"]),
    ("release only closes (grandchild keeps the slot)", "            try:\n                fcntl.flock(self.fd, fcntl.LOCK_UN)\n            except OSError:\n                pass\n            os.close(self.fd)",
     "            os.close(self.fd)", ["t_grandchild"]),
    ("fallback entries get no last_seen", '            s["last_seen"] = max(s.get("last_seen") or 0, s.get("last_activity") or 0)',
     "            pass", ["t_fallback_roster"]),
    ("9p state dir accepted", "    if t in NONLOCAL_FS:", "    if False:", ["t_local_state"]),
]


def run_mutants(base, tool):
    tool_src = open(tool).read()
    hint_src = open(os.path.join(HERE, "hooks", "hint.py")).read()
    ok_all = True
    for m in MUTANTS:
        name, a, b, tests = m[:4]
        target = m[4] if len(m) > 4 else "tool"
        src = hint_src if target == "hint" else tool_src
        if src.count(a) != 1:
            print(f"MUTANT {name}: snippet not found exactly once (stale mutant)")
            ok_all = False
            continue
        d = tempfile.mkdtemp(prefix="dp-mut-", dir=base)
        mt = os.path.join(d, "dingo-pack")
        with open(mt, "w") as f:
            f.write(tool_src if target == "hint" else src.replace(a, b))
        os.environ.pop("SELFTEST_HINT", None)
        if target == "hint":
            mh = os.path.join(d, "hint.py")
            with open(mh, "w") as f:
                f.write(src.replace(a, b))
            os.environ["SELFTEST_HINT"] = mh
        devnull = open(os.devnull, "w")
        old = sys.stdout
        sys.stdout = devnull
        try:
            passed = run_all(base, mt, only=tests)
        finally:
            sys.stdout = old
            os.environ.pop("SELFTEST_HINT", None)
            shutil.rmtree(d, ignore_errors=True)
        killed = not passed
        ok_all &= killed
        print(f"MUTANT {name}: {'killed' if killed else 'SURVIVED'}", flush=True)
    return ok_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("TMPDIR", "/tmp"))
    ap.add_argument("--tool", default=TOOL)
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    os.makedirs(a.base, exist_ok=True)
    if a.mutants:
        sys.exit(0 if run_mutants(a.base, a.tool) else 1)
    sys.exit(0 if run_all(a.base, a.tool, a.only) else 1)


if __name__ == "__main__":
    main()
