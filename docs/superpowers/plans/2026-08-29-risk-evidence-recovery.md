# Risk Evidence and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every command decision, command result, and incomplete task understandable and recoverable in the Chinese Coding Agent UI.

**Architecture:** Keep the policy module responsible for deterministic structured risk decisions. The shell tool turns every execution outcome into one stable result schema, while the registry attaches display-safe evidence. The agent loop derives one final failure record from model, policy, command, timeout, verification, and step-limit signals; the browser consumes those fields without changing SSE transport.

**Tech Stack:** Python 3 standard library, `unittest`, Server-Sent Events, vanilla JavaScript.

---

### Task 1: Structured Command Risk Decisions

**Files:**
- Modify: `coding_agent/policy.py`
- Modify: `tests/test_policy.py`

- [ ] **Step 1: Write failing policy decision tests**

```python
from coding_agent.policy import command_decision

def test_allowed_command_has_chinese_reason(self) -> None:
    decision = command_decision("python -m unittest discover -s tests -v")
    self.assertEqual(decision["decision"], "allow")
    self.assertIn("允许执行", decision["reason"])

def test_unknown_command_requests_confirmation(self) -> None:
    decision = command_decision("custom-build")
    self.assertEqual(decision["decision"], "approval_required")
    self.assertIn("需要确认", decision["recommendation"])

def test_recursive_remove_is_denied_with_chinese_guidance(self) -> None:
    decision = command_decision("rm -rf generated")
    self.assertEqual(decision["decision"], "deny")
    self.assertTrue(decision["reason"])
    self.assertTrue(decision["recommendation"])
```

- [ ] **Step 2: Run the focused policy tests and confirm RED**

Run: `python3 -m unittest tests.test_policy -v`

Expected: FAIL because `command_decision` is not yet exported.

- [ ] **Step 3: Implement the minimum structured policy API**

```python
def command_decision(command: str) -> dict[str, str]:
    decision = classify_command(command)
    if decision == "allow":
        return {"decision": decision, "reason": "允许执行：该命令在本地安全命令白名单内。", "recommendation": "执行后请查看退出码和验证输出。"}
    if decision == "approval_required":
        return {"decision": decision, "reason": "该命令不在安全白名单内。", "recommendation": "需要确认后才能执行；可先改用测试、构建或静态检查命令。"}
    return {"decision": decision, "reason": "命令包含高风险操作或不安全的 Shell 控制符。", "recommendation": "请改为受限的单一安全命令，避免删除、提权、远程执行或命令拼接。"}
```

Keep `classify_command()` unchanged. Make `explain_command()` return the structured Chinese reason so old callers remain compatible.

- [ ] **Step 4: Re-run focused policy tests and confirm GREEN**

Run: `python3 -m unittest tests.test_policy -v`

Expected: PASS.

### Task 2: Shell Result and Evidence Contract

**Files:**
- Modify: `coding_agent/tools_shell.py`
- Modify: `coding_agent/tool_registry.py`
- Create: `tests/test_tools_shell.py`

- [ ] **Step 1: Write failing shell result tests**

```python
def test_denied_command_returns_a_structured_policy_failure(self) -> None:
    result = run_command(self.root, AgentState(), "rm -rf never-run")
    self.assertFalse(result["ok"])
    self.assertEqual(result["risk"]["decision"], "deny")
    self.assertEqual(result["failure"]["type"], "policy_rejected")

def test_nonzero_command_returns_command_failure_evidence(self) -> None:
    result = run_command(self.root, AgentState(), "python -c 'raise SystemExit(2)'")
    self.assertFalse(result["ok"])
    self.assertEqual(result["failure"]["type"], "command_failed")
    self.assertEqual(result["evidence"]["exit_code"], 2)

def test_timeout_returns_timeout_evidence(self) -> None:
    result = run_command(self.root, AgentState(), "python -c 'import time; time.sleep(2)'", timeout_seconds=1)
    self.assertEqual(result["failure"]["type"], "timeout")
    self.assertEqual(result["exit_code"], 124)
```

- [ ] **Step 2: Run shell tests and confirm RED**

Run: `python3 -m unittest tests.test_tools_shell -v`

Expected: FAIL because policy rejection currently raises `PolicyError` and no stable `failure` or `evidence` fields exist.

- [ ] **Step 3: Implement one normalized result schema in `run_command`**

```python
{
    "ok": bool,
    "command": command,
    "exit_code": int | None,
    "duration_ms": int,
    "output": str,
    "changed_files": list[str],
    "risk": command_decision(command),
    "failure": None | {"type": str, "reason": str, "recovery": str},
    "evidence": {"tool": "run_command", "parameters": "command=...", "exit_code": int | None, "duration_ms": int, "output_summary": str},
}
```

For rejected commands, do not run subprocesses or alter `AgentState`; return `policy_rejected`. For timeouts use exit code `124` and `failure.type == "timeout"`. For nonzero commands use `command_failed`. Attach the same compact evidence schema to every registry result, retaining all previous result fields.

- [ ] **Step 4: Run focused shell tests and existing command tests**

Run: `python3 -m unittest tests.test_tools_shell tests.test_agent_loop -v`

Expected: PASS.

### Task 3: Final Failure Attribution

**Files:**
- Modify: `coding_agent/agent_loop.py`
- Modify: `tests/test_agent_loop.py`

- [ ] **Step 1: Write failing report tests**

```python
def test_model_exception_has_recovery_record(self) -> None:
    report = AgentLoop(self.root, ExplodingModel()).run("repair")
    self.assertEqual(report["failure"]["type"], "model_request_failed")
    self.assertTrue(report["failure"]["recovery"])

def test_denied_tool_call_is_attributed_to_policy(self) -> None:
    report = AgentLoop(self.root, DeniedCommandModel()).run("clean")
    self.assertEqual(report["failure"]["type"], "policy_rejected")

def test_nonzero_command_is_attributed_to_command_failure(self) -> None:
    report = AgentLoop(self.root, FailingCommandModel()).run("test")
    self.assertEqual(report["failure"]["type"], "command_failed")
```

- [ ] **Step 2: Run focused agent-loop tests and confirm RED**

Run: `python3 -m unittest tests.test_agent_loop -v`

Expected: FAIL because reports do not include `failure`.

- [ ] **Step 3: Add deterministic report failure selection**

```python
def _failure_report(self, status: str, assistant_text: str) -> dict[str, str] | None:
    if status == "complete":
        return None
    if self.model_error:
        return _failure("model_request_failed", "模型请求失败", self.model_error, "检查模型配置、网络连接和服务额度后重试。")
    for failure_type in ("policy_rejected", "timeout", "command_failed"):
        if failure := self.tool_failures.get(failure_type):
            return _failure(failure_type, failure["label"], failure["reason"], failure["recovery"])
    if self.state.failed_commands:
        return _failure("verification_failed", "验证未通过", "最近一次验证命令没有成功。", "查看终端输出，修复问题后重新运行验证。")
    return _failure("step_limit_reached", "执行步数已达上限", "任务在限定轮次内没有完成验证。", "缩小任务范围或继续执行，并保留已有验证结果。")
```

Track the latest structured tool failure in `AgentLoop`. Return `None` for complete tasks; otherwise include Chinese `type`, `label`, `reason`, and `recovery` with priority: model request, policy rejection, timeout, command failure, verification failure, step limit.

- [ ] **Step 4: Re-run focused agent-loop tests and confirm GREEN**

Run: `python3 -m unittest tests.test_agent_loop -v`

Expected: PASS.

### Task 4: Risk and Recovery UI

**Files:**
- Modify: `coding_agent/static/app.js`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing static UI contract checks**

```python
script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
self.assertIn("result.risk", script)
self.assertIn("result.evidence", script)
self.assertIn("failure.recovery", script)
```

- [ ] **Step 2: Run the focused web test and confirm RED**

Run: `python3 -m unittest tests.test_web -v`

Expected: FAIL because `app.js` currently only renders command exit state and text errors.

- [ ] **Step 3: Render structured risk and recovery fields**

```javascript
const riskDetail = result.risk ? `${result.risk.reason}\n建议：${result.risk.recommendation}` : "";
const evidenceDetail = result.evidence ? `\n${result.evidence.output_summary}` : "";
const failure = report.failure;
if (failure) appendTimelineRow(timeline, failure.label, `${failure.reason}\n恢复建议：${failure.recovery}`, "error");
```

Use text rendering only through the existing escaped timeline API. Preserve terminal output and existing success display.

- [ ] **Step 4: Run frontend static validation and all tests**

Run: `node --check coding_agent/static/app.js`

Expected: exit code 0.

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS.

### Task 5: Manual Browser Acceptance

**Files:**
- Verify: `coding_agent/web.py`, `coding_agent/static/app.js`

- [ ] **Step 1: Start the local server with a test workspace**

Run: `python3 -m coding_agent.web --host 127.0.0.1 --port 50515`

Expected: local HTTP server starts successfully.

- [ ] **Step 2: Exercise a normal request, denied command, and failed command**

Expected: the timeline shows Chinese risk reason, compact execution evidence, and a Chinese recovery recommendation; the final summary remains visible and the IDE remains editable.

- [ ] **Step 3: Stop only the server process created for this check**

Expected: no foreground verification process remains.
