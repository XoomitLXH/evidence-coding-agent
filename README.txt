# Evidence Coding Agent

仓库地址：[XoomitLXH/evidence-coding-agent](https://github.com/XoomitLXH/evidence-coding-agent)

一个独立实现的 Python 本地 Coding Agent。它不依赖 LangChain、Agents SDK 等 Agent 框架，而是使用 OpenAI-compatible Chat Completions 的原生 tool calling，在指定 workspace 内检索、编辑和验证代码。项目的重点不是宣称新的模型算法，而是让“任务完成”有可检查的工程证据。

## 适用场景

- 对本地代码仓库提出修复、重构或测试任务。
- 需要让 Agent 在修改后实际运行验证命令，而不是只给出文字回答。
- 需要通过网页查看任务过程、文件 Diff、终端记录和失败原因。
- 需要为课程演示保留可回放的任务轨迹和评测材料。

## 如何运行

### 1. 准备环境

要求 Python 3.10 或更高版本。核心功能只依赖 Python 标准库；在仓库根目录直接以模块方式运行即可。

~~~bash
git clone https://github.com/XoomitLXH/evidence-coding-agent.git
cd evidence-coding-agent

# 可选：使用独立虚拟环境
python3 -m venv .venv
source .venv/bin/activate
~~~

如需读取 PDF，可任选一个解析库安装：

~~~bash
python3 -m pip install pypdf
# 或：python3 -m pip install pdfplumber
~~~

### 2. 配置模型

模型服务需要支持 OpenAI-compatible Chat Completions 接口。以下以 DeepSeek 为例，密钥只应保存在本机环境变量中，不要写入仓库或提交到 Git。

~~~bash
export DEEPSEEK_API_KEY='your-api-key'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'
export MODEL='deepseek-chat'
~~~

也支持 OPENAI_API_KEY；使用 OpenAI 或其他兼容服务时，按需设置 OPENAI_BASE_URL。可选环境变量 CODING_AGENT_MAX_STEPS 用于限制单个任务的最大推理轮数，默认值为 24。

### 3. 命令行运行

将 --repo 指向 Agent 可以检查和修改的目标项目：

~~~bash
python3 -m coding_agent.cli --repo /path/to/target-repository "修复 XXX，并运行测试"
~~~

常用可选参数：

~~~bash
python3 -m coding_agent.cli --repo /path/to/repo --model deepseek-chat --base-url https://api.deepseek.com --max-steps 24 "检查并修复测试失败"
python3 -m coding_agent.cli --repo /path/to/repo --log /path/to/run.jsonl "实现一个功能并验证"
~~~

### 4. 启动网页界面

网页端左侧用于发起和跟踪任务，右侧可查看 Diff、工作区文件和终端记录。

~~~bash
python3 -m coding_agent.web --repo /path/to/target-repository --port 50516
~~~

默认仅监听本机地址。浏览器打开 [http://127.0.0.1:50516](http://127.0.0.1:50516) 后输入任务即可；在输入框中键入 @ 可以引用 workspace 中的文件。按 Ctrl+C 停止服务。

### 5. 无 API Key 的演示和测试

项目包含确定性的脚本演示，不需要模型密钥：

~~~bash
python3 examples/scripted_demo.py
~~~

运行完整单元测试：

~~~bash
python3 -m unittest discover -s tests -v
~~~

## 特色功能

### 证据驱动的完成门禁

Agent 的状态按 EXPLORE -> EDIT -> VERIFY -> COMPLETE 推进。每次写入 workspace 都会增加 revision；只有最新 revision 对应一次退出码为 0、且未再次改动 workspace 的验证命令，finish 才会成功。这样可以避免“已修复”只停留在模型文字回答里。

### 受控的工作区和命令策略

所有文件工具都限制在 --repo 指定的 workspace 内，路径越界会被拒绝。Shell 命令采用白名单和风险分级：删除、提权、强制重置、推送、远程脚本执行及 Shell 命令拼接会被拒绝；不在白名单中的程序需要人工确认后才能执行。

### 草稿审阅与恢复

任务可将改动保留为草稿，在网页 Diff 中由用户接受或拒绝。网页端会保存任务状态、事件和会话信息，刷新或重启后可恢复已保存的任务记录；中断或需要确认的任务也能继续处理。

### 可观测的任务轨迹

CLI 默认在 workspace 生成 run.jsonl，记录模型消息、工具调用、命令结果、失败原因和验证证据。网页端展示同类事件、文件快照与 Diff，便于课程演示、复盘和定位错误。

### 本地 Plugin 与 Skill 系统

插件可提供工作流指导（Skill）和受控工具，自动发现顺序为：显式 --plugin-dir、项目 .codex/plugins/、用户目录 ~/.codex/plugins/、本机 Codex cache 和内置插件。技能可由任务中的 @skill、/skill 或 --skills 显式选择；插件工具需要通过 schema 校验，名称冲突会被拒绝。

### 有边界的 PDF 读取

内置 pdf 插件提供只读 read_pdf 工具，只允许读取 workspace 内的 PDF，并限制单文件 20 MB、单次 20 页和文本长度。安装 pypdf 或 pdfplumber 后即可启用真实文本提取。

## 使用本地插件

最小插件目录结构如下：

~~~text
my-plugin/
├── .codex-plugin/
│   └── plugin.json
└── skills/
    └── review/
        └── SKILL.md
~~~

plugin.json 至少需要安全的 name 字段，也可定义版本、技能目录和 Python 工具入口：

~~~json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "skills": "skills",
  "tools": "tools.py"
}
~~~

查看已发现的插件和技能：

~~~bash
python3 -m coding_agent.cli --repo /path/to/repo --list-plugins
~~~

加载指定插件并选择技能：

~~~bash
python3 -m coding_agent.cli --repo /path/to/repo --plugin-dir /path/to/my-plugin --skills review "检查代码并运行测试"
~~~

技能内容只作为工作流指导注入模型上下文；用户任务、工作区安全策略和工具权限始终优先。插件的 Python 工具通过受控 adapter 注册，必须使用 OpenAI function tool 格式的 schema。

## 项目结构

~~~text
coding_agent/
├── agent_loop.py       # Agent 主循环、状态恢复和任务完成判定
├── tool_registry.py    # 内置工具与插件工具注册
├── policy.py           # workspace 路径和 Shell 命令安全策略
├── state.py            # revision、验证证据与状态机
├── web.py              # 本地网页服务和任务管理
└── builtin_plugins/    # 内置插件（当前包含 PDF）
examples/
└── scripted_demo.py    # 不需要 API Key 的确定性演示
tests/                  # 单元测试
docs/                   # 文献依据、评测口径和视频演示脚本
~~~

## 安全边界与限制

- 本项目是本地开发辅助工具，不应被视为生产环境的沙箱或安全隔离方案。
- Agent 只能操作传给 --repo 的路径范围，但该范围内的代码改动仍应由使用者审阅。
- 网页端不读取或保存 .env、API key 或访问令牌；请用本机环境变量配置密钥。
- 默认网页服务绑定 127.0.0.1，不要在未增加认证和访问控制的情况下暴露到公网。
- 模型输出可能出错；完成门禁只证明验证命令的结果，不能替代适当的测试覆盖、代码审查或人工验收。
