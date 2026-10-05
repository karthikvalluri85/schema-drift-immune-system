"""Loads the deterministic routing table (policies/playbooks.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import REPO_ROOT


@lru_cache(maxsize=4)
def load_playbooks(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else REPO_ROOT / "policies" / "playbooks.yaml"
    return yaml.safe_load(p.read_text())


@lru_cache(maxsize=4)
def load_invariants(path: str | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else REPO_ROOT / "policies" / "invariants.yaml"
    return yaml.safe_load(p.read_text())["rules"]
