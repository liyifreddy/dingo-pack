#!/usr/bin/env python3
"""开-013 bad-input checks for watch's signal safety and control state (findings A-D of 开-010).

A  every signal re-checks the identity of each recorded process (pid reuse of root / child, other
   owner, now a protected process, reuse between scan and signal)
B  "at most two automatic resumes per job" holds over pause cycles and over a watch restart
C  `watch resume` with no watch running clears the paused state, so `who --gate` lets the next one in
D  concurrent `watch resume` calls, and a resume while the watch empties its mailbox, lose nothing

A, B and D run the real watch code in this process against a simulated process table (fake pids,
os.kill replaced): no real process is ever signalled. C uses one small real process of its own
(40 MB), paused and resumed under a private DINGO_HOME with a fake meminfo.
  python3 selftest_watch_control.py [path/to/bin/dingo-pack]
Each check prints PASS / FAIL; the exit code is 1 if any check failed.
"""
import importlib.machinery, importlib.util, json, os, signal, subprocess, sys, tempfile, threading, time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "bin", "dingo-pack"))
BASE = os.environ.get("DINGO_TEST_BASE") or tempfile.mkdtemp(prefix="dingo-selftest-")
MB, GB = 1 << 20, 1 << 30
UID = os.getuid()
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"  [{detail}]", flush=True)


def load(tag):
    loader = importlib.machinery.SourceFileLoader(f"dingo_wc_{tag}", TOOL)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class World:
    """A simulated process table. Pids are far above anything real; signals only change this table."""
    def __init__(self, mod, home):
        self.mod, self.home = mod, home
        self.p = {}
        self.total, self.avail, self.t = 32 * GB, 20 * GB, 1_000_000.0
        self.sent = []                      # (pid, start at send time, signal)
        self.on_cmdline = None              # hook: runs when pause() / kill() look at the target
        self._kill = os.kill

    def add(self, pid, ppid=1, rss=500 * MB, comm="python3", start=None, uid=UID, session="s1"):
        self.p[pid] = {"ppid": ppid, "rss": rss, "comm": comm, "state": "S", "uid": uid,
                       "start": start if start is not None else pid * 10, "session": session}

    def reuse(self, pid, **kw):
        """pid now names a different process (new start time)."""
        old = self.p[pid]
        self.add(pid, ppid=kw.get("ppid", old["ppid"]), rss=kw.get("rss", old["rss"]), comm=kw.get("comm", old["comm"]),
                 start=old["start"] + 777, uid=kw.get("uid", old["uid"]), session=kw.get("session", old["session"]))

    def kill(self, pid, sig):
        pid = int(pid)
        if pid not in self.p:
            if pid < 3_000_000:
                return self._kill(pid, sig) if sig == 0 else None     # never signal a real process
            raise ProcessLookupError(pid)
        if sig == 0:
            return None
        self.sent.append((pid, self.p[pid]["start"], sig))
        if sig == signal.SIGSTOP:
            self.p[pid]["state"] = "T"
        elif sig == signal.SIGCONT:
            self.p[pid]["state"] = "S"
        elif sig == signal.SIGKILL:
            del self.p[pid]

    def got(self, sig, pid=None, start=None):
        return [(p, s) for p, s, g in self.sent if g == sig and (pid is None or p == pid) and (start is None or s == start)]

    def __enter__(self):
        m, w = self.mod, self
        os.environ["DINGO_HOME"] = self.home
        os.environ["DINGO_CONFIG"] = os.path.join(self.home, "no-config.json")
        os.environ.pop("DINGO_SCOPE_TAG", None)
        m.PIDFD = False
        m.now = lambda: w.t
        m.meminfo = lambda: (w.total, w.avail)
        m.proc_table = lambda: {pid: (r["ppid"], r["rss"]) for pid, r in w.p.items()}
        m.proc_start = lambda pid: w.p[int(pid)]["start"] if int(pid) in w.p else None
        m.proc_info = lambda pid: (w.p[int(pid)]["comm"], w.p[int(pid)]["state"], w.p[int(pid)]["uid"]) if int(pid) in w.p else None
        m.proc_environ = lambda pid, keys=(): ({"DINGO_SESSION": w.p[int(pid)]["session"]}
                                               if int(pid) in w.p and w.p[int(pid)]["session"] else {})
        m.proc_pss = lambda pid: None

        def cmdline(pid):
            if w.on_cmdline:
                f, w.on_cmdline = w.on_cmdline, None
                f()
            return f"fake job {pid}"
        m.proc_cmdline = cmdline
        os.kill = self.kill
        return self

    def __exit__(self, *a):
        os.kill = self._kill


def wconf(mod, **kw):
    w = {"warn": "4G", "stop": "2G", "kill": "0.5G", "min_target": "64M", "cooldown": 0, "unowned": "alert"}
    w.update(kw)
    return mod.watch_config({"watch": w}, 32 * GB)


def resume_args(*pids):
    return SimpleNamespace(action="resume", pids=[str(p) for p in pids] or [], json=False, session=None,
                           daemon=False, dry_run=False, interval=None, once=False, scans=None)


def status(home):
    return json.load(open(os.path.join(home, "watch", "status.json")))


# ------------------------------------------------------------------ A: identity before every signal
def pause_one(mod, home, w):
    """root 3000100 with a child 3000101; memory below the stop line -> watch pauses the tree."""
    w.add(3000100, rss=900 * MB)
    w.add(3000101, ppid=3000100, rss=900 * MB)
    wt = mod.Watcher(wconf(mod))
    w.avail = 1.5 * GB
    wt.step()
    return wt


def test_identity():
    home = tempfile.mkdtemp(prefix="a-", dir=BASE)
    mod = load("a")
    with World(mod, home) as w:
        wt = pause_one(mod, home, w)
        check("A0 setup: the root and its child are paused", w.p[3000100]["state"] == "T" and w.p[3000101]["state"] == "T",
              w.sent)
        # A1 child pid reused while paused -> a manual resume must not wake the new process
        w.reuse(3000101)
        new_start = w.p[3000101]["start"]
        mod.cmd_watch(resume_args(3000100))
        check("A1 child pid reused while paused: `watch resume` does not SIGCONT the new process",
              not w.got(signal.SIGCONT, 3000101, new_start) and w.got(signal.SIGCONT, 3000100), w.sent)
    home = tempfile.mkdtemp(prefix="a2-", dir=BASE)
    with World(mod, home) as w:
        wt = pause_one(mod, home, w)
        w.reuse(3000101)
        new_start = w.p[3000101]["start"]
        w.avail = 20 * GB
        w.t += 30
        wt.step()                                              # automatic resume path
        check("A2 child pid reused while paused: the automatic resume does not SIGCONT the new process",
              not w.got(signal.SIGCONT, 3000101, new_start), w.sent)
    home = tempfile.mkdtemp(prefix="a3-", dir=BASE)
    with World(mod, home) as w:
        wt = pause_one(mod, home, w)
        w.reuse(3000100)                                       # the root itself is another process now
        w.reuse(3000101)
        n0 = len(w.sent)
        mod.cmd_watch(resume_args(3000100))
        w.avail, w.t = 20 * GB, w.t + 30
        wt.step()
        check("A3 root pid reused: nothing of the old record is signalled (manual or automatic)",
              not [s for s in w.sent[n0:] if s[2] == signal.SIGCONT], w.sent[n0:])
    home = tempfile.mkdtemp(prefix="a4-", dir=BASE)
    with World(mod, home) as w:
        wt = pause_one(mod, home, w)
        w.reuse(3000101, uid=UID + 4242)                      # now owned by someone else
        start_other = w.p[3000101]["start"]
        mod.cmd_watch(resume_args("all"))
        check("A4 child pid now owned by another user: not signalled",
              not w.got(signal.SIGCONT, 3000101, start_other), w.sent)
    home = tempfile.mkdtemp(prefix="a5-", dir=BASE)
    with World(mod, home) as w:
        wt = pause_one(mod, home, w)
        w.reuse(3000101, comm="tmux: server")                  # now a protected process
        start_tmux = w.p[3000101]["start"]
        mod.cmd_watch(resume_args("all"))
        check("A5 child pid now a protected process (tmux): not signalled",
              not w.got(signal.SIGCONT, 3000101, start_tmux), w.sent)
    home = tempfile.mkdtemp(prefix="a6-", dir=BASE)
    with World(mod, home) as w:
        # reuse between the scan and the SIGSTOP: the scan saw the old child, the signal must not hit the new one
        w.add(3000100, rss=900 * MB)
        w.add(3000101, ppid=3000100, rss=900 * MB)
        wt = mod.Watcher(wconf(mod))
        w.avail = 1.5 * GB
        w.on_cmdline = lambda: w.reuse(3000101, comm="claude")
        wt.step()
        start_agent = w.p[3000101]["start"]
        check("A6 child reused between scan and SIGSTOP (now an agent): not stopped",
              not w.got(signal.SIGSTOP, 3000101, start_agent) and w.p[3000101]["state"] != "T", w.sent)
    home = tempfile.mkdtemp(prefix="a7-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000100, rss=900 * MB)
        w.add(3000101, ppid=3000100, rss=900 * MB)
        wt = mod.Watcher(wconf(mod))
        w.avail = 0.3 * GB                                     # below the kill line
        w.on_cmdline = lambda: w.reuse(3000101)
        wt.step()
        start_new = w.p.get(3000101, {}).get("start")
        check("A7 child reused between scan and SIGKILL: the new process is not killed",
              3000101 in w.p and not w.got(signal.SIGKILL, 3000101, start_new), w.sent)
    # A8/A9: a record written by the watch before this change (no per-process start times)
    for name, reuse_child in (("A8", False), ("A9", True)):
        home = tempfile.mkdtemp(prefix=name.lower() + "-", dir=BASE)
        with World(mod, home) as w:
            w.add(3000100, rss=900 * MB)
            w.add(3000101, ppid=3000100, rss=900 * MB)
            w.p[3000100]["state"] = w.p[3000101]["state"] = "T"
            os.makedirs(os.path.join(home, "watch"), exist_ok=True)
            json.dump({"stopped": [{"pid": 3000100, "start": w.p[3000100]["start"], "pids": [3000100, 3000101],
                                    "session": "s1", "why": "machine", "reason": "old", "t": w.t, "resumes": 0}]},
                      open(os.path.join(home, "watch", "status.json"), "w"))
            if reuse_child:
                w.reuse(3000101, ppid=1)                       # gone, pid now names an unrelated process
            start_child = w.p[3000101]["start"]
            mod.cmd_watch(resume_args("all"))
            hit = bool(w.got(signal.SIGCONT, 3000101, start_child))
            if reuse_child:
                check("A9 old-format record, child pid reused (no longer under the root): not signalled", not hit, w.sent)
            else:
                check("A8 old-format record, child still under the paused root: resumed with it",
                      hit and w.got(signal.SIGCONT, 3000100), w.sent)


# ------------------------------------------------------------------ B: at most two automatic resumes
def cycle(wt, w):
    """memory short -> pause; room again after the hold -> (maybe) resume. Returns True if resumed."""
    w.avail = 1.5 * GB
    w.t += 1
    wt.step()
    w.avail = 20 * GB
    w.t += 25
    n0 = len(w.got(signal.SIGCONT))
    wt.step()
    return len(w.got(signal.SIGCONT)) > n0


def test_auto_resume_limit():
    mod = load("b")
    home = tempfile.mkdtemp(prefix="b1-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000200, rss=900 * MB)
        wt = mod.Watcher(wconf(mod))
        got = [cycle(wt, w) for _ in range(3)]
        check("B1 three machine pauses of one job: resumed automatically twice, the third stays paused",
              got == [True, True, False] and w.p[3000200]["state"] == "T", got)
    home = tempfile.mkdtemp(prefix="b2-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000200, rss=900 * MB)
        wt = mod.Watcher(wconf(mod))
        got = [cycle(wt, w) for _ in range(2)]
        wt = mod.Watcher(wconf(mod))                           # watch restarted (status.json is all it has)
        got.append(cycle(wt, w))
        check("B2 the count survives a watch restart: 3rd pause after the restart is not auto-resumed",
              got == [True, True, False], got)
    home = tempfile.mkdtemp(prefix="b3-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000200, rss=900 * MB)
        wt = mod.Watcher(wconf(mod))
        got = [cycle(wt, w) for _ in range(2)]
        del w.p[3000200]
        w.t += 1
        wt.step()                                              # the job is gone
        w.add(3000200, rss=900 * MB, start=123456)            # same pid, a new process
        got.append(cycle(wt, w))
        check("B3 a new process under the same pid starts with a fresh count", got == [True, True, True], got)


# ------------------------------------------------------------------ C: resume with no watch running, then gate
def test_offline_resume_gate():
    home = tempfile.mkdtemp(prefix="c-", dir=BASE)
    fake = os.path.join(home, "meminfo")
    def mem(avail_gb):
        open(fake, "w").write(f"MemTotal: {32 * 1024 * 1024} kB\nMemFree: {int(avail_gb * 1024 * 1024)} kB\n"
                              f"MemAvailable: {int(avail_gb * 1024 * 1024)} kB\n")
    mem(1.5)
    tag = f"oss013-{os.getpid()}"
    env = dict(os.environ, DINGO_HOME=home, DINGO_FAKE_MEMINFO=fake, DINGO_SCOPE_TAG=tag,
               DINGO_CONFIG=os.path.join(home, "no-config.json"), DINGO_WATCH_WARN="3G", DINGO_WATCH_STOP="2G",
               DINGO_WATCH_KILL="0.5G", DINGO_WATCH_MIN_TARGET="16M")
    for k in ("CLAUDE_PID", "DINGO_SESSION_MAX"):
        env.pop(k, None)
    job = subprocess.Popen([sys.executable, "-c", "import time;b=bytearray(40*1024*1024);time.sleep(90)"],
                           env=dict(env, DINGO_SESSION="c-job"))
    try:
        time.sleep(0.5)
        w = subprocess.Popen([sys.executable, TOOL, "watch", "--interval", "0.3"], env=env,
                             stdout=open(os.path.join(home, "watch.log"), "w"), stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < 20 and state(job.pid) != "T":
            time.sleep(0.2)
        check("C0 setup: watch paused the job", state(job.pid) == "T", state(job.pid))
        w.send_signal(signal.SIGTERM)
        w.wait(timeout=10)
        mem(20)                                                # plenty of memory now
        r = subprocess.run([sys.executable, TOOL, "watch", "resume", "all"], env=env, capture_output=True, text=True)
        time.sleep(0.3)
        check("C1 watch stopped, `watch resume all` continues the job", state(job.pid) in ("S", "R"), (state(job.pid), r.stdout))
        g = subprocess.run([sys.executable, TOOL, "who", "--gate", "--need", "1M"], env=env, capture_output=True, text=True)
        check("C2 ... and `who --gate` then says OK (the job is running, not paused)",
              g.returncode == 0 and "gate: OK" in g.stdout, g.stdout[-400:])
        st = json.load(open(os.path.join(home, "watch", "status.json")))
        check("C3 ... and status.json no longer lists it as paused", not any(x["pid"] == job.pid for x in st.get("stopped") or []),
              st.get("stopped"))
        os.kill(job.pid, signal.SIGSTOP)                       # paused again by hand: still recorded? no -> gate is about watch
        # gate must still say WAIT for a job watch really holds paused: rewrite the record and check
        st["stopped"] = [{"pid": job.pid, "start": proc_start(job.pid), "pids": [job.pid], "t": time.time(),
                          "why": "machine", "reason": "test"}]
        open(os.path.join(home, "watch", "status.json"), "w").write(json.dumps(st))
        g = subprocess.run([sys.executable, TOOL, "who", "--gate", "--need", "1M"], env=env, capture_output=True, text=True)
        check("C4 control: a job that really is paused by watch still makes the gate WAIT", g.returncode == 1 and "WAIT" in g.stdout,
              g.stdout[-300:])
    finally:
        job.kill()
        job.wait()


def state(pid):
    try:
        st = open(f"/proc/{pid}/stat").read()
        return st[st.rindex(")") + 2:].split()[0]
    except OSError:
        return None


def proc_start(pid):
    st = open(f"/proc/{pid}/stat").read()
    return int(st[st.rindex(")") + 2:].split()[19])


# ------------------------------------------------------------------ D: concurrent control requests
def slow_note_reads(mod, delay, gate=None, who=None):
    """Make every read of the resume mailbox slow (and optionally announce it), to force the interleaving."""
    real = mod.read_json
    def rj(path, default=None):
        v = real(path, default)
        if path.endswith("resumed.json") and (who is None or threading.current_thread().name == who):
            if gate is not None:
                gate.set()
            time.sleep(delay)
        return v
    mod.read_json = rj


def test_concurrent():
    mod = load("d")
    home = tempfile.mkdtemp(prefix="d1-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000300, rss=900 * MB)
        w.add(3000301, rss=900 * MB, session="s2")
        wt = mod.Watcher(wconf(mod))
        w.avail = 1.5 * GB
        wt.step()
        w.t += 1
        wt.step()                                              # both paused
        slow_note_reads(mod, 0.3)
        th = [threading.Thread(target=mod.cmd_watch, args=(resume_args(p),)) for p in (3000300, 3000301)]
        [t.start() for t in th]
        [t.join() for t in th]
        note = json.load(open(os.path.join(home, "watch", "resumed.json")))
        check("D1 two `watch resume` at once: both requests are in the mailbox", sorted(note) == [3000300, 3000301], note)
    mod = load("d2")
    home = tempfile.mkdtemp(prefix="d2-", dir=BASE)
    with World(mod, home) as w:
        w.add(3000300, rss=900 * MB)
        w.add(3000301, rss=900 * MB, session="s2")
        wt = mod.Watcher(wconf(mod))
        w.avail = 1.5 * GB
        wt.step()
        w.t += 1
        wt.step()
        w.avail = 3 * GB                                       # between stop and warn: no automatic action
        os.makedirs(os.path.join(home, "watch"), exist_ok=True)
        open(os.path.join(home, "watch", "resumed.json"), "w").write("[3000300]")
        reading = threading.Event()
        slow_note_reads(mod, 0.4, gate=reading, who="MainThread")
        late = threading.Thread(target=lambda: (reading.wait(5), mod.cmd_watch(resume_args(3000301))), name="late")
        late.start()
        # the watch's own consume + status write (one scan of the real loop)
        if hasattr(mod, "take_resume_requests"):
            mod.take_resume_requests(wt)
            wt.step()
        else:                                                  # the loop body of the version before 开-013
            note = os.path.join(home, "watch", "resumed.json")
            for pid in mod.read_json(note, []) or []:
                wt.stopped.pop(int(pid), None)
            if os.path.exists(note):
                os.unlink(note)
            wt.step()
        late.join()
        mod.read_json = load("d2b").read_json
        if hasattr(mod, "take_resume_requests"):
            mod.take_resume_requests(wt)                       # the next scan
        else:
            note = os.path.join(home, "watch", "resumed.json")
            for pid in mod.read_json(note, []) or []:
                wt.stopped.pop(int(pid), None)
        check("D2 a resume that arrives while watch empties its mailbox is not lost: watch drops both records",
              3000300 not in wt.stopped and 3000301 not in wt.stopped, sorted(wt.stopped))


ALL = (test_identity, test_auto_resume_limit, test_offline_resume_gate, test_concurrent)
only = os.environ.get("ONLY")
saved = dict(os.environ)
for f in ALL:
    if only and f.__name__ not in only.split(","):
        continue
    try:
        f()
    except Exception as e:                                     # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(f.__name__ + " raised", False, repr(e))
    finally:
        os.environ.clear()
        os.environ.update(saved)
print(f"{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
