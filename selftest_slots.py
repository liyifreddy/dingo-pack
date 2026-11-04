#!/usr/bin/env python3
"""R1 (Yi 16:20): heavy slots not fixed at 6 -- memory and CPU decide. Bad inputs on a fake machine.
  python3 test_dynamic.py TOOL
"""
import json, os, subprocess, sys, tempfile, time
import tempfile as _tf
BASE = os.environ.get("DINGO_TEST_BASE") or _tf.mkdtemp(prefix="dingo-selftest-")

TOOL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin/dingo-pack"))
MB, GB = 1 << 20, 1 << 30
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"  [{detail}]", flush=True)


def machine(avail, total=4 * GB, cpus=10, pin=None):
    d = tempfile.mkdtemp(prefix="dyn-", dir=BASE)
    open(f"{d}/meminfo", "w").write(f"MemTotal: {total // 1024} kB\nMemAvailable: {avail // 1024} kB\n")
    open(f"{d}/config.json", "w").write(json.dumps({"slots": pin} if pin else {}))
    env = dict(os.environ, DINGO_HOME=f"{d}/state", DINGO_FAKE_MEMINFO=f"{d}/meminfo", DINGO_CONFIG=f"{d}/config.json",
               DINGO_CPU_SLOTS=str(cpus), DINGO_RESERVE="256M", DINGO_MEM_POLL_S="0.2", DINGO_PEAK_AFTER_S="999")
    for k in ("DINGO_SLOTS",):
        env.pop(k, None)
    return d, env


def job(env, d, name, mem, secs):
    sh = f"echo start $(date +%s.%N) >> {d}/{name}; sleep {secs}; echo end $(date +%s.%N) >> {d}/{name}"
    return subprocess.Popen([sys.executable, TOOL, "run", "--heavy", "--mem", mem, "--threads", "1", "--session", name,
                             "--", "bash", "-c", sh], env=env, stderr=subprocess.DEVNULL)


def spans(d, names):
    out = {}
    for n in names:
        try:
            ls = dict(l.split() for l in open(f"{d}/{n}"))
            out[n] = (float(ls["start"]), float(ls.get("end", "inf")))
        except (OSError, KeyError, ValueError):
            out[n] = None
    return out


def max_concurrent(sp):
    ev = sorted([(a, 1) for a, b in sp.values()] + [(b, -1) for a, b in sp.values()], key=lambda x: (x[0], x[1]))
    c = m = 0
    for _, k in ev:
        c += k
        m = max(m, c)
    return m


# 1. not pinned, memory plentiful: 8 small jobs all run at once (a fixed 6 would hold back 2)
d, env = machine(avail=3 * GB, cpus=10)
names = [f"s{i}" for i in range(8)]
ps = [job(env, d, n, "100M", 3) for n in names]
time.sleep(1.5)
who = subprocess.run([sys.executable, TOOL, "who", "--json"], env=env, capture_output=True, text=True).stdout
for p in ps:
    p.wait()
sp = spans(d, names)
check("1 not pinned: 8 small jobs, 10 CPU slots, enough memory -> all 8 at once", all(sp.values()) and max_concurrent(sp) == 8,
      (max_concurrent(sp) if all(sp.values()) else sp))
info = json.loads(who)
check("1b who --json carries capacity with its basis", "capacity" in info and "no fixed slot count" in info["capacity"]["basis"]
      and info["lanes"]["heavy"]["n"] == 10, info.get("capacity"))

# 2. memory bound: 6 jobs of 400M with 2G available, 256M reserve -> at most 4 at a time
d, env = machine(avail=2 * GB, cpus=10)
names = [f"m{i}" for i in range(6)]
ps = [job(env, d, n, "400M", 3) for n in names]
for p in ps:
    p.wait()
sp = spans(d, names)
mc = max_concurrent(sp) if all(sp.values()) else None
check("2 memory decides: 6 x 400M on 2G - 256M reserve -> at most 4 at once, all finish", mc is not None and 1 <= mc <= 4
      and all(p.returncode == 0 for p in ps), (mc, [p.returncode for p in ps]))

# 3. bad input: a job declaring more than what is free must queue, not go in; it goes once the other ends
d, env = machine(avail=2 * GB, cpus=10)
a = job(env, d, "A", "1G", 4)
time.sleep(1.0)
b = job(env, d, "B", "1.5G", 1)        # 2G - 1G promised = 1G < 1.5G + 256M -> must wait
time.sleep(1.5)
who = subprocess.run([sys.executable, TOOL, "who"], env=env, capture_output=True, text=True).stdout
mid = spans(d, ["B"])["B"]
check("3a job declaring more than available - promised - reserve is NOT started", mid is None, mid)
check("3b who shows it queued / waiting for memory and the room line", ("waiting for memory" in who or "queue" in who)
      and "room now:" in who, who)
a.wait(); b.wait()
sp = spans(d, ["A", "B"])
check("3c it starts only after the other ended", all(sp.values()) and sp["B"][0] >= sp["A"][1] - 0.05, sp)

# 4. CPU slots still count: 4 jobs of --threads 1 on 2 CPU slots -> at most 2 at once
d, env = machine(avail=3 * GB, cpus=2)
names = [f"c{i}" for i in range(4)]
ps = [job(env, d, n, "100M", 2) for n in names]
for p in ps:
    p.wait()
sp = spans(d, names)
check("4 CPU slots = cores: 4 jobs on 2 CPU slots -> at most 2 at once", all(sp.values()) and max_concurrent(sp) <= 2,
      sp)

# 5. pinning still works: slots 3 in config -> at most 3 at once even with room for more
d, env = machine(avail=3 * GB, cpus=10, pin=3)
names = [f"p{i}" for i in range(5)]
ps = [job(env, d, n, "100M", 2) for n in names]
time.sleep(1.0)
who = subprocess.run([sys.executable, TOOL, "who"], env=env, capture_output=True, text=True).stdout
for p in ps:
    p.wait()
sp = spans(d, names)
check("5 pinned slots 3 -> at most 3 at once; who says pinned", all(sp.values()) and max_concurrent(sp) <= 3
      and "slots pinned at 3" in who, (sp, who))

# 6. reserve is adjustable: same 2G, reserve 1.5G -> a 600M job must wait forever (timeout 3 s -> exit 75)
d, env = machine(avail=2 * GB, cpus=10)
env["DINGO_RESERVE"] = "1.5G"
r = subprocess.run([sys.executable, TOOL, "run", "--heavy", "--mem", "600M", "--threads", "1", "--timeout", "3s", "--", "true"],
                   env=env, capture_output=True, text=True)
check("6 reserve 1.5G leaves no room for 600M -> queues, gives up at --timeout (exit 75)", r.returncode == 75, (r.returncode, r.stderr))
print(f"{sum(res)}/{len(res)} passed")
