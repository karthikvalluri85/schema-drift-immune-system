"""Runtime configuration — environment variables only (invariant: no-credentials-in-code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    repo_dir: Path = field(default_factory=lambda: Path(_env("SDIS_REPO_DIR", str(REPO_ROOT))))
    # Snowflake (key-pair auth for the SDIS_AGENT_SVC service user)
    sf_account: str | None = field(default_factory=lambda: _env("SNOWFLAKE_ACCOUNT"))
    sf_user: str = field(default_factory=lambda: _env("SNOWFLAKE_USER", "SDIS_AGENT_SVC"))
    sf_private_key_path: str | None = field(default_factory=lambda: _env("SNOWFLAKE_PRIVATE_KEY_PATH"))
    sf_role: str = field(default_factory=lambda: _env("SNOWFLAKE_ROLE", "SDIS_AGENT_ROLE"))
    sf_warehouse: str = field(default_factory=lambda: _env("SNOWFLAKE_WAREHOUSE", "SDIS_WH"))
    sf_database: str = field(default_factory=lambda: _env("SNOWFLAKE_DATABASE", "SDIS_DB"))
    raw_schema: str = field(default_factory=lambda: _env("SDIS_RAW_SCHEMA", "RAW"))
    drift_schema: str = field(default_factory=lambda: _env("SDIS_DRIFT_SCHEMA", "DRIFT"))
    cortex_model: str = field(default_factory=lambda: _env("SDIS_CORTEX_MODEL", "claude-sonnet-4-5"))
    # GitHub (Surgeon)
    github_repo: str = field(default_factory=lambda: _env("GITHUB_REPOSITORY", "karthikvalluri85/schema-drift-immune-system"))
    github_token: str | None = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    github_base_branch: str = field(default_factory=lambda: _env("SDIS_BASE_BRANCH", "main"))
    # Jira (Diplomat)
    jira_base_url: str = field(default_factory=lambda: _env("JIRA_BASE_URL", "https://karthikvalluri1985.atlassian.net"))
    jira_email: str | None = field(default_factory=lambda: _env("JIRA_EMAIL"))
    jira_api_token: str | None = field(default_factory=lambda: _env("JIRA_API_TOKEN"))
    jira_project_key: str = field(default_factory=lambda: _env("JIRA_PROJECT_KEY", "SDIS"))
    jira_issue_type: str = field(default_factory=lambda: _env("JIRA_ISSUE_TYPE", "Task"))
    # Paperclip (injected by the heartbeat runtime)
    paperclip_api_url: str | None = field(default_factory=lambda: _env("PAPERCLIP_API_URL"))
    paperclip_api_key: str | None = field(default_factory=lambda: _env("PAPERCLIP_API_KEY"))
    paperclip_agent_id: str | None = field(default_factory=lambda: _env("PAPERCLIP_AGENT_ID"))
    paperclip_company_id: str | None = field(default_factory=lambda: _env("PAPERCLIP_COMPANY_ID"))
    paperclip_run_id: str | None = field(default_factory=lambda: _env("PAPERCLIP_RUN_ID"))
    paperclip_task_id: str | None = field(default_factory=lambda: _env("PAPERCLIP_TASK_ID"))
    # Behaviour — adoption mode for production rollouts:
    #   observe → detect, classify, ticket; never gate loads, never open PRs   (zero risk, no dbt changes)
    #   advise  → observe + Surgeon PRs; still never gates loads               (humans merge)
    #   protect → full immune system: load gate + PRs + approvals              (needs the sdis_load_gate macro)
    mode: str = field(default_factory=lambda: _env("SDIS_MODE", "protect").lower())
    # Where human approvals happen when not running under Paperclip:
    #   cli    → asked in the terminal (or pre-answered with --approve / --confirm-producer)
    #   github → serverless runtime: merging the PR IS the approval (branch protection requires a review);
    #            producer confirmations arrive as a workflow_dispatch / repository_dispatch event
    approval_channel: str = field(default_factory=lambda: _env("SDIS_APPROVAL_CHANNEL", "cli").lower())
    dry_run: bool = field(default_factory=lambda: _env("SDIS_DRY_RUN", "false").lower() == "true")
    trace_path: Path = field(default_factory=lambda: Path(_env("SDIS_TRACE_PATH", "traces/trace_events.jsonl")))

    @property
    def dbt_dir(self) -> Path:
        return self.repo_dir / "dbt"

    @property
    def policies_dir(self) -> Path:
        return self.repo_dir / "policies"

    def raw_fqn(self, table: str) -> str:
        return f"{self.sf_database}.{self.raw_schema}.{table.upper()}"

    def drift_fqn(self, table: str) -> str:
        return f"{self.sf_database}.{self.drift_schema}.{table.upper()}"
