# 发布步骤 / Release procedure

## 本次只读查核 / Read-only finding (2026-11-04)

[run 37317607126](https://github.com/liyifreddy/dingo-pack/actions/runs/37317607126)
是 v0.1.1 的发布任务，状态为 **waiting**，steps 为空。`gh run view --log-failed`
返回运行尚未结束、没有日志。环境 API 与 pending_deployments 确认 `pypi` 环境有
required reviewer `liyifreddy`。因此当前阻塞是环境待人工审批；尚不能判定 PyPI 上传失败。
本次核查没有审批、触发、推送或发布。

The existing run is waiting for protected-environment approval before any step runs.
No failed upload log exists yet. The environment protection remains enabled.

## 维护者的设置步骤 / Maintainer configuration

1. 登录 pypi.org。若已有 `dingo-pack` 项目，在项目 Manage → Publishing 添加 GitHub publisher；
   若没有项目，进入 https://pypi.org/manage/account/publishing/ 添加 pending publisher。
2. 字段逐项填写：PyPI project `dingo-pack`（pending 时）；owner `liyifreddy`；repository
   `dingo-pack`；workflow filename `publish.yml`；environment `pypi`。文件名不带目录。
   不创建或传递 API token。
3. GitHub Settings → Environments → `pypi` 核 required reviewer。保留人工审批。
   PyPI 上 environment 必须与工作流完全相同。PyPI 当前账号设置本次核查没有读取，需由维护者核实。
4. 提交候选并完成独立核验后，先看 test workflow 结果；macOS/Windows 未绿不能写成已实测。
   审核版本号后由授权发布者创建匹配的 release tag（候选当前为 `v0.4.1`）。
   新流程先构建、自测、检查元数据与清单，再进受保护的上传 job。
5. 到对应 Actions run 点 Review deployments，核对 tag、提交和构建产物后，选择 `pypi` 并批准。
   本次旧 run 针对 v0.1.1；它不包含本地修复，不能用它发布 0.4.1。
6. 上传完成后核 PyPI 版本，在干净环境安装精确版本并跑版本与 hello 冒烟。
   若上传失败，再留存该次 `gh run view <id> --log-failed`，按实际 OIDC 错误排查 publisher 字段。

For PyPI, add an existing or pending GitHub Trusted Publisher with owner `liyifreddy`,
repository `dingo-pack`, filename `publish.yml`, environment `pypi`. No token is needed.
After review and committing, an authorized publisher creates the matching release and
approves its GitHub environment deployment. Verify the uploaded exact version in a clean
venv. The old waiting run publishes 0.1.1, not this candidate.

## 本地检查 / Local checks

```sh
python3 selftest_dingo_pack.py
python3 selftest_release.py --large-csv
uv build
uvx twine check dist/*
python3 selftest_release.py --artifacts dist
```

`uv build` includes an sdist rebuild, so missing source files are detected. Keep test logs,
real task inputs and generated reports outside distribution allowlists. Full tests need
POSIX process support and local scratch storage; portable smoke tests run in macOS CI.

依据 / Sources:
- https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/
- https://docs.pypi.org/trusted-publishers/adding-a-publisher/
- https://docs.pypi.org/trusted-publishers/using-a-publisher/
