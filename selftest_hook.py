#!/usr/bin/env python3
import json, os, subprocess, sys
import tempfile as _tf
BASE = os.environ.get("DINGO_TEST_BASE") or _tf.mkdtemp(prefix="dingo-selftest-")
H = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks/hint.py"))
cases = [  # (command, expected: deny / hint / silent)
 ("python run_all.py", "deny"),
 ("cd /mnt/g/Agents/RoboSynChallenge/work && python3 scripts/run_all.py --all", "deny"),
 ("python3 tools/记录.py --x", "silent"),
 ("ls -la; git status --short -- a.md", "silent"),
 ("pytest -q tests/", "deny"),
 ("python -m pytest -x", "deny"),
 ("timeout 600 python3 train.py", "deny"),
 ("nohup python3 eval_policy.py > log 2>&1 &", "deny"),
 ("bash -c 'cd x && python run_all.py'", "deny"),
 ("uv run pytest", "deny"),
 ("conda run -n robo python export_onnx.py", "deny"),
 ("./run_all.sh", "deny"),
 ("bash scripts/regress.sh", "deny"),
 ("mineru -p a.pdf -o out", "deny"),
 ("dingo-pack run --heavy --mem 4G -- python run_all.py", "silent"),
 ("dingo-pack run --heavy --mem 4G -- bash -c 'cd x && python run_all.py'", "silent"),
 ("dingo-pack who; python run_all.py", "deny"),
 ("python3 ~/.local/bin/dingo-pack run --heavy -- pytest", "silent"),
 ("echo 'python run_all.py'", "silent"),
 ("cp -r /mnt/g/Agents/big ~/x", "deny"),
 ("cp /mnt/g/Work/a.md ~/a.md", "hint"),
 ("grep -rn foo /mnt/g/Agents/RoboSynChallenge", "deny"),
 ("grep -n foo /mnt/g/Work/head-quarter/AGENTS.md", "silent"),
 ("tar -cf /mnt/g/x.tar ~/data", "deny"),
 ("dingo-pack run --io -- tar -cf /mnt/g/x.tar ~/data", "silent"),
 ("find /mnt/g/Work -maxdepth 1 -name '*.md'", "hint"),
 ("make -j8", "deny"),
]
bad = 0
for enforce in ("1", "0"):
    env = dict(os.environ, DINGO_ENFORCE=enforce, DINGO_HOME=os.path.join(BASE, "dh"))
    for cmd, want in cases:
        r = subprocess.run([sys.executable, H], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                           capture_output=True, text=True, env=env)
        o = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
        got = "deny" if o.get("permissionDecision") == "deny" else "hint" if o.get("additionalContext") else "silent"
        exp = want if enforce == "1" else ("hint" if want == "deny" else want)
        ok = got == exp and r.returncode == 0
        bad += not ok
        print(("PASS" if ok else "FAIL"), f"enforce={enforce}", f"want={exp:<6} got={got:<6}", cmd)
    # Agent tool never denied
# 开-013 E: a fake 9p mount (DINGO_FAKE_MOUNTS) so the scan-size and cd rules do not depend on this machine.
#   net/one.md (a file), net/small/ (5 files), net/big/ (40 dirs x 20 files = 840 entries)
NET = os.path.join(BASE, "net")
os.makedirs(os.path.join(NET, "small"), exist_ok=True)
for i in range(5):
    open(os.path.join(NET, "small", f"f{i}.md"), "w").close()
for d in range(40):
    os.makedirs(os.path.join(NET, "big", f"d{d}"), exist_ok=True)
    for i in range(20):
        open(os.path.join(NET, "big", f"d{d}", f"f{i}"), "w").close()
open(os.path.join(NET, "one.md"), "w").write("foo\n")
LOCAL = os.path.join(BASE, "local")
os.makedirs(LOCAL, exist_ok=True)
mounts = os.path.join(BASE, "mounts")
open(mounts, "w").write(f"C:\\ {NET} 9p rw 0 0\n")
cases13 = [  # (command, expected with enforcement, cwd)
 (f"rg -n foo {NET}/one.md", "silent", LOCAL),                         # E1 rg on one file: allowed (was deny)
 (f"rg --files {NET}/small", "silent", LOCAL),                         # E2 rg on a small directory: allowed (was deny)
 (f"rg -n foo {NET}/big", "deny", LOCAL),                              # E3 rg over a big tree: still refused
 (f"rg -n -e foo -g '*.md' {NET}/small {NET}/one.md", "silent", LOCAL),  # E4 -e / -g values are not paths
 (f"rg foo", "deny", os.path.join(NET, "big")),                        # E5 rg with no path = cwd, a big tree
 (f"rg --max-depth 1 foo {NET}/big", "silent", LOCAL),                 # E6 one level only
 (f"grep -rn foo {NET}/one.md", "silent", LOCAL),                      # E7 grep -r on one file (was deny)
 (f"grep -rn foo {NET}/small", "silent", LOCAL),                       # E8 grep -r on a small directory (was deny)
 (f"grep -rn foo {NET}/big", "deny", LOCAL),                           # E9 grep -r over a big tree: still refused
 ("bash -lc 'python3 train.py'", "deny", LOCAL),                       # E10 -c inside an option cluster (was allowed)
 ("bash -euxc 'make -j8'", "deny", LOCAL),                             # E11 (was allowed)
 ("sh -c 'pytest -q'", "deny", LOCAL),                                 # E12
 ("bash -o pipefail -c 'python run_all.py | tee log'", "deny", LOCAL), # E13
 ("env -u X bash -lc 'pytest'", "deny", LOCAL),                        # E14 env + bash -lc (was allowed)
 ("env -i PATH=/usr/bin python3 train.py", "deny", LOCAL),             # E15
 ("command python3 run_all.py", "deny", LOCAL),                        # E16 (was allowed)
 ("bash -lc 'ls -la'", "silent", LOCAL),                               # E17 a light -c string stays allowed
 ("dingo-pack run --heavy --mem 1G -- bash -lc 'pytest'", "silent", LOCAL),  # E18 already wrapped
 (f"cd {LOCAL} && cp -a big2 copy", "silent", os.path.join(NET, "big")),  # E19 cd to local disk first (was deny)
 (f"cd {LOCAL} && cp -a {NET}/big copy", "deny", NET),                 # E20 ... but an absolute /mnt source still counts
 (f"(cd {LOCAL}) && cp -a big copy", "deny", NET),                     # E21 a subshell's cd does not leak
 (f"bash -c 'cd {LOCAL} && cp -a x y'", "silent", NET),                # E22 cd inside bash -c
]
env13 = dict(os.environ, DINGO_ENFORCE="1", DINGO_HOME=os.path.join(BASE, "dh"), DINGO_FAKE_MOUNTS=mounts)
for cmd, want, cwd in cases13:
    r = subprocess.run([sys.executable, H], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, env=env13, cwd=cwd)
    o = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
    got = "deny" if o.get("permissionDecision") == "deny" else "hint" if o.get("additionalContext") else "silent"
    ok = got == want and r.returncode == 0
    bad += not ok
    print(("PASS" if ok else "FAIL"), "013", f"want={want:<6} got={got:<6}", f"[cwd {os.path.relpath(cwd, BASE)}]", cmd)
gone = os.path.join(BASE, "gone")          # 013: the caller's cwd was deleted (os.getcwd() raises) -> still judged
os.makedirs(gone, exist_ok=True)
r = subprocess.run(["sh", "-c", f'cd "{gone}" && rmdir "{gone}" && exec "{sys.executable}" "{H}"'],
                   input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}),
                   capture_output=True, text=True, env=env13)
o = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
ok = o.get("permissionDecision") == "deny"
bad += not ok
print(("PASS" if ok else "FAIL"), "013 deleted cwd: pytest still denied")
env = dict(os.environ, DINGO_ENFORCE="1", DINGO_HOME=os.path.join(BASE, "dh"))
r = subprocess.run([sys.executable, H], input=json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "x"}}), capture_output=True, text=True, env=env)
o = json.loads(r.stdout)["hookSpecificOutput"]; ok = "permissionDecision" not in o and o.get("additionalContext")
bad += not ok; print("PASS" if ok else "FAIL", "Agent tool: hint only, never denied")
r = subprocess.run([sys.executable, H], input="garbage", capture_output=True, text=True, env=env)
ok = r.returncode == 0 and r.stdout == ""; bad += not ok; print("PASS" if ok else "FAIL", "bad input -> silent, exit 0")
n_all = 2 * len(cases) + len(cases13) + 3
print(f"{n_all-bad}/{n_all} passed")
sys.exit(1 if bad else 0)
