"""`sdis` command line.

    sdis bootstrap                 profile existing loads as the PASSED baseline
    sdis run [--approve] [--confirm-producer]
                                   one synchronous pass of the whole company (no Paperclip needed)
    sdis heartbeat <agent>         entry point for Paperclip's process adapter
    sdis codegen [--check]         render staging models from dbt/sdis_maps
    sdis simulate <scenario>       offline: classify + route a built-in scenario (no Snowflake)
    sdis ledger                    MTTR and credits by drift class
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings


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
    a = p.parse_args(argv)
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
    if a.cmd == "ledger":
        from .agents.auditor import ledger
        from .context import Context
        print(json.dumps(ledger(Context(settings=s, agent="auditor")), indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
