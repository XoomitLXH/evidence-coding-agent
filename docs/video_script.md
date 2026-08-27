# Two-Minute Video Script

## 0:00-0:15 - Goal and constraints

Show the repository tree and state: this is an independently implemented local coding agent.
It uses native OpenAI-compatible tool calling, not an agent framework. The model can inspect
files, search code, patch files, and run controlled local commands.

## 0:15-0:35 - Architecture

Show `AgentLoop`, `ToolRegistry`, `AgentState`, and `policy.py`. Explain the workflow:
`EXPLORE -> EDIT -> VERIFY -> COMPLETE`. The model chooses tools; the system owns tool
execution, history, limits, errors, and the completion gate.

## 0:35-1:20 - Live repair demonstration

Run:

```bash
python3 examples/scripted_demo.py
```

Show the JSON result and the repaired `calculator.py`. Open `run.jsonl` and briefly point to
the sequence: failed test (exit 1), `read_file`, `apply_patch`, passed test (exit 0), `finish`.

## 1:20-1:45 - Why it is reliable

Explain that every write creates a new revision and makes old test evidence invalid. Completion
is rejected unless a clean command succeeds on the current revision. Commands are workspace
scoped and high-risk shell forms are rejected.

## 1:45-2:00 - Literature basis and boundary

State that tool interfaces, retrieval/localization, patching, and test feedback are standard
ideas informed by SWE-agent, RepoCoder, and Agentless. The project's contribution is engineering
reliability and observability rather than a claim of a new model or benchmark result.
