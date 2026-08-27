# Coding Agent 设计与论文借鉴

## 1. 需求边界

题目要求独立实现一个可调用大语言模型、读写文件、执行命令的 coding agent，并且不能依赖 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等 agent 框架。实现目标不是复现完整 SWE-bench，而是交付一个能在本地仓库中完成“理解任务-修改代码-运行验证-汇报证据”的最小闭环。

## 2. 近年工作对照

| 工作 | 主要做法 | 创新/贡献 | 本项目借鉴 |
|---|---|---|---|
| RepoCoder (2023) | 相似度检索与代码生成交替进行 | 把跨文件仓库信息接入生成循环 | 轻量 `search_code`，让模型先定位再编辑 |
| Reflexion (2023) | 把环境反馈转成自然语言反思并写入 episodic memory | 不更新模型参数也能利用试错经验 | 任务账本记录失败命令、原因和下一步 |
| SWE-bench (2023) | 真实 GitHub issue + 真实仓库 + 测试验证 | 建立面向真实软件工程的评测任务 | demo 使用带回归测试的小仓库，结果以测试为准 |
| CodeAct (2024) | 用可执行 Python 代码统一动作空间 | 动作可组合、可动态修正 | 只借鉴“动作可组合”思想；执行面仍使用受控命令工具，便于审计 |
| SWE-agent (2024) | 设计 Agent-Computer Interface，提供浏览、编辑、测试工具 | 证明工具接口设计会显著影响 agent 表现 | 采用小而明确的文件/搜索/补丁/命令工具及结构化输出 |
| Agentless (2024) | 显式拆为 localization、repair、patch validation 三阶段 | 以简单、可解释的 workflow 取得强基线 | 用状态机约束探索、编辑、验证阶段，避免无限循环 |
| OpenHands (2024/ICLR 2025) | 可插拔 agent、沙箱执行、多基准评测 | 工程平台化、执行隔离和扩展性 | 保留本地 workspace 边界、事件轨迹和可替换模型客户端 |
| UTBoost (ACL 2025) | 从任务难度、测试质量等维度严谨评估 coding agents | 强调评测可靠性而不只看单一成功率 | 报告真实命令、退出码、耗时和未解决问题 |

结论：`tool calling + agent loop + 本地执行 + 测试反馈` 是当前主流基线。单纯增加工具数量或 prompt 不是有辨识度的创新；本项目的创新应定位为可靠性和可观测性工程。

## 3. 方案选择

### 方案 A：自由循环工具调用

实现最简单，但容易重复搜索、误改文件、在测试失败后仍输出“完成”。不采用为主方案。

### 方案 B：阶段状态机 + 证据驱动验证（采用）

模型负责每轮决策，系统负责阶段约束和证据门：修改后必须产生成功验证证据，才能完成；失败会把任务退回编辑阶段。这样既保持 LLM 的灵活性，又把完成判定从模型主观陈述改为可检查事实，适合截止时间和视频演示。

### 方案 C：多智能体协作

可拆成定位、实现、审查等角色，但通信和调度成本高，难以在有限时间内证明收益，也与题目要求的独立实现不匹配。本阶段不采用。

## 4. 系统设计

```text
用户任务
   |
   v
Chat Completions + 原生 tool calling
   |
   v
AgentLoop <--> ToolRegistry
   |              |
   |              +-- list_dir/read_file/search_code
   |              +-- write_file/apply_patch
   |              +-- run_command (风险策略)
   |
   +--> StateMachine: EXPLORE -> EDIT -> VERIFY -> COMPLETE
   +--> EvidenceLedger: 命令、退出码、摘要、失败原因
   +--> JSONL EventLog: 可回放轨迹
```

核心组件：

- `model_client.py`：OpenAI-compatible HTTP 客户端，不依赖 agent SDK。
- `tool_registry.py`：工具 schema、参数校验、统一调用入口。
- `tools_files.py` / `tools_search.py` / `tools_shell.py`：最小本地工具集。
- `state.py`：阶段、修改文件、验证证据和失败次数。
- `agent_loop.py`：消息循环、工具结果回填、步数上限和完成门。
- `policy.py`：workspace 路径隔离与高风险命令拒绝。
- `event_log.py`：每轮决策和工具结果写入 JSONL。

## 5. 主要创新点

### 5.1 证据驱动的验证门（主创新）

任何写操作都会把状态标记为 dirty 并进入 `VERIFY`。只有成功执行过验证命令（退出码为 0），并且没有后续未验证修改，系统才接受 `finish`。最终报告自动列出修改文件、验证命令、退出码和输出摘要。该机制直接针对 coding agent 最常见的可靠性问题：代码改了但没有证明可用。

### 5.2 风险感知的命令策略

命令按安全、需确认、拒绝三类处理。默认允许测试、构建、静态检查和只读 git 命令；删除、提权、推送和明显破坏性命令直接拒绝。这样能把“自主执行”限制在可解释边界内。

### 5.3 可回放轨迹与任务账本

JSONL 记录模型消息、工具参数、结果、状态变化；账本保留失败命令和下一步提示。它同时借鉴 Reflexion 的反馈记忆与 OpenHands 的事件化工程设计，为视频演示和错误定位提供证据。

## 6. 验证计划

1. 单元测试：路径隔离、搜索、补丁替换、风险命令策略、验证门。
2. 集成测试：FakeModel 连续调用读文件、写文件、测试和完成，检查最终报告必须包含证据。
3. 演示任务：在临时 Python 仓库中修复一个有回归测试的函数，展示一次失败测试后修复成功，以及 `run.jsonl` 轨迹。

## 7. 明确局限

本实现不是新的基础模型或新的训练算法，创新属于可靠性导向的 agent 系统设计；在没有真实 API key 的环境只能运行测试，不能声称完成真实仓库任务。后续可在 SWE-bench Lite 子集上比较成功率、平均步数、验证覆盖率和成本。
