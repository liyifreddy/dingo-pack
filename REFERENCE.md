# dingo-pack reference

The details behind the [README](README.md).

## Features

| | |
|---|---|
| **Slots** | At most N heavy jobs run at once (default 6). The rest queue, roughly first come first served (the kernel wakes flock waiters, so the order is not strictly guaranteed). |
| **Memory admission** | A job starts only if free memory covers its estimate + a reserve, after subtracting what already-running jobs have promised but not yet used. |
| **Limits** | Each job runs under a per-process limit (`RLIMIT_DATA`) and a whole-process-tree limit. Over the limit, that job fails by itself; the machine does not. |
| **Roster** | Snapshots the live Claude Code sessions (name, directory, state, last status line) as turns happen, at most once per 15 s. After a crash: `list` shows what was lost, `resume` reopens it in terminal tabs. |

## Install notes

Single file, Python ≥ 3.8 standard library, Linux / macOS / WSL (needs `fcntl`; native
Windows is not supported, WSL is).

State lives in `~/.local/state/dingo-pack/` (override with `DINGO_HOME`). It must be on a
**local disk**: flock on network / 9p mounts is not reliable, and dingo-pack refuses to
start there.

## Heavy jobs

```bash
dingo-pack run --heavy --mem 6G -- python train.py      # wait for a slot + 6G free, then run
dingo-pack run --heavy --mem 2G --timeout 30m -- pytest  # give up (exit 75) after 30 min
dingo-pack who                                           # who holds slots, who is queuing
dingo-pack log -n 20                                     # one JSON line per job
```

- `--mem` is the expected peak (default 2G). `--limit` is the per-process limit
  (default 1.5 × `--mem`; `0` = none). `--tree-limit` caps the sum over the job's whole
  process tree (default = `--limit`): a program that forks 8 workers, each under the
  per-process limit, is still caught. Over the tree limit the tree gets SIGTERM, then
  SIGKILL after 10 s, and `run` exits 137 with a line saying so.
- Reserve: `max(1 GB, 10 % of RAM)` by default (`DINGO_RESERVE`). Slots: `DINGO_SLOTS` or
  `--slots`.
- The job inherits the slot lock: if `dingo-pack` itself is killed, the slot stays taken
  until the job is gone too. Nothing to clean up after a crash — the kernel drops flocks.

## Work that is not a shell command

An agent's own review pass or a fan-out of sub-agents runs inside the agent process, so
it cannot be wrapped. Take a **lease** instead:

```bash
dingo-pack lease --mem 3G --for 45m --note "code review"   # returns at once
dingo-pack release                                         # or let it expire
```

The lease ends on `release`, at `--for`, or when the agent process that took it exits
(found via `CLAUDE_PID`, or `DINGO_OWNER_PID`).

## Roster

```bash
dingo-pack save                       # snapshot now (the hooks do this as turns happen)
dingo-pack mark running "step 3"      # self-report: running (= midway) | waiting [--until T] | blocked | done
dingo-pack mark waiting --until "18:30" "nightly job on the build box"
dingo-pack list                       # after a crash: what was lost, grouped
dingo-pack resume --midway            # reopen everything that was mid-task, 4 tabs at a time
dingo-pack resume NAME... --via tmux  # or by name; --via wt | tmux | print
```

`list` groups the sessions that were alive at the last snapshot and are gone now:

- 🔴 midway — resume now · ⏰ waiting — resume when due (sorted by the first `HH:MM` in
  `--until`; no time last) · ⛔ blocked on a person · 💤 idle (no self-report) · ✅ done — no
  need to resume.
- A session that died **with the machine** (last seen before the current boot) is
  `ended reboot`; one closed while the machine was up is `ended closed` and hidden unless
  `--closed`.

`resume` runs `cd <dir> && claude --resume <id> "<prompt>"` in a new Windows Terminal tab
(WSL), a tmux window, or just prints the commands. It skips sessions that are already
running again and pauses after every batch of 4.

Hooks (`hooks/hooks.json`; add to your project's `.claude/settings.json` if you do not use
the plugin):

```json
"UserPromptSubmit": [{ "hooks": [{ "type": "command", "timeout": 5,
  "command": "\"$HOME/.local/bin/dingo-pack\" save --hook >/dev/null 2>&1 || true" }]}],
"Stop": [{ "hooks": [{ "type": "command", "timeout": 5,
  "command": "\"$HOME/.local/bin/dingo-pack\" save --hook >/dev/null 2>&1 || true" }]}]
```

The hook returns immediately; the save runs in a detached child (stopped after 30 s), at
most once per 15 s across all sessions, and never fails the agent's turn.

## Per-project settings

`<project>/.dingo-pack.json`, or under `"projects"` in `~/.config/dingo-pack/config.json`
keyed by the project's absolute path:

```json
{
  "log_glob": "log/*_{name}.md",
  "status_regex": "^Status:",
  "name_regex": "(?<![A-Za-z0-9])([A-Z][0-9]?-\\d{4}-[^\\s,:;]+)",
  "resume_prompt": "The machine crashed and was restarted. Re-read your log ..."
}
```

- Session name: a name you gave the session (`/rename`) wins; else the first match of
  `name_regex` in its first message; else Claude Code's automatic name.
- Status line: the last line matching `status_regex` in the newest file matching
  `log_glob` (`{name}` = session name).

## WSL notes

Running out of memory inside WSL does more than kill a process: the 9p file server that
serves `/mnt/c`, `/mnt/g`, … needs contiguous kernel memory, and when it cannot get it the
Windows drives start returning I/O errors. Symptoms and fixes:

```bash
dmesg -T | grep -iE "out of memory|oom-kill|9p|page allocation failure"   # what happened
free -g; cat /proc/sys/vm/min_free_kbytes
```

- `%UserProfile%\.wslconfig`: give WSL a fixed budget and swap, e.g.
  `[wsl2]` / `memory=24GB` / `swap=8GB`, then `wsl --shutdown`.
- Keep some memory free for the kernel: `vm.min_free_kbytes = 262144` in
  `/etc/sysctl.d/99-9p.conf`.
- WSL2 uses cgroup v1 here, so `systemd-run -p MemoryMax=…` has no effect; rlimits
  (`prlimit --data`, which is what `--limit` sets) do.

## Self-test in full

```bash
python3 selftest_dingo_pack.py --base /some/local/dir     # ~40 s
python3 selftest_dingo_pack.py --mutants                  # each mutant must turn a check red
```

Memory tests never stress the real machine: they run against a simulated one — a
supervisor writes a fake `/proc/meminfo` and plays OOM killer at 350 MB. The negative
control (same four jobs without dingo-pack) must hit the OOM killer; with dingo-pack all four
must finish.

## Limits in full

- Memory used by the agent process itself (and its in-process sub-agents) cannot be
  limited, only counted via leases.
- Processes not started through dingo-pack are not managed; admission only sees what
  `MemAvailable` sees.
- macOS: rlimits on data size are not enforced (only the tree limit works) and free
  memory is approximated from `vm_stat`.
- The roster reads Claude Code's session files (`~/.claude/sessions/*.json`), which are
  not a documented interface. If their shape changes, `save` falls back to transcripts
  only and says so.
- The roster never decides for a session; `resume` only opens it and pastes one line.

