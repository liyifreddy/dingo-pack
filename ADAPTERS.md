# Adapters: teaching dingo-pack about another coding agent

dingo-pack's core (process trees, memory, slots, the git queue via wombat-gate, boxes, the board) does not care which agent you run. Three things do, and they live in an **adapter**:

1. **Find the sessions.** Which agent sessions are alive on this machine: id, name, pid, working directory, state.
2. **Read a session's record.** Where its transcript is, and how many tokens each turn used.
3. **Hook into it.** How to make the agent run `dingo-pack save --hook` every turn and `hooks/hint.py` before heavy tool calls (optional: everything else works without hooks).

dingo-pack ships one adapter, for Claude Code. If you are an agent (Codex, pi, …) reading this to set dingo-pack up for yourself: write the ~100 lines below for your own agent, run the adapter test, and point dingo-pack at the file. We maintain the interface, the Claude Code adapter and the test, not other agents' adapters.

## 1 · The interface

One Python file that defines `ADAPTER`, an object with these attributes and methods (duck-typed; no base class to import):

```python
class MyAgentAdapter:
    name = "my-agent"            # shown in `dingo-pack top` next to each session
    owner_env = "MY_AGENT_PID"   # env var your agent sets in every process it starts, holding the agent's pid;
                                 # None if there is none (then only the agent's process tree counts as its children)

    def sessions(self):
        """Live sessions on this machine. A list of dicts:
           {"id": str, "pid": int, "name": str or None, "cwd": str or None,
            "proc_start": str or None,   # /proc/<pid>/stat field 22, to tell a reused pid apart (None if unknown)
            "status": "busy" | "idle" | "waiting" | None,
            "started_at": float or None}  # epoch seconds"""

    def transcript(self, session_id):
        """Path of the session's transcript file, or None."""

    def subtranscripts(self, session_id, path):
        """Paths of its sub-agents' transcripts (may be [])."""

    def usage(self, path, cache):
        """Tokens in one transcript, read incrementally. `cache` is a dict you may keep state in
        (dingo-pack saves it between runs, keyed however you like). Return
           {"days": {"YYYY-MM-DD": [input, cache_write, cache_read, output, turns]},
            "last_input": int or None}   # input tokens of the latest turn (input + cache write + cache read)
        or None if the file cannot be read. Count each model call once."""

    def hooks(self):
        """A short text for the human: how to make the agent run the two hooks."""

ADAPTER = MyAgentAdapter()
```

Rules:

- **Read only.** An adapter must not write to the agent's files or send signals. dingo-pack's own test checks that `sessions()` and `usage()` leave the files they read unchanged.
- **Say "unknown", never guess.** If a field is not available, return `None`; the board then shows "读不到" / "?" instead of a made-up number.
- Standard library only, Python ≥ 3.8.

## 2 · Test it

```sh
DINGO_AGENTS=/path/to/my_agent_adapter.py dingo-pack adapter test
```

It loads the file, calls each method on the live machine, and checks: types and required keys, that each `pid` is alive, that transcripts exist and `usage()` returns non-negative integers, and that nothing it read changed. Exit code 0 = usable; otherwise it lists what is wrong.

## 3 · Use it

```sh
export DINGO_AGENTS=claude-code,/path/to/my_agent_adapter.py   # several agents on one machine
dingo-pack top        # each session: children, what it waits for, spin flags, tokens
dingo-pack tokens     # tokens per session today
dingo-pack board
```

`DINGO_AGENTS` takes built-in names (`claude-code`) and paths to adapter files, comma-separated. The default is `claude-code`.

## 4 · What uses the interface (0.4)

`top`, `tokens` and the task columns of `board` go through adapters. The roster (`save` / `list` / `resume` / `close-all` / `restore`) and `ghosts` still talk to Claude Code directly; they move behind the interface in a later version.

## 5 · The Claude Code adapter, for reference

- sessions: `~/.claude/sessions/<pid>.json` (`sessionId`, `pid`, `procStart`, `name`, `nameSource`, `status`, `cwd`, `startedAt`).
- transcript: `~/.claude/projects/<dir>/<sessionId>.jsonl`; sub-agents in `<sessionId>/subagents/*.jsonl`.
- usage: assistant lines carry `message.usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`); one message is often written several times with the same `message.id`, so it is counted once.
- owner_env: `CLAUDE_PID`.
- hooks: the plugin in this repo (`/plugin install dingo-pack@dingo-pack`), or the snippet in REFERENCE.md.

## 6 · Claude Code、Codex、OpenCode Go 的接入边界

三者可在同一 POSIX 主机上调用 `dingo-pack run --heavy --mem <预估> -- <命令>`，
共享本地 DINGO_HOME。README 中英版给出项目 AGENTS.md、Codex skill、OpenCode
instructions 的配置例子。这些规则要求 agent 主动包命令，不构成外部命令拦截。

内置适配器只有 `claude-code`；Codex 与 OpenCode Go 没有内置发现/token/恢复适配。
不要在 DINGO_AGENTS 中填写不存在的内置名称。自定义实现通过文件路径加载，按上文
`adapter test` 校验；名册与 ghosts 仍仅针对 Claude。

All three agents can share command admission. Only Claude Code has a built-in session
adapter. Use external adapter file paths for other agents; do not invent built-in names.
The instruction/skill examples in both READMEs are guidance, not enforcement hooks.
