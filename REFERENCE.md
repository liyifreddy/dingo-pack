# dingo-pack reference

The details behind the [README](README.md).

## Features

| | |
|---|---|
| **Slots** | At most N heavy jobs run at once (default 6). The rest queue, roughly first come first served (the kernel wakes flock waiters, so the order is not strictly guaranteed). |
| **Memory admission** | A job starts only if free memory covers its estimate + a reserve, after subtracting what already-running jobs have promised but not yet used. |
| **Limits** | Each job runs under a per-process limit (`RLIMIT_DATA`) and a whole-process-tree limit. Over the limit, that job fails by itself; the machine does not. |
| **Roster** | Snapshots the live Claude Code sessions (name, directory, state, last status line) as turns happen, at most once per 15 s. After a crash: `list` shows what was lost, `resume` reopens it in terminal tabs. |

## Commands added in 0.2

| Command | What it does |
|---|---|
| `dingo-pack` | One screen: memory, promised memory, heavy / io slots and who holds them, live sessions and their state, lost sessions. |
| `run --io -- CMD` | Like `run --heavy`, in a separate lane (`DINGO_IO_SLOTS`, default 1; default `--mem` 512M). For big copies, archives and scans on network / 9p disks. Memory admission counts both lanes. |
| `who [--json]` | Slot holders per lane, the queue, memory. `--json` adds `promised` and `suggest_parallel` = min(free heavy slots, ⌊(free − reserve − promised) / default mem⌋). |
| `top [--watch S]` | Memory (PSS) of each live session's whole process tree, the ghost total, and the five biggest other processes (0.4: plus the task view below). |
| `doctor [--json] [--no-git]` | Memory, OOM / page-allocation / 9p lines in `dmesg` from the last 24 h, each network mount probed with a 3 s limit, `.wslconfig` and `vm.min_free_kbytes` on WSL, slots held by leftovers, lost sessions, ghosts, and `wombat-gate doctor` for each live session's repository. Exit 1 if something is bad. Never changes anything. |
| `ghosts [--json]` | Process trees whose owning Claude Code session is gone. Owner = the `CLAUDE_PID` in the process environment; alive = registry pid + start time match, or (no registry entry) the pid exists. Your own ancestors are never ghosts. |
| `ghosts --reap [#k...] [--yes]` | SIGTERM, then SIGKILL after 10 s, for the chosen groups; each pid checked again (start time) right before. Groups kept on purpose (nohup / setsid / tmux / session said waiting) are skipped unless named. Refused inside an agent. One log line per process. |
| `close-all [--dry-run]` | Everyday name for `stow --close` (same rules). |
| `restore [NAME...]` | Everyday name: what any recent `close-all` / `stow --close` closed (`unstow`), or, if nothing was closed that way, the sessions lost in the last 24 h (`resume --lost`). |
| `stow [--close] [--dry-run]` (advanced) | Saves a stow list of every live session. `--close` sends SIGTERM to sessions that are idle *and* self-reported `waiting` or `done` and not the caller, re-reading each one's live state right before; waits 10 s (`DINGO_STOW_WAIT_S`). Refused inside an agent. |
| `unstow [NAME...] [--all]` (advanced) | Reopens the sessions any stow in the last 7 days closed and that have not come back since (`--all` adds the others from the newest stow). Uses `unstow_prompt` from the project settings. |

CPU slots (0.2): the machine has one CPU slot per core (`DINGO_CPU_SLOTS` / `cpu_slots`).
`run --heavy` takes `--threads` of them (default 8; io jobs 1, leases 2) and exports
`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`,
`NUMEXPR_MAX_THREADS`, `VECLIB_MAXIMUM_THREADS`, `BLIS_NUM_THREADS` = N to the job. Admission
needs both memory and CPU slots. `doctor` rates the 1-minute load per core (> 1.5 warn,
> 3 bad); the hint adds an overload warning above 1.5.

Queue (0.2): slots go first come first served; memory / CPU admission is serialised by one
lock. A job that fits may go ahead of one waiting for memory, at most 3 times
(`DINGO_BACKFILL_MAX`); after that everybody waits for it. The default estimate is 1G; after
60 s (`DINGO_PEAK_AFTER_S`) a running job is counted at 1.2 x its measured peak.

Board (0.3): `board [--json] [--html PATH] [--watch S] [--stale-h H]`. Per session: name,
state (self-report or guessed), box (`box\d+` in the status line), expected finish, flags,
last activity, start time, status line. The write time of a status line is the first time
within 20 characters after 状态 (e.g. `状态（10-05 14:30 柏林）`, `【状态 柏林 18:06】`;
`19:3x` = 19:30); otherwise when dingo-pack first saw it. The expected finish is the first
time within 40 characters after 预计 / 约 … 完 / ETA / 跑完 / 完成于 (a `＝柏林 HH:MM` there
wins), or `HH:MM 前`; earlier than the write time means the next day. "ask now": expected
finish passed and the session did not say done. "may have stopped": status line unchanged
for `--stale-h` hours (default 3). Heading-style status lines (`## 状态行（…）`) take the
next line as their text.

Team routines (0.3, all deterministic, all optional):

| Command | What it does |
|---|---|
| `name L WHAT [--suffix S]` | Next session name for letter L today: `L-MMDD-WHAT`, then `L2-…`, `L3-…` (from the roster). Exit 3 and a warning if a session of that letter is alive (one per letter at a time). |
| `card new TITLE [--registry FILE] [--out FILE]` | Next free number = 1 + the highest `0-NNN` found in the registry file (only read) and in this machine's reservations; reserves it here and writes a skeleton (fixed block + ten items). Never overwrites a file. Project settings: `card_registry`, `card_number_regex`, `card_prefix`, `card_template`. |
| `lint [--json]` | Findings: `name` (not `L[n]-MMDD-what`), `letter` (two of one letter alive), `stale` / `overdue` (from the board), `no-status` (project has a log pattern but no status line found). Each can be switched off: `"lint": {"letter": false}` in the config. Exit 1 if anything is found. |
| `queue add --when C [--when C2] --to WHO --prompt TEXT` · `queue list` · `queue show ID [--clip]` · `queue sent ID` | Prompts waiting for conditions: `mark:NAME=done` (self-reported), `file:PATH`, `at:HH:MM`, `alive:NAME`, `gone:NAME`. `list` marks the ready ones; `show --clip` copies to the Windows clipboard; `sent` archives. Nothing is sent by dingo-pack. |
| `claim list / check / add / wait / sweep / remove --file CLAIM_boxNN.txt` | The box occupancy file (run on the box: `python3 dingo-pack claim …`). dingo writes `who · task · cards · 预计完成 柏林 MM-DD HH:MM · 完成标志 PATH · 写于 柏林 MM-DD HH:MM` (`add --until … --done FLAG`) and also reads hand-written lines: cards from `GPU n` (`不占卡` = none), the end = the latest time after an end word (`预计`, `完`, `≈`, `ETA`), box times converted (`box` = Asia/Shanghai), `预计约 N h` after a `起` time. States: live · done (the flag exists: the next claim command on that box removes the line, into `<file>.released`) · overdue (past end + `--grace` 30 min, cards still computing: still blocks) · stale (past end + grace, no compute process on its cards: reported, written once to `runs/claim_stale_<box>.alert` for the box watchdog, never deleted) · open (no end found: blocks). `check` / `add`: exit 3 if a live line is in the way, 4 if only stale ones are (`--take-stale` treats them as free, only if your card says so). `wait`: removes done lines as their flags appear, waits for live ones, reports stale ones once (`--stop-on-stale`: exit 4), `--timeout` exit 75. `remove [--task T]`: the caller's lines only. |

Task manager and boxes (0.4; read only, no signals):

| Command | What it does |
|---|---|
| `top [--all] [--json] [--watch S]` | Memory as before, then per session: its children older than 60 s (command, age, CPU over 1 s, PSS; `--all` for every one), the waiting loops it runs and what each waits for (a file / a string in a file / a process / a tmux session / on which box), spin flags, tokens today and the last turn's input. Spin: `until ! pgrep -f X` / `while pgrep -f X` whose own shell matches X (never ends); a local wait older than `DINGO_SPIN_H` (3 h) whose files never changed; a waited-on sub-agent output untouched for `DINGO_AGENT_OUT_H` (1 h). "Context long" when the last input is over `DINGO_CTX_WARN` (600000). |
| `tokens [--day YYYY-MM-DD] [--json]` | Tokens per session for a day from the transcripts (input, cache write, cache read, output, turns, sub-agents, last input); each message counted once. Read incrementally, cached in `tokens.json`. |
| `boxes [BOX…] [--no-du] [--every S] [--json]` | One read-only ssh per box (this file is piped to `python3 -`; nothing is installed or written there): up with cards / up without cards / fewer cards than expected / unreachable, cards actual / expected, who is on it (claim lines with their state, cards computing without a line), data disk used / total, biggest top-level directories (`du`, up to 90 s, on the box), watchdog heartbeat, the last boot-failure record. Boxes and their expected card counts: `boxes.json` (`$DINGO_BOXES` or `~/.config/dingo-pack/boxes.json`: `{"boxes": [{"name": "box22", "gpus": 4, "data": "/root/gpufree-data"}]}`), else the `box*` hosts in `~/.ssh/config`. A change into or out of a bad state is logged to `boxes.log.jsonl`; `board` shows the last read. |
| `boxes note BOX TEXT` · `boxes history [BOX]` | Write down what dingo cannot see (a failed start in the provider's console); list a box's records, automatic and written. |
| `adapter test [--agents …]` | Check agent adapters against this machine (see ADAPTERS.md). `DINGO_AGENTS=claude-code,/path/adapter.py` makes `top`, `tokens` and `board` see other agents too. |

`board` also flags `spin?`, `context long` and `time N min ahead` (a status line whose own time is later than when it was written: read `date` first), and lists each session's waits; `lint` reports the last as `future-time`.

"Inside an agent" means any of `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_CODE_SESSION_ID`,
`CLAUDE_CODE_ENTRYPOINT`, `WOMBAT_NO_ESCAPES`, `DINGO_NO_ESCAPES` is set to something
other than empty or `0`.

The hint hook (`hooks/hint.py`) adds, before sub-agents, review skills, heavy commands
and bulk I/O on network mounts: slots per lane, free memory after promised, the ghost
count (cached by `save` in `ghosts.json`), who holds the slots, and the
`suggest_parallel` advice ("No room right now" when it is 0).

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

- `--mem` is the expected peak (default 1G; configurable via default_mem / DINGO_DEFAULT_MEM). `--limit` is the per-process limit
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

