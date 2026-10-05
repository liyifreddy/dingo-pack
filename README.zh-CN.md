# 🐕 dingo-pack

<p align="center">
  <img src="https://raw.githubusercontent.com/liyifreddy/dingo-pack/main/docs/img/dingo-pack-banner.jpg" width="100%"
       alt="四只野犬在分成四格的食槽前并排吃，另外三只在旁边等着轮到">
</p>

**野犬群按次序吃。** 一群澳洲野犬分食的时候不抢不挤。dingo-pack 让一台机器上的十几个编程 agent 也这样轮流用内存，机器死了还能把它们叫回来。

🌐 [English](https://github.com/liyifreddy/dingo-pack/blob/main/README.md) · **中文**

## 🚀 快速上手

```sh
pipx install git+https://github.com/liyifreddy/dingo-pack

dingo-pack run --heavy --mem 4G -- pytest     # 重活先等名额和空闲内存，再跑
dingo-pack who                                # 谁占着名额
dingo-pack list                               # 死机之后：哪些 session 丢了
dingo-pack resume --midway                    # 把做到一半的重新打开
```

在 Claude Code 里装上插件，名册会自己保存：

```
/plugin marketplace add liyifreddy/dingo-pack
/plugin install dingo-pack@dingo-pack
```

## 😩 遇到的问题

- 💥 **重活挤在一起。** 几个 session 的测试、模型加载、构建、代码审查同时开跑。我们 16 GB 的 WSL 里，13 个 session 本身就占了 4.4 GB；接着内核杀掉了一个 7.5–11 GB 的 Python，手上的活跟着没了。
- 🪟 **WSL 的 Windows 盘会掉。** 内存一用光，`/mnt/c`、`/mnt/g` 就一直报 I/O 错误，要重新挂载才好，所有 session 一起受影响。
- 🧠 **死机之后记不住开过哪些。** 笔记本死机时开着 8 个 session，事后只能靠回忆一个个找。

## ✨ 用了之后

- ✅ **重活要排队。** `run --heavy` 先等一个空名额（默认 6 个），再等内存够用，然后带着内存上限跑。在一台假装只有 350 MB 的机器上，四件 150 MB 的活一起跑会被系统杀掉；走 dingo-pack 四件都能跑完。
- ✅ **整件活一起算。** 一个程序开出好几个子进程，也按一整棵算：四个 75 MB 的子进程碰上 200 MB 的上限会被停掉，只看单个进程是发现不了的。
- ✅ **名册随时能找回。** hook 会在 session 干活时给它们拍快照（一次 0.08 秒）。死机之后，`list` 告诉你丢了谁，`resume` 在终端新标签里把它们重新打开，一次 4 个。
- ✅ **只提醒，不拦。** 可选的 hook 会在 agent 要绕开名额做重活时提醒它一句，顺便告诉它现在谁占着名额。

```
 slot 0  [###### pytest（约 2G）######]
 slot 1  [## 加载模型（约 6G）##]
 slot 2  [######### 租约：代码审查 #########]
 queue   C ──> D ──> E      排在最前的要等到空名额，而且内存够用
```

## 🎟️ 重活

```sh
dingo-pack run --heavy --mem 6G -- python train.py       # 等名额和 6 GB 内存，再跑
dingo-pack run --heavy --mem 2G --timeout 30m -- pytest   # 等 30 分钟还没轮到就放弃（退出码 75）
dingo-pack log -n 20                                      # 每件活一行记录
```

- `--mem` 填你估计的最高用量。上限默认是它的 1.5 倍；超了只停你这件（退出码 137），不会拖垮整台机器。
- 不是一条 shell 命令的重活（agent 自己的审查、一批子 agent），用租约：`dingo-pack lease --mem 3G --for 45m`，做完 `dingo-pack release`。agent 退出时租约也会自动放掉。

## 📋 名册

<p align="center">
  <img src="https://raw.githubusercontent.com/liyifreddy/dingo-pack/main/docs/img/dingo-pack-roster-captioned.jpg" width="100%"
       alt="三格：夜里头领在篝火边拿着名册；暴风雨把群冲散，头领护着名册躲雨；天亮头领吹哨，群回到篝火边">
</p>

1. **拍名册**：session 干活的时候，名册跟着更新。
2. **机器倒了**：名册已经存在盘上。
3. **召回**：`list` 看丢了谁，`resume` 把它们叫回来。

<p align="center"><sub>Illustrations generated with Google Gemini and edited by the author.</sub></p>

```sh
dingo-pack mark running "第 3 步"          # 报一下自己在干嘛：running | waiting | blocked | done
dingo-pack mark waiting --until 18:30 "等夜里的任务"
dingo-pack resume 名字… --via tmux         # 按名字打开；可以用 Windows Terminal、tmux，或只打印命令
```

`list` 把丢了的 session 分成：做到一半、在等（按时间先后排）、卡住等人、空闲、已完成。每个重新打开的 session 都会先收到一句话：先看你的日志，再接着做。

## ⚙️ 更多

插件里有命令行、名册 hook、提醒 hook，还有一段教 agent 什么时候该排队的说明。不装插件时怎么配 hook、项目设置（`list` 从哪里读日志和状态行）、全部选项、WSL 内存设置，都在 [REFERENCE.md](REFERENCE.md)（英文）。

## ⚠️ 局限

- agent 进程本身限制不了，只能用租约把它算进去。
- 只管通过 dingo-pack 启动的活。
- macOS：单进程上限不生效（整棵上限有效），空闲内存是估算的。
- 名册读的是 Claude Code 自己的 session 文件，这不是公开接口。格式变了的话，会改为只看会话记录，并且会说明。

## ✅ 自测

```sh
python3 selftest_dingo_pack.py --base /某个本地目录    # 约 40 秒
python3 selftest_dingo_pack.py --mutants              # 故意放回的每个 bug 都必须被抓到
```

内存测试在一台模拟的机器上跑，不会真的占满你的内存。

## 📄 许可

MIT。姊妹工具：[wombat-gate](https://github.com/liyifreddy/wombat-gate)，给共用一个仓库的 agent 排队提交。
