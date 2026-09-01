# Evidence Coding Agent

仓库地址：`https://github.com/XoomitLXH/evidence-coding-agent`。

一个独立实现的 Python coding agent，不使用 LangChain、Agents SDK 等 agent 框架。它通过 OpenAI-compatible Chat Completions 的原生 tool calling，在受控 workspace 中读写代码、检索文件和运行命令。

运行真实模型：

```bash
export OPENAI_API_KEY='仅在本机环境变量中设置'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'
export MODEL='deepseek-chat'
python3 -m coding_agent.cli --repo /path/to/repo "修复 XXX，并运行测试"
```

## 本地 Extension / Plugin

插件遵循 Claude Code / Codex 风格的本地目录约定。项目插件放在
`.codex/plugins/<plugin-name>/`，用户插件放在 `~/.codex/plugins/<plugin-name>/`。
也可以通过一个或多个 `--plugin-dir PATH` 显式指定目录；显式目录优先于项目、用户和内置插件。

最小插件结构：

```text
my-plugin/
├── .codex-plugin/plugin.json
└── skills/
    └── review/SKILL.md
```

`plugin.json` 至少包含安全标识符 `name`，可选 `version`、`skills` 和 `tools`：

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "skills": "skills",
  "tools": "tools.py"
}
```

`skills/*/SKILL.md` 会被索引并按任务自动匹配。任务中可以写 `@review` 或 `/review`，
也可以使用 `--skills review,another-skill` 显式加载。技能内容只作为 workflow guidance 注入
system prompt，任务、安全策略和工具权限始终优先。

查看当前环境自动发现的插件和技能：

```bash
python3 -m coding_agent.cli --repo /path/to/repo --list-plugins
python3 -m coding_agent.cli --repo /path/to/repo --plugin-dir /path/to/my-plugin --skills review "检查代码并运行测试"
```

本机 Codex plugin cache 中的 Superpowers、`frank-gstack-superpowers`、PDF、documents、
presentations 和 spreadsheets 插件会自动发现（目录存在时）。插件工具通过受控 Python adapter
导出 `register(registry)`，schema 必须是 OpenAI function tool 格式，工具名冲突会被拒绝。

内置 `pdf` 插件提供 `read_pdf` 工具：只读 workspace 内的 `.pdf`，单次最多 20 页、文件最多 20 MB、
文本最多约 160k 字符。PDF 文本提取优先使用 `pypdf`，其次使用 `pdfplumber`，两者均未安装时会返回
明确的可选依赖错误。安装任一依赖即可启用真实提取，例如 `python3 -m pip install pypdf`。

启动网页端（对话是主界面，右侧可查看 Diff、文件和终端记录）：

```bash
export OPENAI_API_KEY='仅在本机环境变量中设置'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'
export MODEL='deepseek-chat'
python3 -m coding_agent.web --repo /path/to/repo --port 50516
```

浏览器打开 `http://localhost:50516/`，输入任务后按“开始任务”；输入 `@` 可引用工作区文件。网页端不读取或保存 `.env`、API key 或访问令牌。

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
