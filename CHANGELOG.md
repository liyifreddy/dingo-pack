# 更新记录 / Changelog

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
