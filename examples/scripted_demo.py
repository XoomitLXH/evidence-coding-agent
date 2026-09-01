from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coding_agent.agent_loop import AgentLoop


FIXTURE = Path(__file__).with_name("buggy_calculator")
TEST_COMMAND = "python3 -m unittest discover -s tests -v"
PATCH_ARGUMENTS = {
    "path": "calculator.py",
    "old_text": "return left - right",
    "new_text": "return left + right",
}


def tool_response(name: str, arguments: dict[str, object], call_id: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


class ScriptedModel:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = responses

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return self.responses.pop(0)


def prepare_workspace(destination: Path) -> Path:
    workspace = destination.expanduser().resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(f"demo workspace must be empty: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, workspace, dirs_exist_ok=True)
    return workspace


def run_demo(workspace: Path) -> dict[str, Any]:
    prepared_workspace = prepare_workspace(workspace)
    responses = [
        tool_response("run_command", {"command": TEST_COMMAND}, "demo-1"),
        tool_response("read_file", {"path": "calculator.py"}, "demo-2"),
        tool_response("apply_patch", PATCH_ARGUMENTS, "demo-3"),
        tool_response("run_command", {"command": TEST_COMMAND}, "demo-4"),
        tool_response(
            "finish",
            {"summary": "Fixed add() and verified the regression test."},
            "demo-5",
        ),
    ]
    report = AgentLoop(
        prepared_workspace,
        ScriptedModel(responses),
        max_steps=len(responses),
        require_draft_review=False,
    ).run(
        "Fix add() and run the regression test."
    )
    return {
        "workspace": str(prepared_workspace),
        "status": report["status"],
        "phase": report["phase"],
        "modified_files": report["modified_files"],
        "verification_exit_codes": [item["exit_code"] for item in report["verification"]],
        "log": str(prepared_workspace / "run.jsonl"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic evidence-coding-agent demo")
    parser.add_argument("--workspace", help="an empty directory that will receive the demo repository")
    args = parser.parse_args()
    workspace = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="coding-agent-demo-"))
    try:
        payload = run_demo(workspace)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
