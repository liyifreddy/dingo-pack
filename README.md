# 🐕 dingo-pack

<p align="center">
  <img src="https://raw.githubusercontent.com/liyifreddy/dingo-pack/main/docs/img/dingo-pack-banner.jpg" width="100%"
       alt="Dingoes eating side by side at a trough with four compartments while three more wait their turn">
</p>

**The pack eats in order.** Dingoes share a kill without a scramble. dingo-pack gets a
dozen coding agents on one machine to share it the same way — and calls them back after
a crash.

🌐 **English** · [中文](https://github.com/liyifreddy/dingo-pack/blob/main/README.zh-CN.md)

## 🚀 Quick start

```sh
pipx install git+https://github.com/liyifreddy/dingo-pack

dingo-pack run --heavy --mem 4G -- pytest     # heavy work waits for a slot and free memory
dingo-pack who                                # who holds the slots
dingo-pack list                               # after a crash: which sessions were lost
dingo-pack resume --midway                    # reopen the ones that were mid-task
```

In Claude Code, install the plugin and the roster saves itself:

```
/plugin marketplace add liyifreddy/dingo-pack
/plugin install dingo-pack@dingo-pack
```

## 😩 The problem

- 💥 **Heavy jobs pile up.** Test runs, model loads, builds and reviews from several
  sessions start at once. In our 16 GB WSL VM, 13 sessions already used 4.4 GB; then the
  kernel killed a 7.5–11 GB Python job, and work in flight died with it.
- 🪟 **WSL drops its Windows drives.** When memory runs out, `/mnt/c`, `/mnt/g`, … start
  returning I/O errors until remounted — for every session at once.
- 🧠 **After a crash, who was running?** Our laptop went down with 8 sessions open. We
  pieced the list together from memory.

## ✨ What you get

- ✅ **Slots for heavy work.** `run --heavy` waits for a free slot (6 by default) *and*
  enough free memory, then runs the job under a memory limit. On a simulated 350 MB
  machine, four 150 MB jobs started together hit the OOM killer; through dingo-pack, all
  four finish.
- ✅ **A limit on the whole job.** A job that forks workers is measured as one tree, so
  four 75 MB workers under a 200 MB limit get stopped — a per-process limit misses that.
- ✅ **A roster you can come back to.** Hooks snapshot the live Claude Code sessions as
  they work (a save takes 0.08 s). After a crash, `list` shows who was lost and `resume`
  reopens them in terminal tabs, four at a time.
- ✅ **Hints, never blocks.** An optional hook tells the agent when it is about to start
  heavy work outside a slot, and who holds the slots right now.

```
 slot 0  [###### pytest  (est 2G) ######]
 slot 1  [## model load (est 6G) ##]
 slot 2  [######### lease: code review #########]
 queue   C ──> D ──> E      next in line waits for a free slot AND enough memory
```

## 🎟️ Heavy jobs

```sh
dingo-pack run --heavy --mem 6G -- python train.py       # wait for a slot + 6 GB, then run
dingo-pack run --heavy --mem 2G --timeout 30m -- pytest   # give up after 30 min (exit 75)
dingo-pack log -n 20                                      # one line per job
```

- `--mem` is your best guess at the peak. The limit defaults to 1.5 × that; go over it
  and only your job stops (exit 137), not the machine.
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
dingo-pack resume NAME... --via tmux       # by name; tabs via Windows Terminal, tmux, or just print
```

`list` sorts lost sessions into midway, waiting (earliest time first), blocked, idle and
done. Each reopened session gets a first message: check your log, then pick up where you
left off.

## ⚙️ More

The plugin brings the CLI, the roster hooks, the hint hook and a skill that teaches the
agent when to use a slot. Hook setup without the plugin, project settings (log file and
status line for `list`), every option and WSL memory tips are in the
[reference](REFERENCE.md).

## ⚠️ Limits

- The agent process itself can't be limited, only counted with a lease.
- Only jobs started through dingo-pack are managed.
- macOS: the per-process limit isn't enforced (the tree limit is), and free memory is an
  estimate.
- The roster reads Claude Code's session files, which aren't a documented interface. If
  they change, it falls back to transcripts and says so.

## ✅ Self-test

```sh
python3 selftest_dingo_pack.py --base /some/local/dir    # ~40 s
python3 selftest_dingo_pack.py --mutants                 # each planted bug must turn a check red
```

Memory tests run on a simulated machine, never on your real memory.

## 📄 License

MIT. Sister tool: [wombat-gate](https://github.com/liyifreddy/wombat-gate), a git commit
queue for agents sharing one repo.
