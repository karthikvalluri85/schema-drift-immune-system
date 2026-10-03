"""Git + GitHub REST for the Surgeon (branch → commit → push → PR → merge when allowed)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import requests

from . import invariants
from .config import Settings

API = "https://api.github.com"
BOT_NAME = "sdis-surgeon[bot]"
BOT_EMAIL = "sdis-surgeon@users.noreply.github.com"


class GitHub:
    def __init__(self, s: Settings):
        self.s = s

    def _h(self) -> dict[str, str]:
        if not self.s.github_token:
            raise RuntimeError("GITHUB_TOKEN is not set")
        return {"Authorization": f"Bearer {self.s.github_token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = requests.request(method, f"{API}/repos/{self.s.github_repo}{path}", headers=self._h(), timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ------------------------------------------------------------------ git
    def _git(self, *args: str, cwd: Path | None = None) -> str:
        out = subprocess.run(["git", *args], cwd=cwd or self.s.repo_dir, check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def worktree(self, branch: str) -> Path:
        """Fresh, isolated checkout of the base branch for one incident (never touches the main clone)."""
        wt = self.s.repo_dir.parent / ".sdis-worktrees" / branch.replace("/", "_")
        self._git("fetch", "origin", self.s.github_base_branch)
        if wt.exists():
            self._git("worktree", "remove", "--force", str(wt))
        self._git("worktree", "add", "-B", branch, str(wt), f"origin/{self.s.github_base_branch}")
        return wt

    def commit_and_push(self, wt: Path, branch: str, files: list[str], message: str) -> str:
        for f in files:
            invariants.check_no_secrets(Path(f).read_text(), where=f)
        self._git("add", "-A", "dbt", cwd=wt)
        self._git("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-m", message, cwd=wt)
        remote = f"https://x-access-token:{self.s.github_token}@github.com/{self.s.github_repo}.git"
        self._git("push", "--force", remote, f"HEAD:refs/heads/{branch}", cwd=wt)
        return self._git("rev-parse", "HEAD", cwd=wt)

    def drop_worktree(self, wt: Path) -> None:
        self._git("worktree", "remove", "--force", str(wt))

    # ------------------------------------------------------------------ pulls
    def open_pr(self, branch: str, title: str, body: str, draft: bool, labels: list[str]) -> dict[str, Any]:
        existing = self._req("GET", "/pulls", params={"head": f"{self.s.github_repo.split('/')[0]}:{branch}",
                                                     "state": "open"})
        if existing:
            return existing[0]
        pr = self._req("POST", "/pulls", json={"title": title, "head": branch, "base": self.s.github_base_branch,
                                              "body": body, "draft": draft, "maintainer_can_modify": True})
        if labels:
            try:
                self._req("POST", f"/issues/{pr['number']}/labels", json={"labels": labels})
            except requests.HTTPError:
                pass  # labels are cosmetic
        return pr

    def pr(self, number: int) -> dict[str, Any]:
        return self._req("GET", f"/pulls/{number}")

    def ci_state(self, sha: str) -> str:
        """'success' | 'pending' | 'failure' from GitHub check runs on the head commit."""
        runs = self._req("GET", f"/commits/{sha}/check-runs").get("check_runs", [])
        if not runs:
            return "pending"
        if any(r["status"] != "completed" for r in runs):
            return "pending"
        bad = {"failure", "cancelled", "timed_out", "action_required"}
        return "failure" if any(r["conclusion"] in bad for r in runs) else "success"

    def merge(self, number: int, severity: str, approved: bool, human_sevs: list[str]) -> dict[str, Any]:
        pr = self.pr(number)
        invariants.check_merge_allowed(severity, approved, human_sevs)
        invariants.check_ci_green(self.ci_state(pr["head"]["sha"]))
        if pr.get("draft"):
            self._req("PATCH", f"/pulls/{number}", json={"draft": False})
        return self._req("PUT", f"/pulls/{number}/merge", json={"merge_method": "squash"})

    def comment(self, number: int, body: str) -> None:
        self._req("POST", f"/issues/{number}/comments", json={"body": body})
