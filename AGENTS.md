# AGENTS.md — house rules for coding agents working in this repo

These rules apply to any AI coding agent (Claude Code, Codex, Cursor…) and to humans.

## Never
- Never write to the Snowflake `RAW` schema from SDIS code. Reads only. (`invariants.check_sql_is_safe`)
- Never let model output (Cortex narratives, LLM text) influence class, severity, gate or approvals.
- Never hand-edit `dbt/models/staging/stg_*.sql` or `stg_*.yml` — edit `dbt/sdis_maps/*.yml` and run `sdis codegen`.
- Never commit credentials. Snowflake uses key-pair auth from env vars; tokens come from env / Paperclip secrets.
- Never merge a SEV1/SEV2 remediation without a recorded approval, or anything with non-green CI.

## Always
- Add or change behaviour in `policies/playbooks.yaml` first, then code; `tests/test_policies.py` keeps them in sync.
- Every new invariant in `policies/invariants.yaml` needs an enforcing function in `sdis/invariants.py` and a test.
- Every agent handler returns an `AgentOutput` with a non-empty `decisions[]` (what, why, alternatives rejected).
- Keep remediations minimal: change mappings, never downstream aliases.
- Run before pushing: `ruff check . && sdis codegen --check && pytest -q`.

## Layout
`sdis/engine.py` is the pure core (diff → classify → blast radius → route). IO lives at the edges:
`warehouse.py` (Snowflake), `github.py`, `jira.py`, `paperclip.py`. Agents in `sdis/agents/` compose them.
