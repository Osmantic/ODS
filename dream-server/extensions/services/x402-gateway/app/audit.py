from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self._disabled_reason: str | None = None
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._disable(f"cannot create audit directory {self.path.parent}: {exc}")

    def write(self, event: str, payload: dict[str, Any]) -> None:
        if not self.path or self._disabled_reason:
            return
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as exc:
            self._disable(f"cannot write audit log {self.path}: {exc}")

    def _disable(self, reason: str) -> None:
        self._disabled_reason = reason
        print(f"x402-gateway audit disabled: {reason}", file=sys.stderr, flush=True)
