from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_loop import AgentLoop
from .config import Config
from .model_client import OpenAICompatibleClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-driven local coding agent")
    parser.add_argument("task", help="programming task for the agent")
    parser.add_argument("--repo", default=".", help="workspace root")
    parser.add_argument("--log", default=None, help="JSONL trajectory path")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args()
    root = Path(args.repo).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"workspace does not exist: {root}")
    config = Config.from_env(model=args.model, base_url=args.base_url, max_steps=args.max_steps)
    log_path = Path(args.log).expanduser().resolve() if args.log else root / "run.jsonl"
    report = AgentLoop(root, OpenAICompatibleClient(config), log_path=log_path, max_steps=config.max_steps).run(args.task)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
