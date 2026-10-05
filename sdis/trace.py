"""Three-surface JSONL trace (operational / cognitive / contextual).

Adapted from the AgentTrace-inspired tracing in aws-samples/sample-Agentic-Ai-Data-Operations:
every event links to a run_id + span so a whole incident can be replayed from the log.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: Path, run_id: str | None = None, agent: str = "sdis"):
        self.path = Path(path)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.agent = agent
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, surface: str, event: str, incident_id: str | None = None, **attrs: Any) -> None:
        assert surface in {"operational", "cognitive", "contextual"}
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": self.run_id,
            "span_id": uuid.uuid4().hex[:8],
            "agent": self.agent,
            "surface": surface,
            "event": event,
            "incident_id": incident_id,
            "attrs": attrs,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def output(self, output: Any) -> None:
        d = output.to_dict() if hasattr(output, "to_dict") else dict(output)
        for dec in d.get("decisions", []):
            self.emit("cognitive", "decision", d.get("incident_id"), **dec)
        self.emit("operational", "agent_output", d.get("incident_id"), status=d.get("status"),
                  artifacts=d.get("artifacts"), warnings=d.get("warnings"), next_steps=d.get("next_steps"))
