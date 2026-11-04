# 更新记录 / Changelog

## 0.5.0 · 候选 / candidate

- Heavy work is intercepted, not just hinted: with `DINGO_ENFORCE=1` (or `"hook": {"enforce": true}`) the PreToolUse hook denies a heavy command and hands back the wrapped one, `dingo-pack run --heavy --mem <peak> -- bash -c '<original>'`; a small job is told `--mem 1G --threads 2`, and batch reads and writes on `/mnt` are pointed at `run --io`.
  The rules cover `run_all` / `regress` / `eval` / `infer` / `predict` / `export` / `preprocess` / `convert` / `build_*` / `mineru` scripts, `mineru` / `magic-pdf` / `ninja` / `colcon` / `cmake --build` / `docker build`, and see through `bash -c`, `timeout`, `nohup`, `uv run`, `conda run` and `poetry run`; `rg` and `grep -r` count by what they actually scan (a single file, a directory under `small_scan` entries and `--max-depth 1` pass), and `cd` / `pushd` in a command line are followed, so relative paths are judged from the directory in force.
  Without the flag the hook only advises, as before; sub-agents are advised, not blocked; any error in the hook lets the command through, so an agent is never wedged.
- `watch` keeps standing guard while work runs: one resident instance (`watch --daemon`, `stop`, `status`, `resume <pid|all>`) scans every process of the user every 2 s, including ones not started through dingo, and attributes them to a session through `DINGO_SESSION`, then `CLAUDE_PID`, then the nearest agent ancestor (claude / codex / opencode / hermes).
  Three lines by default (on a 31 GB machine): available < 4.6 GB or one process growing 2 GB in 60 s → alert; < 2.5 GB → SIGSTOP the fastest-growing job's whole tree, resumed automatically when there is room again (at most twice per job); < 1.2 GB → kill the largest job and log which one and how to re-run it.
  Agents, shells, tmux, systemd, sshd, `watch` itself and other users' processes are never touched; processes that belong to no session are only reported. Thresholds, interval and the protected list are configurable (`"watch"` or `DINGO_WATCH_*`).
- Slots are computed from memory, not fixed at six: a heavy job gets in when its declared `--mem` fits what is free minus what running jobs were promised and a reserve (default max(1 GB, 10 % of memory)); CPU slots = cores, so going over only makes things slower. `who` and `board` say how many more jobs fit and what limits them (`room now: …`), `who --json` carries `capacity`, and `who --gate --need 4G` exits 1 with `gate: WAIT` when nothing should start. A fixed count is still available (`"slots"` / `DINGO_SLOTS` / `--slots`).
- Sessions can be tagged as a whole: `dingo-pack session --name <name> --max 8G -- <agent command>` puts `DINGO_SESSION` / `DINGO_SESSION_MAX` on the session and every child, and `watch` enforces the ceiling on that session (pausing its largest job, not resuming it by itself). Kernel memory limits through cgroups are not available on cgroup v1 hosts such as this WSL2 machine, so this and `watch` are the backstop; `session` switches to `systemd-run … MemoryMax` where cgroup v2 delegation works.
- A status line that has stopped moving is reported: every 5 minutes `watch` reads the status line of each live session (`log_glob` / `status_regex`, the same way `board` does; the directory part of `log_glob` may be a glob) and, for a session whose word is 跑着 (running) and has not changed for 2 h, writes a `status-stalled` event and shows it in `who` and at the top of `board` with the session, the hours and the log path; 已交 / 已关 / 等 Yi / 等指挥 / 卡住 and sessions that have exited are not reported.
- Independent-review fixes A–E. A: every SIGSTOP / SIGCONT / SIGKILL re-checks each process against the values read at scan time (pid, start time, owner, not an agent or protected command) and uses pidfd where available, so a reused pid is skipped and a record whose root changed hands sends nothing; old-format records still resume what is still in the tree. B: automatic resumes are counted per `pid:start time` and capped (default 2), across watch restarts; a manual `watch resume` neither counts nor resets, and `resume_hold` / `max_auto_resumes` are configurable. C: `watch resume` clears `status.json` under the control lock, and `who --gate` counts only jobs that are really still stopped (same pid and start time, state T), so an offline resume no longer leaves the gate at WAIT. D: a short `watch/control.lock`, separate from the lifecycle lock, covers the resume mailbox and `status.json` on both sides, so concurrent requests are no longer lost or overwritten. E: `rg` / `grep -r` are judged by scan size and wrapper scripts (`bash -lc`, `env -u X bash -lc`, `command …`) are unwrapped.
- Shrink towards 0.5: `board`, `name`, `card`, `lint` and `queue` move to shepherd-dispatch; `claim` and `boxes` move to boobook-watch — they still run here and print one line on stderr saying where they go (`DINGO_NO_MOVE_NOTE=1` silences it). `close-all`, `stow` and opening terminal tabs (`resume` / `restore` / `unstow` with `--via wt|tmux`) are no longer maintained, though they still work; `--via print` stays. The roster (save / list / resume / after a crash who was lost) stays core, and `--help`, each subcommand's help and the first screen of both READMEs say so.

## 0.4.2 · 候选 / candidate

- Re-release of the 0.4.1 candidate with the public repo brought in sync: `hooks/hint.py` and `skills/dingo-pack/SKILL.md` now match the source tree.
- Positional arguments after options are accepted on Python < 3.12 (argparse `parse_known_args` fallback), so `mark done --sid X note` and similar forms work from 3.8 through 3.14; command lines are unchanged.
- Self-test falls back to the tool's fixed-zone implementation when `zoneinfo` is missing (Python 3.8), removing the four zone-related failures there.
- CI: the 3.8 job runs on `ubuntu-22.04` (setup-python has no 3.8 build for 26.04) and `fail-fast` is off, so one red job no longer cancels the others; the test matrix is an explicit `include`.

## 0.4.1 · 候选 / candidate

- 基于 0.4.0，保留 boxes、tokens、任务列及只读适配接口；未部署、未发布。
  Based on 0.4.0; retains boxes, tokens, task columns and read-only adapters; not deployed or published.
- 失败时输出配置上限、退出信号和审慎的可能撞限提示；保留子命令输出、退出码与整树停止行为。
  Report configured limits, exit signals and cautious memory hints; preserve child output and exit codes.
- sdist 补 ADAPTERS、发布说明与回归；补中英平台表、三种 agent 接法和安装演示。
  Include adapter/release docs and regressions in sdist; document platforms, agent setup and local install.
- 发布前独立构建检查，审批后的上传任务只消费产物；增加 Linux/macOS CI 与 Windows 边界检查。
  Validate artifacts before environment approval; publishing consumes artifacts; add platform CI.

## 0.4.0 · work 源码基线 / source baseline

boxes、tokens、adapter test、任务等待列及 claim 扩展；历史测试通过但未部署。
Adds boxes, tokens, adapter test, task/wait columns and claim extensions; historically tested, not deployed.

## 0.3.1 · 未公开发布 / unpublished build

慢盘队列、CPU 名额、doctor、ghosts、会话恢复、看板与占用登记。
I/O lane, CPU slots, doctor, ghosts, session restore, board and claims.

## 0.1.1 · 公开 release / public release

名额、内存准入与上限、lease、Claude 名册与恢复。
Slots, memory admission/limits, leases and Claude roster/resume.
