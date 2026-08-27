# Demo and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deterministic, no-API-key repair demonstration and an honest evaluation package for the evidence-driven coding agent.

**Architecture:** A small broken calculator repository is copied into an empty workspace. A deterministic local model emits the same five native tool calls as a real tool-calling model would: fail a test, inspect source, patch it, rerun the test, and finish. The existing `AgentLoop`, verification gate, and JSONL event log remain the production path under test.

**Tech Stack:** Python 3 standard library, `unittest`, `argparse`, JSON Lines, Git.

---

### Task 1: Define the end-to-end demonstration contract

**Files:**
- Create: `tests/test_scripted_demo.py`

- [ ] **Step 1: Write the failing test**

```python
result = subprocess.run(
    [sys.executable, "examples/scripted_demo.py", "--workspace", str(workspace)],
    cwd=repo_root,
    text=True,
    capture_output=True,
    check=False,
)
self.assertEqual(result.returncode, 0, result.stderr)
report = json.loads(result.stdout)
self.assertEqual(report["status"], "complete")
self.assertEqual(report["verification_exit_codes"], [1, 0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_scripted_demo -v`

Expected: FAIL because `examples/scripted_demo.py` does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scripted_demo.py
git commit -m "test: define scripted demo contract"
```

### Task 2: Create a stable repair fixture

**Files:**
- Create: `examples/buggy_calculator/calculator.py`
- Create: `examples/buggy_calculator/tests/test_calculator.py`

- [ ] **Step 1: Add the deliberately faulty implementation**

```python
def add(left: int, right: int) -> int:
    return left - right
```

- [ ] **Step 2: Add the regression test**

```python
from calculator import add


def test_add_two_positive_numbers() -> None:
    assert add(2, 3) == 5
```

- [ ] **Step 3: Verify the fixture fails before repair**

Run: `python3 -m unittest discover -s examples/buggy_calculator/tests -v`

Expected: FAIL because `add(2, 3)` returns `-1`.

- [ ] **Step 4: Commit**

```bash
git add examples/buggy_calculator
git commit -m "test: add deterministic repair fixture"
```

### Task 3: Implement the no-key scripted tool-calling demo

**Files:**
- Create: `examples/scripted_demo.py`
- Modify: `tests/test_scripted_demo.py`

- [ ] **Step 1: Implement an OpenAI response-shaped scripted model**

```python
def tool_response(name: str, arguments: dict[str, object], call_id: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }},
        ]}}]
    }
```

- [ ] **Step 2: Prepare an empty workspace without overwriting user files**

```python
def prepare_workspace(destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"demo workspace must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, destination, dirs_exist_ok=True)
```

- [ ] **Step 3: Run the five-step repair trajectory**

```python
responses = [
    tool_response("run_command", {"command": TEST_COMMAND}, "demo-1"),
    tool_response("read_file", {"path": "calculator.py"}, "demo-2"),
    tool_response("apply_patch", PATCH_ARGUMENTS, "demo-3"),
    tool_response("run_command", {"command": TEST_COMMAND}, "demo-4"),
    tool_response("finish", {"summary": "Fixed add() and verified the regression test."}, "demo-5"),
]
report = AgentLoop(workspace, ScriptedModel(responses), max_steps=len(responses)).run("Fix add()")
```

- [ ] **Step 4: Emit a JSON report including evidence and workspace path**

```python
payload = {
    "workspace": str(workspace),
    "status": report["status"],
    "phase": report["phase"],
    "modified_files": report["modified_files"],
    "verification_exit_codes": [item["exit_code"] for item in report["verification"]],
    "log": str(workspace / "run.jsonl"),
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_scripted_demo -v`

Expected: PASS and the test observes tool calls in the exact order `run_command`, `read_file`, `apply_patch`, `run_command`, `finish`.

- [ ] **Step 6: Commit**

```bash
git add examples/scripted_demo.py tests/test_scripted_demo.py
git commit -m "feat: add reproducible scripted repair demo"
```

### Task 4: Publish the evaluation and presentation contract

**Files:**
- Create: `.gitignore`
- Create: `docs/evaluation_harness.md`
- Create: `docs/video_script.md`
- Modify: `README.txt`

- [ ] **Step 1: Ignore generated and credential-bearing artifacts**

```gitignore
.env
run.jsonl
__pycache__/
.venv/
venv/
.pytest_cache/
```

- [ ] **Step 2: Document metrics without claiming benchmark results**

```markdown
| 指标 | 定义 |
|---|---|
| 任务成功率 | 完成且当前 revision 有成功验证证据的任务数 / 总任务数 |
| 验证覆盖率 | 发生编辑后至少执行一次干净验证命令的任务数 / 发生编辑的任务数 |
| 平均步数 | 每个任务的模型决策轮数均值 |
| 平均耗时 | 从 `run_started` 到 `run_finished` 的墙钟时间均值 |
```

- [ ] **Step 3: Give the two-minute demo narrative and concise run commands**

```bash
python3 -m unittest discover -s tests -v
python3 examples/scripted_demo.py --workspace /tmp/coding-agent-demo
python3 -m coding_agent.cli --repo /path/to/repo "修复 XXX，并运行测试"
```

- [ ] **Step 4: Verify all final deliverables**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS.

Run: `python3 -m compileall -q coding_agent examples`

Expected: exit code 0.

Run: `python3 examples/scripted_demo.py`

Expected: JSON report has `status: "complete"` and verification exit codes `[1, 0]`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.txt docs
git commit -m "docs: add evaluation and demo guidance"
```
