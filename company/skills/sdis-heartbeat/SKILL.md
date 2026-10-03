---
name: sdis-heartbeat
description: How SDIS process agents follow the Paperclip heartbeat protocol via `sdis heartbeat <agent>`.
metadata:
  paperclip:
    tags: [data-reliability, heartbeat]
---

# SDIS heartbeat

`sdis heartbeat <agent>` implements the Paperclip heartbeat protocol for deterministic agents:

1. `GET /api/agents/me` → budget check (≥80% → SEV1/SEV2 only; 100% → exit).
2. List assignments (`todo, in_progress, in_review, blocked`), prioritising `PAPERCLIP_TASK_ID`.
3. `POST /api/issues/{id}/checkout` — a 409 means another agent owns it; skip, never retry.
4. Run the agent handler. Incident context comes from `sdis-incident:` / `sdis-*:` lines in the
   issue description; state lives in `SDIS_DB.DRIFT`.
5. `PATCH` the issue with a comment listing artifacts, **decisions and rejected alternatives**,
   and the next step. Status: success → `done`, waiting (CI/approval) → `in_progress`,
   blocked → `blocked`.
6. Delegation = child issues with `parentId`, never polling another agent.

Everything is also written to `traces/trace_events.jsonl` (operational / cognitive / contextual).
