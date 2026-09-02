# Evidence Coding Agent

> 本地证据驱动 Coding Agent：理解任务、改代码、运行验证。

[GitHub 项目](https://github.com/XoomitLXH/evidence-coding-agent) | Python 3.10+

## 快速开始

```bash
git clone https://github.com/XoomitLXH/evidence-coding-agent.git
cd evidence-coding-agent
python3 -m venv .venv && source .venv/bin/activate
export DEEPSEEK_API_KEY="你的密钥"
python3 -m coding_agent.cli --repo /path/to/repo "修复问题并运行测试"
```

网页：`python3 -m coding_agent.web --repo /path/to/repo`，打开 `http://127.0.0.1:50516`。也支持 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`。

## 核心能力与特色

| 能力 | 说明 |
| --- | --- |
| 证据闭环 | 探索、编辑、运行、验证后才完成；保存 JSONL 轨迹与证据，任务可恢复。 |
| 可编辑 IDE | 浏览、编辑、保存、查看 Diff；右侧 IDE 可直接修改。Python 主实现与 `main()` 置前，测试置后。 |
| 执行与安全 | Python 运行/调试及受控 Shell；工作区隔离，删除、提权、强制重置、远程脚本需确认。 |
| Plugin / Skill | 自动发现扩展，工具调用经 schema 校验；`--list-plugins` 查看，`--plugin-dir`、`--skills` 指定。 |
| PDF 与协作 | 安装 `pypdf` 后读取工作区 PDF；网页支持草稿审阅、接受、拒绝、恢复及明确结果。 |

扩展示例：`python3 -m coding_agent.cli --repo /path/to/repo --list-plugins`。
