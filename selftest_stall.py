#!/usr/bin/env python3
"""a session whose status line says running (the default stall word) and has not changed for
stall_h hours (default 2) is reported by watch (event + status), `who` and `board`; done / closed /
waiting / a session that has ended are not. The project sets only log_glob: the default
status_regex ('^Status') and status words are what is tested.

Sessions are faked the way selftest_dingo_pack's board test does it: a Claude Code session file per
`sleep` process (alive), a transcript, and a log with the status line. Private DINGO_HOME and
DINGO_CLAUDE_HOME; watch runs with a scope tag, so it never looks at real processes or sessions.
  python3 selftest_stall.py [path/to/bin/dingo-pack]
"""
import json, os, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "bin", "dingo-pack"))
BASE = os.environ.get("DINGO_TEST_BASE") or tempfile.mkdtemp(prefix="dingo-selftest-")
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"  [{str(detail)[:600]}]", flush=True)


def proc_start(pid):
    st = open(f"/proc/{pid}/stat").read()
    return int(st[st.rindex(")") + 2:].split()[19])


def stamp(dt):
    """A dated status stamp dt seconds from now (dated, so the test works across midnight)."""
    return time.strftime("%m-%d %H:%M", time.localtime(time.time() + dt))


home = os.path.join(BASE, "dh")
ch = os.path.join(BASE, "claude")
cwd = os.path.join(BASE, "proj")
# logs kept outside the session's cwd, one directory per team (the directory part is a pattern)
teams = [os.path.join(BASE, "notes", t, "logs") for t in ("team-a", "team-b")]
for d in [home, os.path.join(ch, "sessions"), os.path.join(ch, "projects", "-proj"), cwd] + teams:
    os.makedirs(d, exist_ok=True)
cfg = os.path.join(BASE, "config.json")
json.dump({"projects": {os.path.realpath(cwd): {"log_glob": "../notes/*/logs/*_{name}.md"}}},
          open(cfg, "w"))
logpath = {}
H = 3600
lines = {  # name -> (status line, alive, expected to be reported)
    "S1-1104-running-old": (f"Status {stamp(-2.5 * H)}: running - training", True, True),
    "S2-1104-handed-in": (f"Status {stamp(-3 * H)}: done - report in x", True, False),
    "S3-1104-waiting": (f"**Status {stamp(-3 * H)}:** waiting - needs a decision on y", True, False),
    "S4-1104-closed": (f"Status {stamp(-3 * H)}: closed - wrapped up", True, False),
    "S5-1104-running-fresh": (f"Status {stamp(-600)}: running - just started", True, False),
    "S6-1104-running-dead": (f"Status {stamp(-3 * H)}: running - session is gone", False, False),
}
procs = []
for k, (name, (line, alive, _)) in enumerate(lines.items()):
    p = subprocess.Popen(["sleep", "300"])
    procs.append(p)
    sid = f"{k:04d}stal-0001"
    json.dump({"pid": p.pid, "sessionId": sid, "cwd": cwd, "procStart": proc_start(p.pid), "name": name,
               "nameSource": "user", "status": "busy", "startedAt": int((time.time() - 4 * H) * 1000)},
              open(os.path.join(ch, "sessions", f"{p.pid}.json"), "w"))
    with open(os.path.join(ch, "projects", "-proj", f"{sid}.jsonl"), "w") as f:
        f.write(json.dumps({"type": "user", "message": {"content": name}}) + "\n")
    logpath[name] = os.path.join(teams[k % 2], f"2000-01-02_{name}.md")
    with open(logpath[name], "w") as f:
        f.write(f"# log\n- did a thing\n\n{line}\n")
for p, (line, alive, _) in zip(procs, lines.values()):
    if not alive:
        p.kill()
        p.wait()
env = dict(os.environ, DINGO_HOME=home, DINGO_CLAUDE_HOME=ch, DINGO_CONFIG=cfg, DINGO_SCOPE_TAG=f"stall-{os.getpid()}",
           DINGO_WATCH_STALL_EVERY="1")
for k in ("DINGO_SESSION", "DINGO_SESSION_MAX", "CLAUDE_PID", "CLAUDE_CONFIG_DIR"):
    env.pop(k, None)
want = sorted(n for n, (_, _, rep) in lines.items() if rep)
w = None
try:
    w = subprocess.Popen([sys.executable, TOOL, "watch", "--interval", "0.5", "--dry-run"], env=env,
                         stdout=open(os.path.join(BASE, "watch.log"), "w"), stderr=subprocess.STDOUT)
    evp = os.path.join(home, "watch", "events.jsonl")
    ev = []
    t0 = time.time()
    while time.time() - t0 < 20:
        ev = [json.loads(l) for l in open(evp)] if os.path.exists(evp) else []
        if any(e["kind"] == "status-stalled" for e in ev):
            break
        time.sleep(0.3)
    time.sleep(1.5)                                         # a few more scans: the event must not repeat
    ev = [json.loads(l) for l in open(evp)] if os.path.exists(evp) else []
    st = [e for e in ev if e["kind"] == "status-stalled"]
    got = sorted(e.get("session") for e in st)
    check("S1 watch: a live session saying running, unchanged 2.5 h > 2 h -> status-stalled event", "S1-1104-running-old" in got, got)
    check("S2 watch: done / waiting / closed, a fresh running and an ended session are not reported", got == want, got)
    e1 = st[0] if st else {}
    check("S3 the event says which session, how long, and where the log is",
          e1.get("hours", 0) >= 2.4 and str(e1.get("log", "")).endswith("_S1-1104-running-old.md")
          and "2.5 h" in e1.get("msg", ""), e1)
    check("S4 reported once, not on every scan", len(st) == 1, len(st))
    status = json.load(open(os.path.join(home, "watch", "status.json")))
    check("S5 watch status.json lists it", [x["name"] for x in status.get("stalled") or []] == want, status.get("stalled"))
    who = subprocess.run([sys.executable, TOOL, "who"], env=env, capture_output=True, text=True).stdout
    check("S6 `who` shows the reminder with session, hours and log",
          "stalled: S1-1104-running-old" in who and "2.5 h" in who and "_S1-1104-running-old.md" in who
          and "S2-1104" not in who and "S3-1104" not in who, who[-600:])
    brd = subprocess.run([sys.executable, TOOL, "board"], env=env, capture_output=True, text=True).stdout
    rows = {b["name"]: b for b in json.loads(subprocess.run([sys.executable, TOOL, "board", "--json"], env=env,
                                                            capture_output=True, text=True).stdout or "[]")}
    check("S7 `board` shows the reminder line and flags the row",
          "stalled: S1-1104-running-old" in brd and any(f.startswith("stalled 2.5") for f in rows.get("S1-1104-running-old", {}).get("flags", []))
          and not any(f.startswith("stalled") for n, b in rows.items() if n != "S1-1104-running-old" for f in b["flags"]),
          brd[-500:] + str({n: b["flags"] for n, b in rows.items()}))
    w.send_signal(signal.SIGTERM)
    w.wait(timeout=10)
    # N is configurable: with stall_h = 5 min the fresh running (10 min) is reported too
    brd = subprocess.run([sys.executable, TOOL, "board"], env=dict(env, DINGO_WATCH_STALL_H="0.08"),
                         capture_output=True, text=True).stdout
    check("S8 stall_h is configurable (DINGO_WATCH_STALL_H=0.08 -> the 10-minute-old running counts too)",
          "stalled: S5-1104-running-fresh" in brd and "stalled: S1-1104-running-old" in brd, brd[-500:])
    # a status line that changes resets the clock
    with open(logpath["S1-1104-running-old"], "a") as f:
        f.write(f"\nStatus {stamp(0)}: running - next step\n")
    brd = subprocess.run([sys.executable, TOOL, "board"], env=env, capture_output=True, text=True).stdout
    check("S9 the line moves on -> no longer reported", "stalled: S1" not in brd, brd[-400:])
finally:
    if w and w.poll() is None:
        w.kill()
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()
print(f"{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
