# Evidence Coding Agent

仓库地址：`https://github.com/<你的账号>/evidence-coding-agent`（创建公开仓库后替换为实际地址）。

一个独立实现的 Python coding agent，不使用 LangChain、Agents SDK 等 agent 框架。它通过 OpenAI-compatible Chat Completions 的原生 tool calling，在受控 workspace 中读写代码、检索文件和运行命令。

运行真实模型：

```bash
export OPENAI_API_KEY='仅在本机环境变量中设置'
export OPENAI_BASE_URL='https://api.openai.com/v1'
export MODEL='gpt-4o-mini'
python3 -m coding_agent.cli --repo /path/to/repo "修复 XXX，并运行测试"
```

无需 API key 的完整演示与测试：

```bash
python3 -m unittest discover -s tests -v
python3 examples/scripted_demo.py
```

特色：

1. 状态机：`EXPLORE -> EDIT -> VERIFY -> COMPLETE`。
2. 证据门：每次写入递增 revision；只有当前 revision 的零退出码、且不修改 workspace 的验证命令，才能 `finish`。
3. 安全与可观测性：工具仅访问指定 workspace；拒绝删除、提权、推送和高风险 shell 形式；`run.jsonl` 记录可回放轨迹、失败命令和验证证据。

`run.jsonl`、`.env` 和缓存均不提交。论文依据与评测口径见 `docs/literature_basis.md`、`docs/evaluation_harness.md`；该项目的创新是可靠性工程，不宣称新的模型算法或 SWE-bench 成绩。
