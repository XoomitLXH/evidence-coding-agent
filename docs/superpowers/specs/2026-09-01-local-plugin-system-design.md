# Local Plugin System Design

## Goal

为 evidence coding agent 增加 Claude Code/Codex 风格的本地 extension/plugin 机制，并可加载 Superpowers 与文档技能，首批提供受控 PDF 文本读取能力。

## Scope

- 发现项目级 `.codex/plugins/`、用户级 `~/.codex/plugins/`、显式 `--plugin-dir` 以及内置插件目录。
- 校验 `.codex-plugin/plugin.json`，读取 `skills/` 下的 `SKILL.md`。
- 按任务文本与显式 `@skill`/`/skill` 标记选择技能，并把内容作为隔离的 system prompt 段注入模型上下文。
- 保留现有核心工具；允许 manifest 指定受控 Python 工具模块；首批内置 PDF 插件注册 `read_pdf`。
- CLI 支持插件目录、插件/技能列表和显式技能；日志与报告记录插件加载状态。

不在本次范围内：浏览器/UI 专属插件、远程市场安装、任意 shell 工具插件、自动安装第三方依赖。

## Architecture

`PluginManager` 负责目录发现、manifest/schema 校验、技能索引和选择。每个 `Plugin` 保存根目录、manifest、技能元数据和工具定义。工具模块只能暴露 `register(registry)` 函数，注册时必须提供 JSON schema 与 callable，且调用仍经过 workspace 路径策略。

`AgentLoop` 在 `_start` 时创建 `PluginManager`，从任务和显式技能选择内容，构造一次性的 system prompt；`ToolRegistry` 接收 manager，合并核心与插件工具并以实例属性提供给 complete/stream。插件异常降级为 warning，不阻塞核心 agent。

## Skill Loading

技能目录名、manifest 中的技能名和 `SKILL.md` front matter 名称都必须是安全标识符。任务中的 `@name` 或 `/name` 为显式选择；没有显式选择时，使用名称/描述/关键词与任务文本的大小写不敏感匹配，最多注入 8 个技能、每个文件 40 KB。注入内容带有来源标签，提示模型将其视为工作流指导而非用户指令。

## PDF Tool

`read_pdf(path, page_start=1, page_end=20)` 只接受 workspace 内 `.pdf` 文件，文件上限 20 MB、页数上限 20。优先使用 `pypdf`，不可用时尝试 `pdfplumber`；两者都不可用则返回可操作错误。结果包含相对路径、页码范围、文本和截断标记，绝不执行 PDF 内部内容。

## Error Handling and Security

- 无效 manifest、越界路径、重复工具名、非法 schema、技能过大或读取失败均记录 warning 并跳过对应项目。
- 插件工具名称不得覆盖核心工具或其他插件工具。
- 只加载显式允许目录中的 Python 模块；不执行 manifest 中的命令或网络安装脚本。
- 日志只记录插件/技能名称、路径和摘要，不记录技能全文或 PDF 全文。

## Testing

增加单元测试覆盖：manifest 发现与失败降级、项目目录优先级、技能显式/自动选择和大小限制、工具 schema 合并/冲突、PDF 路径与页数限制及可选依赖错误、AgentLoop 将动态工具传给 complete/stream、CLI 列表参数。
