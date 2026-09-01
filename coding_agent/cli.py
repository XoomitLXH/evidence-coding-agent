from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_loop import AgentLoop
from .config import Config
from .model_client import OpenAICompatibleClient
from .plugins import PluginManager


def _skill_values(values: list[str] | None) -> list[str]:
    """Accept repeated --skills flags as well as comma-separated values."""
    result: list[str] = []
    for value in values or []:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-driven local coding agent")
    parser.add_argument("task", nargs="?", help="programming task for the agent")
    parser.add_argument("--repo", default=".", help="workspace root")
    parser.add_argument("--log", default=None, help="JSONL trajectory path")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="additional plugin directory or plugin root (repeatable)",
    )
    parser.add_argument(
        "--skills",
        action="append",
        default=[],
        metavar="NAME[,NAME]",
        help="explicit skill names (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="list discovered plugins and skills, then exit",
    )
    args = parser.parse_args()
    root = Path(args.repo).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"workspace does not exist: {root}")
    plugin_dirs = [Path(item).expanduser() for item in args.plugin_dir]
    explicit_skills = _skill_values(args.skills)
    if args.list_plugins:
        print(json.dumps(PluginManager(root, plugin_dirs).metadata, ensure_ascii=False, indent=2))
        return 0
    if not args.task or not args.task.strip():
        parser.error("task is required unless --list-plugins is used")
    config = Config.from_env(model=args.model, base_url=args.base_url, max_steps=args.max_steps)
    log_path = Path(args.log).expanduser().resolve() if args.log else root / "run.jsonl"
    report = AgentLoop(
        root,
        OpenAICompatibleClient(config),
        log_path=log_path,
        max_steps=config.max_steps,
        plugin_dirs=plugin_dirs,
        explicit_skills=explicit_skills,
    ).run(args.task)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
