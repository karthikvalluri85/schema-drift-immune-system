"""Agent handlers. Each takes (ctx, task) and returns an AgentOutput.

    sentinel      → watches RAW, snapshots schemas, profiles every new load
    diagnostician → classifies drift, computes blast radius, routes, sets the load gate
    surgeon       → patches dbt maps, opens PRs, merges when policy allows
    diplomat      → Jira ticket + producer note / confirmation
    auditor       → verifies, releases the gate, resolves, keeps the cost & MTTR ledger
"""
from . import auditor, diagnostician, diplomat, sentinel, surgeon

HANDLERS = {
    "sentinel": sentinel.handle,
    "diagnostician": diagnostician.handle,
    "surgeon": surgeon.handle,
    "diplomat": diplomat.handle,
    "auditor": auditor.handle,
}
