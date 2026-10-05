---
schema: agentcompanies/v1
kind: team
slug: drift-response
name: Drift Response
description: Detect → diagnose → repair → communicate → audit, for every upstream schema change.
manager: ../../agents/head-of-data-reliability/AGENTS.md
includes:
  - ../../agents/sentinel/AGENTS.md
  - ../../agents/diagnostician/AGENTS.md
  - ../../agents/surgeon/AGENTS.md
  - ../../agents/diplomat/AGENTS.md
  - ../../agents/auditor/AGENTS.md
  - ../../skills/sdis-heartbeat/SKILL.md
tags:
  - team
  - data-reliability
---

The Drift Response pod. Deterministic agents do the work in seconds; the Head of Data
Reliability and the board (you) approve anything SEV1/SEV2.
