from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from .event_log import EventLog
from .draft import DraftChanges
from .state import AgentState
from .tool_registry import TOOL_SPECS, ToolRegistry
from .plugins import PluginManager


class Model(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


SYSTEM_PROMPT = """You are a local coding agent. Work only inside the provided workspace.
Use tools to inspect before editing. Prefer small apply_patch replacements. After every edit,
run an appropriate test, build, or static-check command. If a command fails, inspect its output,
repair the code, and rerun it. A read-only task with no failed commands may call finish without
running a command. After modifying the workspace, you may call finish only when a clean
verification command for the latest revision exits 0.
Never claim success without tool evidence. Keep changes focused and explain unresolved issues.
默认使用中文回复，最终总结也使用中文；代码、命令和文件路径保持原样。
"""


def _failure(failure_type: str, label: str, reason: str, recovery: str) -> dict[str, str]:
    return {
        "type": failure_type,
        "label": label,
        "reason": reason,
        "recovery": recovery,
    }


class AgentLoop:
    def __init__(
        self,
        root: Path,
        model: Model,
        *,
        log_path: Path | None = None,
        max_steps: int = 24,
        event_listener: Callable[[dict[str, Any]], None] | None = None,
        plugin_dirs: list[Path] | None = None,
        explicit_skills: list[str] | None = None,
        plugin_manager: PluginManager | None = None,
        require_draft_review: bool = False,
    ):
        self.root = root.resolve()
        self.model = model
        self.plugin_dirs = [Path(item).expanduser() for item in (plugin_dirs or [])]
        self.explicit_skills = list(explicit_skills or [])
        self.plugin_manager = plugin_manager
        self.state = AgentState()
        self.log = EventLog(log_path or self.root / "run.jsonl", listener=event_listener)
        self.require_draft_review = bool(require_draft_review)
        self.registry = ToolRegistry(
            self.root,
            self.state,
            self.log,
            require_draft_review=self.require_draft_review,
            plugin_manager=self.plugin_manager,
        )
        self.max_steps = max_steps
        self.model_error: str | None = None
        self.tool_failures: dict[str, dict[str, str]] = {}
        self.messages: list[dict[str, Any]] = []
        self.task: str | None = None
        self.next_step = 1
        self.assistant_text = ""
        self.paused_status: str | None = None
        self.pending_call: dict[str, Any] | None = None

    def run(self, task: str) -> dict:
        self._start(task)
        self.log.write("run_started", task=task, workspace=str(self.root))
        return self._continue()

    def _start(self, task: str) -> None:
        self.state = AgentState()
        self.require_draft_review = bool(self.require_draft_review)
        self.plugin_manager = self.plugin_manager or PluginManager(self.root, self.plugin_dirs)
        self.registry = ToolRegistry(self.root, self.state, self.log, require_draft_review=self.require_draft_review, plugin_manager=self.plugin_manager)
        skill_prompt = self.plugin_manager.system_prompt_addendum(task, self.explicit_skills)
        self.model_error = None
        self.tool_failures = {}
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT + skill_prompt},
            {"role": "user", "content": f"Task: {task}\nWorkspace: {self.root}\nCurrent state: {self.state.prompt_context()}"},
        ]
        self.task = task
        self.next_step = 1
        self.assistant_text = ""
        self.paused_status = None
        self.pending_call = None

    def session_snapshot(self) -> dict[str, Any]:
        """Capture resumable execution state without model configuration or credentials."""
        return {
            "state": self.state.to_dict(),
            "tool_failures": self.tool_failures,
            "messages": self.messages,
            "task": self.task,
            "next_step": self.next_step,
            "assistant_text": self.assistant_text,
            "paused_status": self.paused_status,
            "pending_call": self.pending_call,
            "drafts": self.registry.drafts.to_dict(),
            "require_draft_review": self.require_draft_review,
        }

    def restore_session(self, snapshot: dict[str, Any]) -> None:
        """Restore a paused loop into this loop's fresh log and model runtime."""
        if not isinstance(snapshot, dict):
            raise ValueError("agent session snapshot must be an object")
        messages = snapshot.get("messages")
        tool_failures = snapshot.get("tool_failures")
        drafts = snapshot.get("drafts", {})
        pending_call = snapshot.get("pending_call")
        if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
            raise ValueError("agent session snapshot has invalid messages")
        if not isinstance(tool_failures, dict) or not isinstance(drafts, dict):
            raise ValueError("agent session snapshot has invalid runtime data")
        if pending_call is not None and not isinstance(pending_call, dict):
            raise ValueError("agent session snapshot has invalid pending call")
        paused_status = snapshot.get("paused_status")
        if paused_status not in {None, "awaiting_approval", "review_required", "awaiting_clarification"}:
            raise ValueError("agent session snapshot has invalid pause status")
        self.state = AgentState.from_dict(snapshot.get("state", {}))
        self.require_draft_review = bool(snapshot.get("require_draft_review", paused_status == "review_required"))
        self.plugin_manager = self.plugin_manager or PluginManager(self.root, self.plugin_dirs)
        self.registry = ToolRegistry(
            self.root,
            self.state,
            self.log,
            drafts=DraftChanges.from_dict(self.root, drafts),
            require_draft_review=self.require_draft_review,
            plugin_manager=self.plugin_manager,
        )
        self.tool_failures = {
            str(name): {str(key): str(value) for key, value in failure.items()}
            for name, failure in tool_failures.items()
            if isinstance(failure, dict)
        }
        self.messages = list(messages)
        self.task = str(snapshot["task"]) if snapshot.get("task") is not None else None
        self.next_step = max(1, int(snapshot.get("next_step", 1)))
        self.assistant_text = str(snapshot.get("assistant_text", ""))
        self.paused_status = paused_status
        self.pending_call = pending_call
        self.model_error = None

    def _continue(self) -> dict:
        status = "incomplete"
        while self.next_step <= self.max_steps:
            step = self.next_step
            self.next_step += 1
            self.state.turn = step
            try:
                stream_method = getattr(self.model, "stream", None)
                if callable(stream_method):
                    message = self._stream_response(stream_method, self.messages, step)
                else:
                    response = self.model.complete(self.messages, self.registry.tool_specs)
                    choice = (response.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
            except Exception as exc:
                self.assistant_text = f"任务执行失败：{exc}"
                status = "error"
                self.model_error = str(exc)
                self.log.write("model_error", error=self.assistant_text)
                break
            self.assistant_text = message.get("content") or ""
            self.log.write(
                "model_response",
                step=step,
                message=self._display_message(message),
                state=self.state.prompt_context(),
            )
            tool_calls = message.get("tool_calls") or []
            self.messages.append(message)
            if not tool_calls:
                if self.state.phase == "COMPLETE" and self.state.can_finish():
                    status = "complete"
                    break
                self.messages.append({"role": "user", "content": f"You must use a tool. Verification gate state: {self.state.prompt_context()}"})
                continue
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = None
                display_arguments = self.registry.display_arguments(arguments)
                self.log.write("tool_call", step=step, name=name, arguments=display_arguments)
                if not isinstance(arguments, dict):
                    reason = "tool arguments must be a JSON object"
                    self.state.record_invalid_tool_protocol(reason)
                    result = {
                        "ok": False,
                        "error": reason,
                        "failure": _failure(
                            "invalid_tool_protocol",
                            "工具调用格式无效",
                            reason,
                            "修正工具参数为 JSON 对象后重试。",
                        ),
                    }
                    self.log.write(
                        "tool_result",
                        name=name,
                        arguments=display_arguments,
                        result=result,
                        state=self.state.prompt_context(),
                    )
                else:
                    result = self.registry.call(name, arguments)
                self._record_tool_failure(result)
                failure = result.get("failure") if isinstance(result, dict) else None
                failure_type = failure.get("type") if isinstance(failure, dict) else None
                if failure_type in {"awaiting_approval", "review_required"}:
                    self.pending_call = {
                        "call": call,
                        "name": name,
                        "arguments": arguments,
                        "step": step,
                    }
                    self.paused_status = failure_type
                    report = self.report(failure_type, self.assistant_text)
                    self.log.write(
                        "run_paused",
                        status=failure_type,
                        pending=self._display_message({"tool_calls": [call]}),
                        report=report,
                    )
                    return report
                self._append_tool_result(call, name, result, step)
                if failure_type == "policy_rejected":
                    # Keep the rejected call and its result in the same model context,
                    # but never retain it as a pending call: clarification must lead
                    # to a new plan instead of replaying a dangerous command.
                    self.pending_call = None
                    self.paused_status = "awaiting_clarification"
                    report = self.report("awaiting_clarification", self.assistant_text)
                    self.log.write(
                        "run_paused",
                        status="awaiting_clarification",
                        pending=None,
                        report=report,
                    )
                    return report
                if self.state.phase == "COMPLETE" and self.state.can_finish():
                    status = "complete"
                    break
            if status == "complete":
                break
            self.messages.append({"role": "user", "content": f"Continue the task. Current state: {self.state.prompt_context()}"})
        if status != "complete" and self.state.phase == "COMPLETE" and self.state.can_finish():
            status = "complete"
        return self._finish(status)

    def resume_after_approval(self, *, approved: bool) -> dict:
        if self.paused_status != "awaiting_approval" or not self.pending_call:
            raise RuntimeError("no command approval is pending")
        pending = self.pending_call
        name = str(pending.get("name") or "")
        arguments = pending.get("arguments")
        call = pending.get("call")
        if not isinstance(arguments, dict) or not isinstance(call, dict) or name != "run_command":
            raise RuntimeError("pending approval is not a runnable command")
        if approved:
            result = self.registry.execute_approved_command(arguments)
        else:
            command = str(arguments.get("command") or "")
            result = {
                "ok": False,
                "command": command,
                "error": "用户拒绝执行该命令。",
                "failure": _failure(
                    "approval_rejected",
                    "命令已拒绝",
                    "用户没有批准该命令。",
                    "根据用户反馈选择安全命令或说明无需执行该命令。",
                ),
            }
            self.log.write(
                "tool_result",
                name=name,
                arguments=self.registry.display_arguments(arguments),
                result=result,
                state=self.state.prompt_context(),
            )
        self._record_tool_failure(result)
        self._append_tool_result(call, name, result, int(pending.get("step") or self.next_step))
        self.pending_call = None
        self.paused_status = None
        self.messages.append({"role": "user", "content": f"Continue the task. Current state: {self.state.prompt_context()}"})
        return self._continue()

    def resume_after_review(self, *, accepted: bool) -> dict:
        if self.paused_status != "review_required" or not self.pending_call:
            raise RuntimeError("no draft review is pending")
        pending = self.pending_call
        name = str(pending.get("name") or "")
        arguments = pending.get("arguments")
        call = pending.get("call")
        if not isinstance(arguments, dict) or not isinstance(call, dict) or name != "finish":
            raise RuntimeError("pending review is not a completion request")
        if accepted:
            accepted_result = self.registry.drafts.accept()
            conflicts = accepted_result.get("conflicts") or []
            if conflicts:
                result = {
                    "ok": False,
                    "failure": _failure(
                        "review_required",
                        "草稿存在文件冲突",
                        f"以下文件在审阅期间已被外部修改：{', '.join(conflicts)}。",
                        "重新加载文件并确认差异后，再次接受或拒绝草稿。",
                    ),
                    "conflicts": conflicts,
                }
                self._record_tool_failure(result)
                self.log.write(
                    "tool_result",
                    name=name,
                    arguments=self.registry.display_arguments(arguments),
                    result=result,
                    state=self.state.prompt_context(),
                )
                self.log.write("run_paused", status="review_required", report=self.report("review_required", self.assistant_text))
                return self.report("review_required", self.assistant_text)
            result = self.registry.call(name, arguments)
        else:
            result = {
                "ok": False,
                "error": "用户拒绝了草稿改动。",
                "failure": _failure(
                    "draft_rejected",
                    "草稿已拒绝",
                    "用户拒绝将草稿写入工作区。",
                    "根据用户反馈修改方案，或说明不再需要该改动。",
                ),
            }
            self.log.write(
                "tool_result",
                name=name,
                arguments=self.registry.display_arguments(arguments),
                result=result,
                state=self.state.prompt_context(),
            )
        self._record_tool_failure(result)
        self._append_tool_result(call, name, result, int(pending.get("step") or self.next_step))
        self.pending_call = None
        self.paused_status = None
        if self.state.phase == "COMPLETE" and self.state.can_finish():
            return self._finish("complete")
        self.messages.append({"role": "user", "content": f"Continue the task. Current state: {self.state.prompt_context()}"})
        return self._continue()

    def resume_after_clarification(self, instruction: str) -> dict:
        if self.paused_status != "awaiting_clarification":
            raise RuntimeError("no policy clarification is pending")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("clarification instruction must not be empty")
        clarification = instruction.strip()
        self.pending_call = None
        self.paused_status = None
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "用户补充的允许范围："
                    f"{clarification}\n"
                    "安全策略拒绝的原命令已记录为失败；不要自动重放、变体重放或绕过策略执行原命令。"
                    "请在允许范围内重新规划，如需执行新的命令，先遵守安全策略并使用新的安全工具调用。"
                    f"\n当前状态：{self.state.prompt_context()}"
                ),
            }
        )
        return self._continue()

    def _append_tool_result(self, call: dict[str, Any], name: str, result: dict[str, Any], step: int) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": call.get("id", f"call-{step}"),
            "name": name,
            "content": self.registry.encode(result),
        })

    def _finish(self, status: str) -> dict:
        report = self.report(status, self.assistant_text)
        self.log.write("run_finished", status=status, report=report)
        return report

    def _record_tool_failure(self, result: dict[str, Any]) -> None:
        failure = result.get("failure")
        if not isinstance(failure, dict):
            return
        failure_type = failure.get("type")
        if not isinstance(failure_type, str) or not failure_type:
            return
        self.tool_failures[failure_type] = {
            key: str(value)
            for key, value in failure.items()
            if key in {"type", "label", "reason", "recovery"} and isinstance(value, str)
        }

    def _stream_response(self, stream_method: Callable[..., Any], messages: list[dict[str, Any]], step: int) -> dict[str, Any]:
        """Consume a model stream while publishing deltas and rebuilding one assistant message."""
        reasoning = ""
        content = ""
        finish_reason = None
        tool_calls: dict[int, dict[str, Any]] = {}
        self.log.write("model_message_start", step=step)
        for delta in stream_method(messages, self.registry.tool_specs):
            if not isinstance(delta, dict):
                continue
            reasoning_delta = str(delta.get("reasoning") or delta.get("reasoning_content") or "")
            content_delta = str(delta.get("content") or "")
            reasoning += reasoning_delta
            content += content_delta
            for position, fragment in enumerate(delta.get("tool_calls") or []):
                if not isinstance(fragment, dict):
                    continue
                index = fragment.get("index", position)
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    index = position
                current = tool_calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if fragment.get("id"):
                    current["id"] = fragment["id"]
                if fragment.get("type"):
                    current["type"] = fragment["type"]
                function = fragment.get("function") or {}
                if function.get("name"):
                    current["function"]["name"] = function["name"]
                if function.get("arguments"):
                    current["function"]["arguments"] += function["arguments"]
            finish_reason = delta.get("finish_reason") or finish_reason
            self.log.write(
                "model_delta",
                step=step,
                reasoning=reasoning_delta,
                content=content_delta,
                tool_calls=[],
                finish_reason=delta.get("finish_reason"),
            )
        self.log.write("model_message_end", step=step, finish_reason=finish_reason)
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning:
            message["reasoning_content"] = reasoning
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return message

    @staticmethod
    def _display_message(message: dict[str, Any]) -> dict[str, Any]:
        display = dict(message)
        tool_calls = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            visible_call = dict(call)
            function = dict(call.get("function") or {})
            raw_arguments = function.get("arguments")
            try:
                parsed_arguments = json.loads(raw_arguments or "{}")
            except (TypeError, json.JSONDecodeError):
                parsed_arguments = None
            function["arguments"] = json.dumps(
                ToolRegistry.display_arguments(parsed_arguments),
                ensure_ascii=False,
            )
            visible_call["function"] = function
            tool_calls.append(visible_call)
        if "tool_calls" in display:
            display["tool_calls"] = tool_calls
        return display

    def report(self, status: str, assistant_text: str = "") -> dict:
        return {
            "status": status,
            "phase": self.state.phase,
            "summary": self.state.final_summary or assistant_text,
            "modified_files": sorted(self.state.modified_files),
            "verification": self.state.verification,
            "failed_commands": self.state.failed_commands,
            "ledger": self.state.ledger[-10:],
            "evidence_required": self.state.phase != "COMPLETE" and not self.state.can_finish(),
            "failure": self._failure_report(status),
            "plugins": self.plugin_manager.metadata if self.plugin_manager else {"plugins": [], "skills": [], "warnings": []},
        }

    def _failure_report(self, status: str) -> dict[str, str] | None:
        if status == "complete":
            return None
        if status == "awaiting_clarification":
            failure = self.tool_failures.get("policy_rejected")
            if failure:
                return _failure(
                    "policy_rejected",
                    failure.get("label", "安全策略阻止执行"),
                    failure.get("reason", "该操作被安全策略阻止。"),
                    failure.get("recovery", "请补充明确的允许范围，智能体将重新规划且不会自动重放原命令。"),
                )
            return _failure(
                "policy_rejected",
                "等待补充允许范围",
                "上一条命令被安全策略阻止。",
                "补充明确的允许范围后继续任务；原命令不会被自动重放。",
            )
        for failure_type in ("awaiting_approval", "review_required"):
            failure = self.tool_failures.get(failure_type)
            if failure:
                return _failure(
                    failure_type,
                    failure.get("label", "等待用户操作"),
                    failure.get("reason", "需要用户操作后才能继续。"),
                    failure.get("recovery", "完成操作后继续任务。"),
                )
        if self.model_error:
            return _failure(
                "model_request_failed",
                "模型请求失败",
                self.model_error,
                "检查模型配置、网络连接和服务额度后重试。",
            )
        for failure_type in ("invalid_tool_protocol", "policy_rejected", "timeout", "command_failed"):
            failure = self.tool_failures.get(failure_type)
            if failure:
                return _failure(
                    failure_type,
                    failure.get("label", "工具执行失败"),
                    failure.get("reason", "工具没有返回可用结果。"),
                    failure.get("recovery", "查看工具输出后重试。"),
                )
        if self.state.failed_commands:
            return _failure(
                "verification_failed",
                "验证未通过",
                "最近一次验证命令没有成功。",
                "查看终端输出，修复问题后重新运行验证。",
            )
        return _failure(
            "step_limit_reached",
            "执行步数已达上限",
            "任务在限定轮次内没有完成验证。",
            "缩小任务范围或继续执行，并保留已有验证结果。",
        )
