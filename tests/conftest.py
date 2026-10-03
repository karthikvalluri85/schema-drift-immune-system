from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def manifest_dbt_dir(tmp_path_factory) -> Path:
    """dbt project copy with a parsed manifest.json (offline; dummy credentials)."""
    dst = tmp_path_factory.mktemp("dbt") / "dbt"
    shutil.copytree(ROOT / "dbt", dst, ignore=shutil.ignore_patterns("target", "logs", "dbt_packages"))
    env = {**os.environ, "SNOWFLAKE_ACCOUNT": "dummy", "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null",
           "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    subprocess.run(["dbt", "parse", "--profiles-dir", ".", "--no-partial-parse", "--quiet"],
                   cwd=dst, env=env, check=True, capture_output=True)
    return dst
