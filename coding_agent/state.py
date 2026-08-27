from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentState:
    MAX_LEDGER_ENTRIES = 50
    phase: str = "EXPLORE"
    dirty: bool = False
    modified_files: set[str] = field(default_factory=set)
    verification: list[dict] = field(default_factory=list)
    ledger: list[str] = field(default_factory=list)
    failed_commands: int = 0
    turn: int = 0
    final_summary: str = ""
    revision: int = 0
    verified_revision: int | None = None

    def add_ledger(self, message: str) -> None:
        self.ledger.append(message)
        del self.ledger[:-self.MAX_LEDGER_ENTRIES]

    @property
    def last_verification_pass(self) -> bool:
        return bool(
            self.verification
            and self.verification[-1].get("exit_code") == 0
            and self.verification[-1].get("is_clean_verification")
        )

    def mark_modified(self, paths: list[str]) -> None:
        self.revision += 1
        self.dirty = True
        self.modified_files.update(paths)
        self.phase = "VERIFY"
        self.add_ledger(f"Modified: {', '.join(paths)}; verification is required.")

    def record_command(
        self,
        command: str,
        exit_code: int,
        output: str,
        duration_ms: int,
        *,
        workspace_changed: bool = False,
    ) -> None:
        is_clean_verification = exit_code == 0 and not workspace_changed
        self.verification.append({
            "command": command,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "output": output[-4000:],
            "timestamp": now_iso(),
            "revision": self.revision,
            "is_clean_verification": is_clean_verification,
        })
        if is_clean_verification:
            self.verified_revision = self.revision
            self.dirty = False
            self.phase = "VERIFY"
            self.add_ledger(f"Verification passed: {command}")
        elif exit_code == 0:
            self.phase = "VERIFY"
            self.add_ledger(f"Command changed workspace: {command}. Run a clean verification command.")
        else:
            self.phase = "EDIT"
            self.failed_commands += 1
            self.add_ledger(f"Verification failed ({exit_code}): {command}. Inspect output and repair.")

    def can_finish(self) -> bool:
        return (
            self.last_verification_pass
            and self.verified_revision == self.revision
            and not self.dirty
            and self.phase == "VERIFY"
        )

    def prompt_context(self) -> str:
        evidence = "none" if not self.verification else "; ".join(
            f"{item['command']} => exit {item['exit_code']}" for item in self.verification[-4:]
        )
        files = ", ".join(sorted(self.modified_files)) or "none"
        ledger = " | ".join(self.ledger[-5:]) or "none"
        return f"phase={self.phase}; modified_files={files}; verification={evidence}; ledger={ledger}"
