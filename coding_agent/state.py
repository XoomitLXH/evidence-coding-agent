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
    invalid_tool_protocols: int = 0
    turn: int = 0
    final_summary: str = ""
    revision: int = 0
    verified_revision: int | None = None

    def to_dict(self) -> dict:
        """Return the runtime state needed to resume an interrupted agent loop."""
        return {
            "phase": self.phase,
            "dirty": self.dirty,
            "modified_files": sorted(self.modified_files),
            "verification": list(self.verification),
            "ledger": list(self.ledger),
            "failed_commands": self.failed_commands,
            "invalid_tool_protocols": self.invalid_tool_protocols,
            "turn": self.turn,
            "final_summary": self.final_summary,
            "revision": self.revision,
            "verified_revision": self.verified_revision,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AgentState":
        if not isinstance(payload, dict):
            raise ValueError("agent state snapshot must be an object")
        verification = payload.get("verification", [])
        ledger = payload.get("ledger", [])
        modified_files = payload.get("modified_files", [])
        if not isinstance(verification, list) or not isinstance(ledger, list) or not isinstance(modified_files, list):
            raise ValueError("agent state snapshot has invalid collection fields")
        verified_revision = payload.get("verified_revision")
        if verified_revision is not None and not isinstance(verified_revision, int):
            raise ValueError("agent state snapshot has invalid verified revision")
        return cls(
            phase=str(payload.get("phase", "EXPLORE")),
            dirty=bool(payload.get("dirty", False)),
            modified_files={str(path) for path in modified_files},
            verification=[item for item in verification if isinstance(item, dict)],
            ledger=[str(item) for item in ledger][-cls.MAX_LEDGER_ENTRIES:],
            failed_commands=int(payload.get("failed_commands", 0)),
            invalid_tool_protocols=int(payload.get("invalid_tool_protocols", 0)),
            turn=int(payload.get("turn", 0)),
            final_summary=str(payload.get("final_summary", "")),
            revision=int(payload.get("revision", 0)),
            verified_revision=verified_revision,
        )

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

    def record_invalid_tool_protocol(self, reason: str) -> None:
        self.invalid_tool_protocols += 1
        self.add_ledger(f"Invalid tool protocol: {reason}")

    def can_finish(self) -> bool:
        if not self.modified_files:
            return self.failed_commands == 0 and self.invalid_tool_protocols == 0
        return (
            self.last_verification_pass
            and self.verified_revision == self.revision
            and not self.dirty
            and self.phase in {"VERIFY", "COMPLETE"}
        )

    def prompt_context(self) -> str:
        evidence = "none" if not self.verification else "; ".join(
            f"{item['command']} => exit {item['exit_code']}" for item in self.verification[-4:]
        )
        files = ", ".join(sorted(self.modified_files)) or "none"
        ledger = " | ".join(self.ledger[-5:]) or "none"
        return f"phase={self.phase}; modified_files={files}; verification={evidence}; ledger={ledger}"
