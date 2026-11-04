# 🐕 dingo-pack

<p align="center">
  <img src="https://raw.githubusercontent.com/liyifreddy/dingo-pack/main/docs/img/dingo-pack-banner.jpg" width="100%"
       alt="Dingoes eating side by side at a trough with four compartments while three more wait their turn">
</p>

**Like a tab saver, for your Claude Code sessions.**

```sh
dingo-pack save          # remember the open sessions (the hooks also do it)
dingo-pack close-all     # remember them, close the idle ones that are waiting / done
dingo-pack restore       # bring the last batch back, one terminal tab each
```

🌐 **English** · [中文](https://github.com/liyifreddy/dingo-pack/blob/main/README.zh-CN.md)

The pack eats in order: dingoes share a kill without a scramble. dingo-pack is the pack
leader for a dozen coding agents on one machine — it also hands out slots for heavy work
and checks what is wrong when the machine struggles.

`restore` opens a new Windows Terminal tab per session when it runs in WSL, a tmux window
inside tmux, and otherwise prints the commands for you to paste. `close-all` only closes
sessions that are idle and said they are waiting or done, and it is for humans: inside an
agent it refuses.

## 🚀 Quick start

```sh
pipx install git+https://github.com/liyifreddy/dingo-pack

dingo-pack                                    # one screen: memory, slots, sessions
dingo-pack run --heavy --mem 4G -- pytest     # heavy work waits for a slot and free memory
dingo-pack doctor                             # what's wrong with this machine right now?
dingo-pack list                               # after a crash: which sessions were lost
dingo-pack resume --midway                    # reopen the ones that were mid-task
```

In Claude Code, install the plugin and the roster saves itself:

```
/plugin marketplace add liyifreddy/dingo-pack
/plugin install dingo-pack@dingo-pack
```

One file, Python ≥ 3.8, standard library only. Linux, macOS and WSL.

## Candidate release and installation demo

Release candidate source: **0.4.1** (0.4.0 functionality plus diagnostics and packaging fixes).
The public GitHub release is **0.1.1**. 0.4.1 is not deployed or published yet. It adds
`boxes`, `tokens`, adapter validation, and task/wait columns to the released CLI.
`watch`, `dispatch` and full three-table crosscheck remain design proposals.
See [CHANGELOG](CHANGELOG.md) and [release procedure](RELEASING.md).

From a reviewed checkout, use a separate environment to try the candidate:

```sh
python3 -m venv .venv-demo
.venv-demo/bin/python -m pip install .
.venv-demo/bin/dingo-pack --version
.venv-demo/bin/dingo-pack run --heavy --threads 1 --mem 64M -- python3 -c 'print("hello")'
```

Expected: `dingo-pack 0.4.1`, then `hello` (admission may wait). State and locks must
live on a local disk; leave `DINGO_HOME` at its default, outside a WSL mounted drive.
The GitHub quick-start command installs the public revision, not this local candidate.

## Platforms and agents

| Platform | Support boundary | Tested for this candidate (2026-11-04) |
|---|---|---|
| Linux | POSIX CLI and `/proc` monitoring | WSL Linux kernel tested; separate native Linux host not tested |
| WSL2 | Run Python and the CLI inside WSL; state on the Linux disk | Tested: full suite, bounded CSV and install smoke |
| macOS | POSIX CLI, `ps`/`vm_stat` fallbacks; Linux-only probes may be unknown | Not tested; portable CI job added, not yet run |
| Native Windows | Unsupported: requires `fcntl`, POSIX signals and resource limits; use WSL2 | Tested with native Windows Python 3.14: actionable unsupported-platform message, exit 2 |

All three agents can use the same `run` queue on a supported host. No tool-specific
hook is required to wrap a shell command. Configuration examples below are manual
instructions, not enforcement hooks.

- **Claude Code:** plugin installation above supplies hooks; see [REFERENCE](REFERENCE.md)
  for manual hook configuration. The bundled `skills/dingo-pack/SKILL.md` supplies guidance.
- **Codex:** add the following rule to the project's `AGENTS.md`. Optionally copy
  `skills/dingo-pack/SKILL.md` into `.agents/skills/dingo-pack/SKILL.md` in that project.
- **OpenCode Go:** when using OpenCode with Go, put the same rule in the project's
  `AGENTS.md`. Alternatively reference a rule file in `opencode.json`:
  `{"$schema":"https://opencode.ai/config.json","instructions":["docs/heavy-work.md"]}`.
  No provider credentials are needed by dingo-pack.

```markdown
Before heavy shell work, run `dingo-pack who`, then wrap the command:
`dingo-pack run --heavy --mem <estimated peak> -- <command>`.
Use the same local DINGO_HOME for all agents sharing the machine.
Keep the queue and memory limits in force; report failures with the diagnostic line.
```

Claude Code has a built-in session/token adapter. Codex and OpenCode can share the
queue, but automatic session discovery, token counts, resume and close are not
provided for them. External read-only adapters can extend `top`/`tokens`/task columns;
[ADAPTERS](ADAPTERS.md) defines that interface. Roster and ghosts are still Claude-specific.
Agent UI sessions were not launched for this candidate's tests.

Sources: [Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[skills](https://learn.chatgpt.com/docs/build-skills),
[OpenCode rules](https://docs.opencode.ai/docs/rules/).

## Reading memory failures

On a failed job, the CLI preserves its exit status and prints configured per-process
and tree limits, sampled RSS, and the exit signal. `SIGSEGV`/139 and `SIGKILL`/137 with
a configured cap mean **possible memory limit hit**, not proof of OOM. A tree-limit
stop is confirmed by the supervisor. An ordinary nonzero exit keeps its cause unknown;
read the child's `MemoryError`, parser error or application error too.

Without configuration, defaults are `--mem 1G`, per-process `--limit 1.5G`, and matching tree limit.
`default_mem` / `DINGO_DEFAULT_MEM` overrides the estimate. RLIMIT_DATA
and RLIMIT_AS are not RSS: failed allocations and brief peaks can be missed by RSS
sampling. For large CSVs, prefer chunked reading; if more memory is needed, check
available memory and raise the estimate/caps together. Input file size alone does not
predict peak memory. `python3 selftest_release.py --large-csv` exercises a 670 MiB
synthetic CSV under bounded memory without loading gigabytes into RAM.

## 😩 The problem

- 💥 **Heavy jobs pile up.** Test runs, model loads, builds and reviews from several
  sessions start at once. In our 16 GB WSL VM, 13 sessions already used 4.4 GB; then the
  kernel killed a 7.5–11 GB Python job, and work in flight died with it.
- 🪟 **WSL drops its Windows drives.** When memory runs out, `/mnt/c`, `/mnt/g`, … start
  returning I/O errors until remounted — for every session at once.
- 👻 **Closed sessions leave things running.** Their python, node and MCP servers keep
  eating memory until the next big job tips the machine over.
- 🧠 **After a crash, who was running?** Our laptop went down with 8 sessions open. We
  pieced the list together from memory.

## ✨ Five things

- ✅ 🎟️ **Slots and memory.** `run --heavy` waits for a free slot (6 by default) *and*
  enough free memory, then runs the job under a memory limit for the whole process tree.
  On a simulated 350 MB machine, four 150 MB jobs started together hit the OOM killer;
  through dingo-pack, all four finish.
- ✅ 🚚 **A lane for slow disks.** `run --io` gives big copies, archives and scans on a
  network or 9p drive their own queue (1 at a time), apart from CPU work.
- ✅ 📋 **Roster: close-all, restore, recall.** Before you shut down, `close-all` notes every
  session and closes the ones that are idle and waiting or done; `restore` brings them
  back. After a crash `restore` works too: it brings back what the crash lost.
- ✅ 👻 **Ghosts and sub-agent hints.** `ghosts` finds processes whose session is gone.
  The hint hook tells an agent, before it starts sub-agents or heavy commands, how many
  slots are free, how much memory is left, and how many jobs it should run in parallel.
- ✅ 🩺 **One-stop checkup.** `doctor` reads memory, recent OOM kills, whether each
  network drive still answers, your WSL settings, stuck slots, lost sessions, ghosts and
  git lock files (through [wombat-gate](https://github.com/liyifreddy/wombat-gate)). It
  only reports; it never fixes anything for you.

```
 heavy 0  [###### pytest  (est 2G) ######]
 heavy 1  [######### lease: code review #########]
 io    0  [## tar of the dataset ##]
 queue    C ──> D ──> E      next in line waits for a free slot AND enough memory
```

## 🎟️ Heavy work and slow disks

```sh
dingo-pack run --heavy --mem 6G -- python train.py       # wait for a slot + 6 GB, then run
dingo-pack run --heavy --mem 2G --timeout 30m -- pytest   # give up after 30 min (exit 75)
dingo-pack run --io -- tar -cf /mnt/g/backup.tar data/    # big copy on a slow disk: its own lane
dingo-pack who                                            # who holds the slots, who is waiting
dingo-pack top                                            # memory by session
```

- `--threads N` (default 8) sets `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`
  and friends for the job and takes N of the machine's CPU slots (one per core). `doctor`
  and the hint warn when the load is above 1.5 per core.
- `--mem` is your best guess at the peak. The limit defaults to 1.5 × that; go over it
  and only your job stops (a single process gets a MemoryError; a whole tree over the
  limit is stopped with exit 137), not the machine.
- Work that isn't a shell command — an agent's own review, a batch of sub-agents — takes
  a lease: `dingo-pack lease --mem 3G --for 45m`, then `dingo-pack release`. The lease
  also ends when its agent exits.

## 📋 The roster

<p align="center">
  <img src="https://raw.githubusercontent.com/liyifreddy/dingo-pack/main/docs/img/dingo-pack-roster-captioned.jpg" width="100%"
       alt="Three panels: at night the pack leader keeps a roster by the campfires; a storm scatters the pack while the leader shelters with the roster; at dawn the leader whistles and the pack comes back to the fires">
</p>

1. **Save** — sessions are snapshotted as they work.
2. **The machine goes down** — the roster is already on disk.
3. **Resume** — `list` shows who was lost; `resume` calls them back.

<p align="center"><sub>Illustrations generated with Google Gemini and edited by the author.</sub></p>

```sh
dingo-pack mark running "step 3"           # say what you're doing: running | waiting | blocked | done
dingo-pack mark waiting --until 18:30 "nightly job"
dingo-pack close-all                    # before shutting down: note everyone, close the idle waiting / done ones
dingo-pack restore                         # bring back what close-all closed
dingo-pack resume NAME... --via tmux       # by name; tabs via Windows Terminal, tmux, or just print
```

`close-all` never touches a session that is busy, running, blocked, or the one you
type it in, and it checks each session again right before closing it. It is for humans:
inside an agent it refuses.

## 👻 Ghosts

```sh
dingo-pack ghosts                 # process trees whose Claude Code session is gone
dingo-pack ghosts --reap          # stop them, after asking (humans only)
```

A process belongs to the session whose `CLAUDE_PID` it carries. Jobs left on purpose
(nohup, setsid, tmux, or a session that said it is waiting) are listed but kept.

## 📊 Board

```sh
dingo-pack board                  # one line per session: state, box, expected finish, flags
dingo-pack board --html board.html --watch 30   # a page that refreshes itself
```

It reads each session's last status line. "ask now" means the expected finish it wrote
has passed; "may have stopped" means the status line has not changed for 3 hours
(`--stale-h`). It only understands an expected time after a word like 预计 / 约 … 完 / ETA;
anything else is left blank rather than guessed.

## 🧰 Team routines (optional)

`name` (next session name for a letter), `card new` (next free card number + skeleton),
`lint` (naming, one session per letter, stale or overdue status lines), `queue` (prompts
waiting for a condition) and `claim` (the per-box occupancy file). All are deterministic
and off unless you call them — see the [reference](REFERENCE.md).

## ⚙️ More

The plugin brings the CLI, the roster hooks, the hint hook and a skill that teaches the
agent when to use a slot. Hook setup without the plugin, project settings, every option
and WSL memory tips are in the [reference](REFERENCE.md).

## ⚠️ Limits

- The agent process itself can't be limited, only counted with a lease.
- Only jobs started through dingo-pack are managed.
- Slots are handed out roughly first come, first served (the kernel decides who wakes first).
- macOS: the per-process limit isn't enforced (the tree limit is), and free memory is an
  estimate. `ghosts`, `top` and the WSL checks need Linux.
- The roster reads Claude Code's session files, which aren't a documented interface. If
  they change, it falls back to transcripts and says so.

## ✅ Self-test

```sh
python3 selftest_dingo_pack.py --base /some/local/dir    # 1–3 min, depending on load
python3 selftest_dingo_pack.py --mutants                 # each planted bug must turn a check red
```

Memory tests run on a simulated machine, never on your real memory.

## 📄 License

MIT. Sister tool: [wombat-gate](https://github.com/liyifreddy/wombat-gate), a git commit
queue for agents sharing one repo.
