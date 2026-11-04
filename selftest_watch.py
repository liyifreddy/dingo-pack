#!/usr/bin/env python3
"""开-006 bad-input checks for `dingo-pack watch` / `session` / `run` caps.

Memory pressure for watch is simulated (fake /proc/meminfo = budget - RSS of the test's own
processes); the test processes really allocate (<= ~1.3 GB). Only processes carrying this test's
DINGO_SCOPE_TAG are visible to watch, so real sessions are never touched.
  python3 test_watch.py TOOL
"""
import json, os, signal, subprocess, sys, tempfile, threading, time
import tempfile as _tf
BASE = os.environ.get("DINGO_TEST_BASE") or _tf.mkdtemp(prefix="dingo-selftest-")

TOOL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin/dingo-pack"))
HERE = os.path.dirname(os.path.abspath(__file__))
EAT_CODE = "import time\nb=[]\nfor i in range(60):\n b.append(bytearray(50*1024*1024));time.sleep(0.05)\nprint('ate')"
MB, GB = 1 << 20, 1 << 30
TAG = f"oss006-{os.getpid()}"
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"  [{detail}]", flush=True)


def rss_tree(pid):
    tot, stack = 0, [pid]
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                st = open(f"/proc/{d}/stat").read()
                kids.setdefault(int(st[st.rindex(")") + 2:].split()[1]), []).append(int(d))
            except OSError:
                pass
    while stack:
        p = stack.pop()
        try:
            tot += int(open(f"/proc/{p}/statm").read().split()[1]) * 4096
        except OSError:
            pass
        stack += kids.get(p, [])
    return tot


def state(pid):
    try:
        st = open(f"/proc/{pid}/stat").read()
        return st[st.rindex(")") + 2:].split()[0]
    except OSError:
        return None


class Machine:
    """Writes a fake meminfo: avail = budget - extra - RSS of the given pids."""
    def __init__(self, path, total, budget):
        self.path, self.total, self.budget, self.extra, self.pids, self.on = path, total, budget, 0, [], True
        self.write()
        threading.Thread(target=self.loop, daemon=True).start()

    def write(self):
        used = sum(rss_tree(p) for p in self.pids)
        avail = int(max(0, self.budget - self.extra - used))
        tmp = self.path + ".t"
        open(tmp, "w").write(f"MemTotal: {self.total // 1024} kB\nMemFree: {avail // 1024} kB\nMemAvailable: {avail // 1024} kB\n")
        os.replace(tmp, self.path)
        self.avail = avail

    def loop(self):
        while self.on:
            self.write()
            time.sleep(0.1)


def events(home):
    p = os.path.join(home, "watch", "events.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def wait_for(fn, timeout):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return None


def start_watch(home, fake, extra_env):
    env = dict(os.environ, DINGO_HOME=home, DINGO_FAKE_MEMINFO=fake, DINGO_SCOPE_TAG=TAG, **extra_env)
    return subprocess.Popen([sys.executable, TOOL, "watch", "--interval", "0.5"], env=env,
                            stdout=open(os.path.join(home, "watch.log"), "w"), stderr=subprocess.STDOUT)


def eater(n_mb, step, pause, hold=0, env_extra=None, exe=None):
    env = dict(os.environ, DINGO_SCOPE_TAG=TAG, **(env_extra or {}))
    for k in ("CLAUDE_PID", "DINGO_SESSION", "DINGO_SESSION_MAX"):
        if k not in (env_extra or {}):
            env.pop(k, None)
    code = (f"import time;b=[]\nfor i in range(0,{n_mb},{step}):\n b.append(bytearray({step}*1024*1024));time.sleep({pause})\n"
            f"print('ate {n_mb} MB ok',flush=True);time.sleep({hold})")
    return subprocess.Popen([exe or sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)


def base_env():
    return {"DINGO_WATCH_MIN_TARGET": "64M", "DINGO_WATCH_GROW_WARN": "300M", "DINGO_WATCH_GROW_WINDOW": "10"}


# ---------------------------------------------------------------- A: slow unwrapped grower -> alert, pause, resume
def test_grow_pause_resume():
    home = tempfile.mkdtemp(prefix="w1-", dir=BASE)
    fake = os.path.join(home, "meminfo")
    m = Machine(fake, 4 * GB, 3 * GB)
    # a fake "claude" agent (comm = claude) that is itself big: must never be paused
    bindir = os.path.join(home, "bin"); os.makedirs(bindir)
    os.symlink(sys.executable, os.path.join(bindir, "claude"))
    agent = eater(500, 100, 0.01, hold=120, exe=os.path.join(bindir, "claude"))
    wait_for(lambda: rss_tree(agent.pid) > 450 * MB, 10)
    # an unowned big process (no session): alert only, never paused by default
    lone_code = "import time;b=bytearray(300*1024*1024);time.sleep(120)"
    lone_env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PID",)}
    lone_env["DINGO_SCOPE_TAG"] = TAG
    out = subprocess.run(["bash", "-c", f"{sys.executable} -c '{lone_code}' >/dev/null 2>&1 & echo $!"], env=lone_env,
                         capture_output=True, text=True).stdout       # orphaned: no agent among its ancestors
    lone = type("P", (), {})(); lone.pid = int(out.strip())
    lone.kill = lambda: os.kill(lone.pid, signal.SIGKILL); lone.wait = lambda: None
    # the culprit: started from "inside" the agent session (CLAUDE_PID = the fake agent), not via dingo-pack
    bad = eater(1300, 20, 0.1, env_extra={"CLAUDE_PID": str(agent.pid)})
    m.pids = [agent.pid, lone.pid, bad.pid]
    w = start_watch(home, fake, dict(base_env(), DINGO_WATCH_WARN="1.6G", DINGO_WATCH_STOP="1.1G", DINGO_WATCH_KILL="0.3G"))
    paused = wait_for(lambda: [e for e in events(home) if e["kind"] == "paused"], 40)
    ev = events(home)
    kinds = [e["kind"] for e in ev]
    first_alert = kinds.index("low-memory") if "low-memory" in kinds else 99   # warn line is above the stop line
    first_pause = kinds.index("paused") if "paused" in kinds else -1
    check("A1 slow unwrapped grower: alert comes before the pause", 0 <= first_alert < first_pause, kinds)
    check("A2 the paused one is the culprit (not the agent, not the unowned one)",
          bool(paused) and paused[0]["pid"] == bad.pid, paused)
    check("A3 culprit attributed to its session via CLAUDE_PID", bool(paused) and paused[0].get("session") == f"claude pid {agent.pid}",
          paused and paused[0].get("session"))
    time.sleep(1.0)
    r1 = rss_tree(bad.pid); time.sleep(1.5); r2 = rss_tree(bad.pid)
    check("A4 paused = state T, memory stops growing", state(bad.pid) == "T" and r2 - r1 < 25 * MB, (state(bad.pid), r1, r2))
    check("A5 fast-growth alert names the pid", any(e["kind"] == "fast-growth" and e.get("pid") == bad.pid for e in ev), kinds)
    time.sleep(max(0, paused[0]["t"] + 7 - time.time()) if paused else 0)   # past the 5 s cooldown: a 2nd pick would show
    check("A6 agent and unowned process never paused", state(agent.pid) != "T" and state(lone.pid) != "T",
          (state(agent.pid), state(lone.pid)))
    st = json.load(open(os.path.join(home, "watch", "status.json")))
    check("A7 status.json lists the paused job with its session and a resume command",
          any(r["pid"] == bad.pid for r in st["stopped"]) and "watch resume" in paused[0]["msg"], st["stopped"])
    out = subprocess.run([sys.executable, TOOL, "who"], env=dict(os.environ, DINGO_HOME=home, DINGO_FAKE_MEMINFO=fake),
                         capture_output=True, text=True).stdout
    check("A8 `who` shows watch running and the paused pid", "watch: running" in out and f"paused pid {bad.pid}" in out, out)
    gate = subprocess.run([sys.executable, TOOL, "who", "--gate"], env=dict(os.environ, DINGO_HOME=home, DINGO_FAKE_MEMINFO=fake),
                          capture_output=True, text=True)
    check("A9 `who --gate` says WAIT (exit 1) while a job is paused / memory is short", gate.returncode == 1 and "WAIT" in gate.stdout,
          gate.stdout)
    # pressure goes away (the agent frees its memory) -> auto resume after the hold, job finishes
    agent.kill(); lone.kill(); agent.wait(); lone.wait()
    m.pids = [bad.pid]; m.budget = 3.9 * GB
    resumed = wait_for(lambda: [e for e in events(home) if e["kind"] == "resumed"], 40)
    check("A10 room again -> watch resumes it by itself (SIGCONT)", bool(resumed), [e["kind"] for e in events(home)])
    try:
        out, _ = bad.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        out = "(timeout)"
    check("A11 resumed job carries on and finishes its work", bad.returncode == 0 and "ate 1300 MB ok" in out, (bad.returncode, out))
    w.send_signal(signal.SIGTERM); w.wait(timeout=10); m.on = False


# ---------------------------------------------------------------- B: near the edge -> kill the biggest, say how to re-run
def test_kill():
    home = tempfile.mkdtemp(prefix="w2-", dir=BASE)
    fake = os.path.join(home, "meminfo")
    m = Machine(fake, 4 * GB, 2 * GB)
    small = eater(200, 100, 0.01, hold=120, env_extra={"DINGO_SESSION": "s-small"})
    big = eater(900, 30, 0.05, hold=120, env_extra={"DINGO_SESSION": "s-big"})
    m.pids = [small.pid, big.pid]
    w = start_watch(home, fake, dict(base_env(), DINGO_WATCH_WARN="1.4G", DINGO_WATCH_STOP="1.2G", DINGO_WATCH_KILL="0.6G"))
    wait_for(lambda: [e for e in events(home) if e["kind"] == "paused"], 30)
    m.extra = 900 * MB                      # something else eats the rest: past the kill line
    killed = wait_for(lambda: [e for e in events(home) if e["kind"] == "killed"], 20)
    time.sleep(0.5)
    check("B1 past the kill line: the biggest job is killed, the small one lives",
          bool(killed) and killed[0]["pid"] == big.pid and big.poll() is not None and small.poll() is None,
          (killed, big.poll(), small.poll()))
    check("B2 kill event says who, why and how to run it again",
          bool(killed) and killed[0].get("session") == "s-big" and "dingo-pack run --heavy --mem" in killed[0]["msg"], killed)
    small.kill(); w.send_signal(signal.SIGTERM); w.wait(timeout=10); m.on = False


# ---------------------------------------------------------------- C: session cap (dingo-pack session --max)
def test_session_cap():
    home = tempfile.mkdtemp(prefix="w3-", dir=BASE)
    fake = os.path.join(home, "meminfo")
    m = Machine(fake, 32 * GB, 30 * GB)     # plenty of memory: only the session cap matters
    env = dict(os.environ, DINGO_HOME=home, DINGO_SCOPE_TAG=TAG)
    dry = subprocess.run([sys.executable, TOOL, "session", "--name", "capped", "--max", "400M", "--dry-run", "--", "claude"],
                         env=env, capture_output=True, text=True).stdout
    check("C1 session on cgroup v1 (this WSL) falls back to watch enforcement", "enforced by dingo-pack watch" in dry
          and "would run: claude" in dry, dry)
    # session wraps a shell that starts the job, like an agent's Bash tool would
    code = "import time;b=[]\nfor i in range(40):\n b.append(bytearray(20*1024*1024));time.sleep(0.1)\ntime.sleep(60)"
    sess = subprocess.Popen([sys.executable, TOOL, "session", "--name", "capped", "--max", "400M", "--quiet", "--",
                             "bash", "-c", f"{sys.executable} -c '{code}'; echo done"], env=env)
    m.pids = [sess.pid]
    w = start_watch(home, fake, base_env())
    paused = wait_for(lambda: [e for e in events(home) if e["kind"] == "paused"], 30)
    check("C2 session over its 400M cap -> its biggest job paused, the session shell not",
          bool(paused) and paused[0].get("session") == "capped" and state(sess.pid) != "T", (paused, state(sess.pid)))
    time.sleep(3)
    check("C3 a session-cap pause is not auto-resumed while memory is plentiful",
          not [e for e in events(home) if e["kind"] == "resumed"], [e["kind"] for e in events(home)])
    r = subprocess.run([sys.executable, TOOL, "watch", "resume", str(paused[0]["pid"])], env=env, capture_output=True, text=True)
    time.sleep(0.5)
    check("C4 `watch resume PID` continues it by hand", state(paused[0]["pid"]) in ("S", "R") and "resumed" in r.stdout,
          (state(paused[0]["pid"]), r.stdout, r.stderr))
    if paused:
        os.kill(paused[0]["pid"], signal.SIGKILL)
    sess.kill(); w.send_signal(signal.SIGTERM); w.wait(timeout=10); m.on = False


# ---------------------------------------------------------------- D: cgroup detection on a fake v2 tree
def test_cgroup_detect():
    d = tempfile.mkdtemp(prefix="cg-", dir=BASE)
    os.makedirs(os.path.join(d, "user.slice/user-1002.slice/user@1002.service"))
    open(os.path.join(d, "cgroup.controllers"), "w").write("cpu io memory pids\n")
    open(os.path.join(d, "user.slice/user-1002.slice/user@1002.service/cgroup.controllers"), "w").write("cpu memory pids\n")
    pc = os.path.join(d, "proc_cgroup")
    open(pc, "w").write("0::/user.slice/user-1002.slice/user@1002.service/app.slice/x.scope\n")
    env = dict(os.environ, DINGO_HOME=d, DINGO_FAKE_CGROUP_ROOT=d, DINGO_FAKE_PROC_CGROUP=pc)
    out = subprocess.run([sys.executable, TOOL, "session", "--max", "8G", "--dry-run", "--", "claude"], env=env,
                         capture_output=True, text=True).stdout
    check("D1 delegated cgroup v2 -> session uses systemd-run MemoryMax", "systemd-run --user --scope" in out and "MemoryMax=" in out, out)
    open(pc, "w").write("12:memory:/user.slice\n0::/user.slice/x.scope\n")
    out = subprocess.run([sys.executable, TOOL, "session", "--max", "8G", "--dry-run", "--", "claude"], env=env,
                         capture_output=True, text=True).stdout
    check("D2 hybrid v1 (like this WSL) -> no systemd-run", "systemd-run" not in out, out)


# ---------------------------------------------------------------- E: run caps on the real machine
def test_run_caps():
    home = tempfile.mkdtemp(prefix="r-", dir=BASE)
    env = dict(os.environ, DINGO_HOME=home)
    lo = {"v": None}
    def mon():
        while not lo.get("stop"):
            a = int([l for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0].split()[1]) * 1024
            lo["v"] = a if lo["v"] is None else min(lo["v"], a)
            time.sleep(0.05)
    a0 = int([l for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0].split()[1]) * 1024
    t = threading.Thread(target=mon, daemon=True); t.start()
    r = subprocess.run([sys.executable, TOOL, "run", "--heavy", "--mem", "300M", "--threads", "1", "--", sys.executable, "-c", EAT_CODE],
                       env=env, capture_output=True, text=True)
    check("E1 one process past its cap (300M est, 450M limit, asks 3 GB) -> only it fails",
          r.returncode != 0 and "ate" not in r.stdout and "MemoryError" in r.stderr, (r.returncode, r.stdout, r.stderr[-300:]))
    code = ("import os,time\nfor i in range(4):\n if os.fork()==0:\n  b=bytearray(250*1024*1024);[b.__setitem__(j,1) for j in range(0,len(b),4096)];time.sleep(30);os._exit(0)\n"
            "time.sleep(30)")
    r = subprocess.run([sys.executable, TOOL, "run", "--heavy", "--mem", "300M", "--threads", "1", "--", sys.executable, "-c", code],
                       env=env, capture_output=True, text=True, timeout=60)
    lo["stop"] = True; t.join()
    check("E2 a tree of 4 x 250 MB workers (each under the per-process limit) -> tree stopped, exit 137",
          r.returncode == 137 and "tree-limit" in r.stderr, (r.returncode, r.stderr[-300:]))
    check("E3 machine available memory never dropped more than 1.2 GB during E1/E2",
          a0 - lo["v"] < 1.2 * GB, f"start {a0 / GB:.1f} G, lowest {lo['v'] / GB:.1f} G")


def cleanup():
    """Kill every process still carrying this test's tag (SIGKILL works on paused ones too)."""
    for d in os.listdir("/proc"):
        if d.isdigit() and int(d) != os.getpid():
            try:
                if f"DINGO_SCOPE_TAG={TAG}".encode() in open(f"/proc/{d}/environ", "rb").read().split(b"\0"):
                    os.kill(int(d), signal.SIGKILL)
            except OSError:
                pass


ALL = (test_grow_pause_resume, test_kill, test_session_cap, test_cgroup_detect, test_run_caps)
only = os.environ.get("ONLY")
for i, f in enumerate(ALL):
    if only and f.__name__ not in only.split(","):
        continue
    TAG = f"oss006-{os.getpid()}-{i}"          # each test sees only its own processes
    try:
        f()
    except Exception as e:
        check(f.__name__ + " raised", False, repr(e))
    finally:
        cleanup()
print(f"{sum(res)}/{len(res)} passed")
