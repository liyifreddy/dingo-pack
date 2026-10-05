---
name: dingo-pack
description: How to run heavy work (test suites, model loads, big builds, code reviews, parallel sub-agents) when many agents share one machine, and how to report session state for crash recovery. Use before starting anything that needs more than ~1 GB of memory or several minutes of CPU, and whenever you write a status line.
---

# Heavy work on a shared machine (dingo-pack)

A dozen other agents run on this machine. If several of them start heavy work at the
same moment, the machine runs out of memory and everybody's work dies with it.
`dingo-pack` hands out a fixed number of heavy slots and checks free memory first.

## Do

- Shell command that is heavy: `dingo-pack run --heavy --mem <peak, e.g. 4G> -- <command>`.
  - It may wait; that is the point. `--timeout 30m` if you would rather give up (exit 75).
  - Exit 137 with "stopped by --tree-limit" means your job used more than you said; raise
    `--mem` honestly or make the job smaller. Do not just set `--tree-limit 0`.
- Heavy work that is not a shell command (a code review, a verifier sub-agent, several
  sub-agents in parallel): `dingo-pack lease --mem <est> --for <duration> --note "<what>"`
  before, `dingo-pack release` after.
- Before starting: `dingo-pack who` shows what is running and what is queued.
- Whenever you write a status line in your log, also report it:
  `dingo-pack mark running "<step>"` (same as `midway`) ·
  `dingo-pack mark waiting --until "<time>" "<what for>"` (`--until` is optional but lets `list`
  sort by when to resume) · `dingo-pack mark blocked "<who must decide>"` · `dingo-pack mark done`.
  After a crash this is how the person restarting the machine knows what to resume first.

## Don't

- Don't start a model load, a full test suite or a large build without `dingo-pack run`.
- Don't poll `dingo-pack who` in a loop; `run` waits for you.
- Don't edit or delete files under `~/.local/state/dingo-pack/`.
