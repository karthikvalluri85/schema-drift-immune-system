---
schema: agentcompanies/v1
kind: company
slug: schema-drift-immune-system
name: Schema Drift Immune System
description: An autonomous data-reliability company that detects, contains and fixes upstream schema drift in a dbt + Snowflake platform — before dashboards break.
version: 0.1.0
license: MIT
authors:
  - name: Karthik Valluri
homepage: https://github.com/karthikvalluri85/schema-drift-immune-system
goals:
  - Contain every upstream schema change before it reaches a dashboard (time to contain < 5 minutes)
  - Resolve additive, rename and type-widening drift with a reviewed PR in under 1 hour
  - Never let semantic drift (same name, new meaning) reach finance reporting
  - Keep a cost and MTTR ledger per incident so data reliability has unit economics
tags:
  - data-engineering
  - dbt
  - snowflake
  - data-reliability
requirements:
  secrets:
    - GITHUB_TOKEN
    - JIRA_API_TOKEN
---

# Schema Drift Immune System

Upstream teams change schemas without warning: a column is renamed, a type widens, a flag
replaces a code, a currency silently switches. This company treats those changes like an
immune system treats a pathogen — **detect, contain, repair, remember**.

## How the company works

| Stage | Agent | What happens | Tech |
|---|---|---|---|
| Detect | **Sentinel** | Snapshots `INFORMATION_SCHEMA`, profiles every new Bronze load (MINHASH, HLL, medians, top-k) | Snowflake |
| Diagnose | **Diagnostician** | Classifies drift (additive · rename · type widening · breaking · semantic), computes blast radius from dbt lineage, routes by playbook, sets the load gate | Python rules, dbt manifest |
| Contain | **Diagnostician** | Load gate `PASS / HOLD / QUARANTINE` — dbt staging only reads PASSED batches | Snowflake + dbt macro |
| Repair | **Surgeon** | Edits one mapping line, re-renders staging, opens a PR; merges only when CI is green and policy allows | GitHub, dbt |
| Communicate | **Diplomat** | Jira ticket with blast radius + producer note; asks the producer to confirm semantic changes | Jira |
| Remember | **Auditor** | Releases the gate, runs `dbt build`, resolves, records MTTR and Snowflake credits per incident | Snowflake ACCOUNT_USAGE |
| Govern | **Head of Data Reliability** | Reviews SEV1/SEV2 remediations, runs the weekly reliability review | Claude Code |

## House rules (enforced in code — see `policies/invariants.yaml`)

1. Routing is deterministic. LLM output (Cortex narratives) is never an input to routing.
2. Agents never write to Bronze. Quarantine hides a batch; it never deletes one.
3. SEV1/SEV2 fixes need a recorded human approval. Nothing merges with red CI.
4. Every agent run leaves its decisions — and the alternatives it rejected — in the trace.
