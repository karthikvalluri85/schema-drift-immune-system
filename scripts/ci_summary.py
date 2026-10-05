"""Render dbt run_results.json as a GitHub job summary (what Slim CI actually built)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> None:
    p = Path(path)
    if not p.exists():
        print("_No run_results.json — nothing was built._")
        return
    res = json.loads(p.read_text(encoding="utf-8"))["results"]
    models = sorted(r["unique_id"].split(".")[-1] for r in res if r["unique_id"].startswith("model."))
    tests = [r for r in res if r["unique_id"].startswith("test.")]
    failed = [r["unique_id"] for r in res if r["status"] in ("error", "fail")]
    print("### Slim CI (`state:modified+ --defer`)")
    print(f"- Models built ({len(models)}): {', '.join(models) or '—'}")
    print(f"- Tests: {len(tests)} run, {len(failed)} failed")
    if failed:
        print("- ❌ " + ", ".join(failed))
    print("\nCompare the models above with the **Blast radius** section of the PR description: "
          "they should match.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dbt/target/run_results.json")
