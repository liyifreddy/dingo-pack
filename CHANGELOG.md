# 更新记录 / Changelog

## 0.5.6 · 候选

- **Hook refusals name the part:** a refused Bash call now says, in three lines, which part of the line was refused (as typed), why, and how to change it (wrap that part in `dingo-pack run --heavy` / `--io`, split it off, or work on the local disk); a relative path on a 9p cwd is pointed out. English by default, Chinese with `DINGO_LANG=zh` (or a `zh` `LC_ALL` / `LANG`).
- **ANSI-C strings no longer flip the quoting:** `$'...\'...'` made shlex lose track of quotes, so quoted text (e.g. a `cp -r` inside an argument) was judged as a command in the cwd and refused as 9p bulk io.
- **`tar -C DIR`:** members after `-C` / `--directory` are resolved inside DIR, not in the cwd (`tar -cf ~/x.tar -C ~/src dir` from a `/mnt/g` cwd is no longer refused).
- Self-test: eleven new hook cases (read-only `git show | sha256sum`, local `cp` / heredoc / compound copy with a 9p cwd pass; `cp -r` of a big 9p tree, `tar -C` into 9p, heredoc `| bash` running heavy work still refused) and the refusal text in both languages.

## 0.5.5 · 2026-11-18

- **Self-test passes on Python 3.8 again:** `t_stall_last_line` resolved the examples path from a relative `__file__` after an earlier test left the working directory deleted; it now uses the path taken at import.

## 0.5.4 · 2026-11-18

- **Default status words are plain English, with no names.** The status line defaults to `Status: <word> - ...` (`status_regex` default `^\s*Status\b`, anchored at the line start, so a status line quoted inside the text no longer counts) and the words are `running` / `waiting` / `blocked` / `done` / `closed`; `stall_words` defaults to `["running"]`. The word is read with the new `watch.status_word_regex` (first group). The former built-in Chinese words moved to `examples/status-words-zh.json`; a project that keeps them sets `status_word_regex` and `stall_words` in its config.
- **`stall_words` / `stuck_net_wchan` written as one string** are one item, not split into characters.
- **A scoped watch (`DINGO_SCOPE_TAG`) sees its own D processes.** Stat-only attribution carries no tag, so every D process was skipped under a scope; it now reads just that variable, bounded to 1 s, only when scoped.
- **D processes first seen during a pile-up get their wchan later.** Above `stuck_heavy` the details are skipped; once the pile-up is gone they are read once, so a 9p wait is no longer taken for a local one and reported after 30 s.
- **The guard waits out the respawn gap.** The supervisor records `respawning` with the time it will start the next watch; a guard that lands in that gap waits for the new watch instead of stopping the supervisor (and resetting its crash count).
- **The respawn supervisor writes its Python stack to `fault.log`** on a crash, like the watch and the guard.
- **Hook: four more ways to start a heavy job are caught.** `"$(python3 x.py)"` and `` "`...`" `` inside double quotes, `echo 'python3 train.py' | bash` (also `printf ... | sh`); and `$((a<<b))` is a shift, no longer taken for a here-document that swallowed the following lines.

## 0.5.3 · 2026-11-18

- **kernel-stuck 加持续时间门槛、网络盘等待单列。** 一个进程在 D 状态满 `stuck_secs`（默认 30 s，配置 `watch.stuck_secs` 或 `DINGO_WATCH_STUCK_SECS`）才报；wchan 是 `p9_client_rpc` 这类网络盘 / 9p 等待的（前缀表 `watch.stuck_net_wchan`）在 status.json 计数（`blocked_net`，每行 `net`），满更长的 `stuck_net_secs`（默认 600 s，`DINGO_WATCH_STUCK_NET_SECS`）才报——慢的共享盘不算卡死，十分钟不应答的算。在一台机器三天的记录里，kernel-stuck 报了一万四千多次，绝大多数是「D 0 s」或 9p 等待；照新规则只剩一次真正的卡死。
- **钩子不再误拦只读命令。** `<(...)` / `>(...)` / `$(...)` 当成一条单独的命令，后面的词仍属外面那条（原来 `diff <(git show x:selftest_a.py) selftest_a.py` 的尾巴被切成一段「命令」判成重活；`diff <(python3 eval.py) …` 反倒漏了）；here-document 正文不当命令，喂给 shell 的除外（`bash <<EOF`，以及经管道交给 shell 的 `cat <<EOF | bash`、`… | tee x | sudo sh`）；命令词是 `cat` / `grep` / `sha256sum` / `diff` / `git show|diff|log…` 这类只读工具的一段不算重活。
- **钩子补拦间接执行的重活。** `xargs [选项] python3 train.py`、`find … -exec|-execdir|-ok|-okdir python3 …`、反引号 `` `…` ``（按 `$(…)` 处理）里的重活现在会被拦；`xargs wc` / `find -exec cat` 这类只读的仍放行。
- **guard 停了不再说成「watch 可能挂了」。** `who` / `watch status` 的 STALE 行把两件事分开判、分开说：guard 自己多久没心跳（`guard.json`），和 watch 本身的状态（持锁 + 最近一次扫描）。watch 活着时写「watch 本身在跑（pid、几秒前扫过），别动它，重起 guard」；watch 也卡住时点名 STUCK；watch 没在跑时才说两样都要起。重起 watch 的判定原本就只看 watch，没变。
- **stalled 只看会话当前那条状态行。** 很多日志把状态行放在开头（标题和第一个 `##` 段之间）原地改，别人又在下面追加或插入状态行；原来只取位置上最后一条，会话已经改了状态还被报「跑着 N 小时」。现在开头那行和最后一行比各自写的时刻，晚的算数；时刻相同或缺时刻按位置；第一条状态行写在段落里的日志（逐段追加的写法）只按位置。开头那行另读文件前 4 KB，日志超过 64 KB 也看得到。
- **watch 崩了留现场、自己重起、连崩就停。** `watch --daemon` 改成起看护进程 `watch --respawn`：watch 一死就记 `watch-crash` 事件，写明退出码和信号（139 写成 SIGSEGV），2 s 后重起；10 分钟内死满 5 次（`crash_max` / `crash_window`）记 `crash-loop` 停手，guard 也不再重起，`who` / `watch status` 写明。crash-loop 标记只在本次开机内有效，重启机器后作废。watch 和 guard 启动时开 faulthandler，栈写进 `watch/fault.log`。内存扫描一分钟内出现两次 TypeError / KeyError / SystemError（`odd_max` / `odd_window`），记 `odd-errors`，退出码 70，交给看护进程起新的；段错误前解释器常先抛出这类不该出现的异常。旁路的状态行检查出错只记一行，不计入。
- **D 进程多时扫描降载。** D 状态进程超过 `stuck_heavy`（默认 200）个时，只读它们的 `stat`，不读 syscall / wchan / fds；每轮最多给 `stuck_scan_max`（默认 200）个新出现的走祖先链，其余的留给下几轮（`status.json` 的 `stuck_pending`）。正常时同时在 D 的不过几十个，内核卡住时会堆到几千个。
- **guard 退出时留话。** 收到 SIGTERM / SIGINT / SIGHUP 或出错退出时，记一行 `op: watch-guard-exit`（原因、信号），`guard.json` 写上 `exited`，STALE 行带出这句。以前 guard 停了也不知道为什么。
- README 的 cron 示例改成每分钟 `guard --once --every 60`，不再落在 STALE 线上。
- `selftest_watch_control.py` 的测试替身跟上现行代码；`selftest_release.py` 的诊断文案断言跟上 0.5.1 的新文案（只改测试）。
- 代码注释和文档里的私人示例换成通用例子；README / REFERENCE 补 `stuck_secs`、`stuck_net_wchan`、`stuck_net_secs`；0.5.2 没单独发布，与 0.5.3 一起发。

### Known issues（留给 0.5.4）

- guard 正好落在看护进程 2 s 重起空档里时，会停掉看护进程另起一个，连崩计数清零。
- 降载期间第一次看到的 D 进程不读 wchan，之后不补读：其中在网络盘上等的会按普通 D 的 30 s 门槛报（多报，不漏报）。
- 钩子旧有的漏拦：双引号里的 `"$(python3 x.py)"`、`` "`…`" ``，`echo 'python3 train.py' | bash`；`$((a<<b))` 会被当成 here-document。

## 0.5.2 · 未单独发布、与 0.5.3 一起发布 / released together with 0.5.3

0.5.1 起的改动都在 0.5.2、0.5.3 这两节里（0.5.2 在 0.5.1 上修看门狗心跳、会话自锁）。

- **「谁占着锁」以 `/proc/locks` 为准，不是「谁打开着这个文件」。** 上一版按 inode 找持锁者，结果 `tail -f watch.log` 这种只打开、不上锁的观察者也被当成锁主，guard 反而不敢恢复。现在直接读 `/proc/locks`（内核只列真实锁），拿到 pid 与设备/inode 对上才算；只打开没上锁的一律不算。
- **guard 只认真正的看门狗，不再按子串猜。** 旧版用「命令行里含 dingo-pack 和 watch」判身份，`tail -f watch.log`、`vim dingo-pack-watch.md` 这种会被误认、收到 SIGTERM。现在两条一起核：① 按 inode 找出**真的持有 `watch/lock` 的那个 pid**（flock 不告诉是谁，只有持锁者的 fd 里有这个 inode）；② 它的 argv 必须正是 `python …/dingo-pack watch …`（逐段比，不看子串）。两条有一条不满足就拒绝、一个信号都不发，并说清是谁占着。
- **guard 真能救回一个「还持着锁、但已经不扫了」的看门狗。** 原来它只喊一句 `watch --daemon`，而旧 watch 还持着锁，新进程立刻 `already running` 退出，guard 却记成「重启成功」——恰恰是「它卡住了没人管」那种情形。现在：先核旧 PID 确实是一个 `dingo-pack watch`（命令行不是就拒绝、不碰它），SIGTERM → 5 秒 → 仍活着就 SIGKILL，还活着就明说「杀不掉（可能在 D 状态），这里救不了」；锁放掉后才起新的，并且**等到出现一次比原来新的扫描**才记成功；失败在 `guard.json` 写 `ok: false` + 原因、`who` 里也显示。
- **看门狗有外部心跳了：`dingo-pack watch guard`。** 一个看门狗报不了自己的死：它被杀、崩掉，没人重起也没人知道（例如它在一台机器上连着崩了五次）。guard 每 `--every` 秒（默认 60）看一次：没人持锁、或最近一次扫描早于 max(3×间隔, 2 分钟) → 重起 `watch --daemon`，在 log.jsonl 记一行、在 `watch/guard.json` 写心跳；`who` / `watch status` 显示 guard 还活着没有。`--once` 适合挂 cron（0.5.3 起推荐每分钟 `--once --every 60`）。
- **会话自锁不再把整机挡住。** 一个会话超了自己的上限、而它所有活着的任务都已被暂停（被停的内存还占着，永远降不回去），现在会被点名 `session-selflock` 并写进 status.json 的 `selflock`；`who --gate` 不再因为它报 WAIT（别的会话照常可以起），只在真的有非自锁的暂停任务时挡。

- **超时记录不再被整表清空。** 原来「卡住的读」记满 512 条就整表清掉，把仍然可能在途的读者忘了，下一轮又开一个 —— 清空只买到新的阻塞线程。现在：满了先只回收「进程已不在 / pid 已换人」的记录，还满就**停止开新的内存文件读**、一律回 unknown（只给 stat 能给的东西）。`selftest_watch.py` 的清理也改成每个 pid 一个自己的结果盒 + 只采信它自己的、已完成的读：一个 pid 迟到的 environ 结果不会串到下一个 pid 头上，也就不会向无关进程发 SIGKILL。
- **公共读入口统一挡 D，不只 watch。** `cmdline` / `environ` / `status` / `smaps_rollup` / `statm` 这些要走进程内存的文件，现在在 `proc_read` 这一个入口就先看 state：D / X 直接回「不知道」，不打开、不留线程。所以 `top`、`ghosts`、任务列表（`session_tasks`）、`who` 也一并挡住了，不只是 watch 主循环。已经超时过一次的 `(pid, 文件)` 记下来不再重试（一个永久阻塞的读者就够了，反复扫不能越积越多）；`top` / `ghosts` / 任务列表各自有一轮 8 s 的总预算（`DINGO_SCAN_READ_BUDGET`），用完只回 stat 能给的。
- **一个卡在内核里的进程不再让 `who` / `top` / `watch` 一起瞎掉。** 有一次，一个 python3 卡在 state D（brk 分配），任何读它 `/proc/<pid>/{statm,cmdline,environ,smaps_rollup,status}` 的程序都跟着进 D：`watch` 守护进程、一次 `dingo-pack who` 和 707 个 `ps` 全挂住，load 900+，内存看门狗从 21:4x 起实际停摆。
  现在：`proc_table` 只读 `stat`（rss 也取自 stat 第 24 列，不再读 statm）；`cmdline` / `environ` / `smaps_rollup` / `status` 一律带 0.3 s 期限，且 state D 的进程直接跳过这些读，只按祖先链归属会话；一轮扫描的这类可选读另有 5 s 总预算（`DINGO_SCAN_READ_BUDGET`）。
- **新增 `dingo-pack stuck`**：列出 state D 的进程（PID、命令、ppid、进程年龄、RSS、所属会话/祖先链、syscall、wchan、打开的 fd 号），按命令分组给出「有多少个卡住」，有就退 1。只读 `stat` / `syscall` / `wchan` / `fd` 目录，不读会被卡住的文件，所以它自己在 D 进程存在时也能跑（实测 1.2 s 出结果）。fd 的**目标**不读：readlink 落在挂死的挂载上一样会卡。
- **看门狗自己的心跳**：`who` / `top` / `watch status` 在最近一次扫描早于 max(3 × interval, 120 s) 时写 `watch: STUCK?` 并给出重启办法；`status.json` 多 `blocked` / `blocked_groups` / `blocked_total` / `scan_s`，卡住的进程也写成 `kernel-stuck` 事件。
- **内存准入不再把「还在爬坡的活」的余量提前分给别人。** 原来按 `承诺 − 已用` 算占用：一件申报 170M、刚起只用了 43M 的活，会让下一件也进来，第三件再进来，三件加起来在都没到申报量之前就把 350M 的机器顶穿（实测：三件同时放行、峰值 449M、看门狗 OOM 杀掉一件）。现在起跑不到一分钟（`PEAK_AFTER_S`）的活按整份申报算，过了这一分钟再按老规矩把用不到的余量还回去。
- **`--limit` 改为默认不开（opt-in）。** `RLIMIT_DATA` / `RLIMIT_AS` 限的是虚拟地址空间，不是 RSS：实测 `import numpy` 就映射 1.15 GB 匿名内存而 RSS 只有 27 MB，旧的「默认 1.5 × --mem」会让一个只用了申报量零头的正确程序在 3 秒内死掉（session 日志里 11 次 rc −11 全是 `limit_kind: data`，峰值 RSS 常低于限额的 5%）。默认的保护改用**按真实内存量的整树上限**（`--tree-limit`，默认 1.5 × --mem，按 PSS/RSS 采样）；要硬顶自己程序的地址空间仍然可以 `--limit`。退出诊断里也写清这一点。
- 自测：新增 `t_kernel_stuck`（假 /proc 里用真 FIFO 冒充会阻塞的 cmdline / environ / smaps / status：`stuck` 不被挂住、`watch --once` 跳过并记录、`who` 仍答得出、旧时间戳报 STUCK；另加两条复核要的：公共读入口 5 轮扫 12 个 D 进程零阻塞线程、超时过的读不再重试线程数不涨），`t_memory_liar` 显式传 `--limit`，另加一条「默认不设上限」——**省略 `--limit`**（不是传 0），并让子进程打印自己的 `RLIMIT_DATA`，必须是 `-1`、1 GiB 保留地址空间仍能映射。`selftest_watch.py`：E1 改成验默认的整树终止（137），新增 E1b 显式 `--limit` 验 MemoryError；cleanup 只对非 D 进程读 environ，每次 0.3 s 期限、整轮 10 s 预算（原来会无期限读全机 environ，在这台卡死的机器上整轮回归会挂在清理阶段）。

## 0.5.0 · 公开 release / public release

- Heavy work is intercepted, not just hinted: with `DINGO_ENFORCE=1` (or `"hook": {"enforce": true}`) the PreToolUse hook denies a heavy command and hands back the wrapped one, `dingo-pack run --heavy --mem <peak> -- bash -c '<original>'`; a small job is told `--mem 1G --threads 2`, and batch reads and writes on `/mnt` are pointed at `run --io`.
  The rules cover `run_all` / `regress` / `eval` / `infer` / `predict` / `export` / `preprocess` / `convert` / `build_*` / `mineru` scripts, `mineru` / `magic-pdf` / `ninja` / `colcon` / `cmake --build` / `docker build`, and see through `bash -c`, `timeout`, `nohup`, `uv run`, `conda run` and `poetry run`; `rg` and `grep -r` count by what they actually scan (a single file, a directory under `small_scan` entries and `--max-depth 1` pass), and `cd` / `pushd` in a command line are followed, so relative paths are judged from the directory in force.
  Without the flag the hook only advises, as before; sub-agents are advised, not blocked; any error in the hook lets the command through, so an agent is never wedged.
- `watch` keeps standing guard while work runs: one resident instance (`watch --daemon`, `stop`, `status`, `resume <pid|all>`) scans every process of the user every 2 s, including ones not started through dingo, and attributes them to a session through `DINGO_SESSION`, then `CLAUDE_PID`, then the nearest agent ancestor (claude / codex / opencode / hermes).
  Three lines by default (on a 31 GB machine): available < 4.6 GB or one process growing 2 GB in 60 s → alert; < 2.5 GB → SIGSTOP the fastest-growing job's whole tree, resumed automatically when there is room again (at most twice per job); < 1.2 GB → kill the largest job and log which one and how to re-run it.
  Agents, shells, tmux, systemd, sshd, `watch` itself and other users' processes are never touched; processes that belong to no session are only reported. Thresholds, interval and the protected list are configurable (`"watch"` or `DINGO_WATCH_*`).
- Slots are computed from memory, not fixed at six: a heavy job gets in when its declared `--mem` fits what is free minus what running jobs were promised and a reserve (default max(1 GB, 10 % of memory)); CPU slots = cores, so going over only makes things slower. `who` and `board` say how many more jobs fit and what limits them (`room now: …`), `who --json` carries `capacity`, and `who --gate --need 4G` exits 1 with `gate: WAIT` when nothing should start. A fixed count is still available (`"slots"` / `DINGO_SLOTS` / `--slots`).
- Sessions can be tagged as a whole: `dingo-pack session --name <name> --max 8G -- <agent command>` puts `DINGO_SESSION` / `DINGO_SESSION_MAX` on the session and every child, and `watch` enforces the ceiling on that session (pausing its largest job, not resuming it by itself). Kernel memory limits through cgroups are not available on cgroup v1 hosts such as many WSL2 setups, so this and `watch` are the backstop; `session` switches to `systemd-run … MemoryMax` where cgroup v2 delegation works.
- A status line that has stopped moving is reported: every 5 minutes `watch` reads the status line of each live session (`log_glob` / `status_regex`, the same way `board` does; the directory part of `log_glob` may be a glob) and, for a session whose word is the stall word (then 跑着, "running") and has not changed for 2 h, writes a `status-stalled` event and shows it in `who` and at the top of `board` with the session, the hours and the log path; other words and sessions that have exited are not reported.
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
