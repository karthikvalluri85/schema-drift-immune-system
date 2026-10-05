"""`sdis` command line.

    sdis bootstrap                 profile existing loads as the PASSED baseline
    sdis run [--approve] [--confirm-producer]
                                   one synchronous pass of the whole company (no Paperclip needed)
    sdis heartbeat <agent>         entry point for Paperclip's process adapter
    sdis codegen [--check]         render staging models from dbt/sdis_maps
    sdis simulate <scenario>       offline: classify + route a built-in scenario (no Snowflake)
    sdis ledger                    MTTR and credits by drift class
    sdis on-merge --branch <ref>   serverless: a merged remediation PR wakes the Auditor
    sdis producer <INC> --confirm|--reject   serverless: the producer's answer to a semantic incident
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import REPO_ROOT, Settings, load_dotenv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sdis", description="Schema Drift Immune System")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    r = sub.add_parser("run")
    r.add_argument("--approve", action=argparse.BooleanOptionalAction, default=None,
                   help="pre-answer human approval prompts (local mode only)")
    r.add_argument("--confirm-producer", action=argparse.BooleanOptionalAction, default=None)
    h = sub.add_parser("heartbeat")
    h.add_argument("agent", choices=["sentinel", "diagnostician", "surgeon", "diplomat", "auditor"])
    c = sub.add_parser("codegen")
    c.add_argument("--check", action="store_true")
    sim = sub.add_parser("simulate")
    sim.add_argument("scenario", choices=["additive", "rename", "type_widening", "breaking", "semantic", "all"])
    sim.add_argument("--json", action="store_true")
    sub.add_parser("ledger")
    dm = sub.add_parser("demo", help="credential-free demo: land a scenario / show demo state")
    dm.add_argument("action", choices=["land", "status"])
    dm.add_argument("scenario", nargs="?", default="rename")
    sub.add_parser("lineage-snapshot", help="refresh sdis/data/lineage.json from dbt/target/manifest.json")
    om = sub.add_parser("on-merge", help="serverless: an sdis/inc-… PR was merged → Auditor verifies and resolves")
    om.add_argument("--branch", required=True)
    pr = sub.add_parser("producer", help="serverless: record the producer's answer for a semantic incident")
    pr.add_argument("incident")
    g = pr.add_mutually_exclusive_group(required=True)
    g.add_argument("--confirm", action="store_true")
    g.add_argument("--reject", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "heartbeat":  # woken by Paperclip: pick up GitHub/Jira secrets from the repo's .env
        load_dotenv(Path(os.environ.get("SDIS_REPO_DIR") or REPO_ROOT) / ".env")
    s = Settings()

    if a.cmd == "codegen":
        from . import codegen
        stale = codegen.run(s.dbt_dir, check=a.check)
        if a.check and stale:
            print("Generated files are stale — run `sdis codegen`:\n  " + "\n  ".join(stale))
            return 1
        print("codegen: " + (f"{len(stale)} file(s) rendered" if stale else "up to date"))
        return 0
    if a.cmd == "simulate":
        from .simulate import simulate
        return simulate(a.scenario, as_json=a.json)
    if a.cmd == "bootstrap":
        from .runner import run_local
        run_local(s, do_bootstrap=True)
        return 0
    if a.cmd == "run":
        from .runner import run_local
        outs = run_local(s, approve=a.approve, confirm=a.confirm_producer)
        print(json.dumps([{"agent": o.agent, "status": o.status, "incident": o.incident_id} for o in outs], indent=2))
        return 0
    if a.cmd == "heartbeat":
        from .runner import heartbeat
        heartbeat(a.agent, s)
        return 0
    if a.cmd == "demo":
        from . import demo
        if a.action == "land":
            demo.land(a.scenario)
            print(f"landed '{a.scenario}' (load {__import__('sdis.scenarios', fromlist=['x']).NEW_LOAD}) in "
                  f"{demo.state_path()}. Run agents with SDIS_DEMO={a.scenario}.")
        else:
            print(json.dumps(demo.status(), indent=2, default=str))
        return 0
    if a.cmd == "lineage-snapshot":
        from .blast_radius import LINEAGE_SNAPSHOT, load_manifest, slim_manifest
        LINEAGE_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        LINEAGE_SNAPSHOT.write_text(json.dumps(slim_manifest(load_manifest(s.dbt_dir)), indent=1, sort_keys=True) + "\n")
        print(f"wrote {LINEAGE_SNAPSHOT}")
        return 0
    if a.cmd == "on-merge":
        from .context import Task
        from .runner import incident_from_branch, run_task
        inc = incident_from_branch(a.branch)
        if not inc:
            print(f"{a.branch} is not an SDIS remediation branch; nothing to do")
            return 0
        outs = run_task(Task(agent="auditor", title=f"Verify & close {inc}", description="merged on GitHub",
                             incident_id=inc, meta={"approved": "true"}), s)
        print(json.dumps([{"agent": o.agent, "status": o.status, "incident": o.incident_id} for o in outs], indent=2))
        return 0 if all(o.status != "blocked" for o in outs) else 1
    if a.cmd == "producer":
        from .context import Task
        from .runner import run_task
        outs = run_task(Task(agent="diplomat", title=f"Producer answer for {a.incident}", description="",
                             incident_id=a.incident.upper()), s, confirm=bool(a.confirm))
        print(json.dumps([{"agent": o.agent, "status": o.status, "incident": o.incident_id} for o in outs], indent=2))
        return 0
    if a.cmd == "ledger":
        from .agents.auditor import ledger
        from .context import Context
        print(json.dumps(ledger(Context(settings=s, agent="auditor")), indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
