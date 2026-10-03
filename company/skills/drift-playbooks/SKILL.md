---
name: drift-playbooks
description: Reference for the five SDIS drift classes, severity matrix and playbook routes.
metadata:
  paperclip:
    tags: [reference, data-reliability]
---

# Drift playbooks (source of truth: `policies/playbooks.yaml`)

| Class | Route | Gate | Surgeon | Diplomat | Auto-merge |
|---|---|---|---|---|---|
| additive | auto_document | PASS | document column | — | when green |
| type_widening | contract_update_pr | HOLD | update contract type | FYI | when green (SEV3/4) |
| rename | compat_alias_pr | HOLD | remap source column | Jira + note | never |
| breaking | quarantine_and_remap | QUARANTINE | draft remap proposal | Jira + note | never |
| semantic | quarantine_and_confirm | QUARANTINE | normalise after confirmation | Jira + confirmation | never |

Severity = `severity[class][blast tier]`; blast tier HIGH if any tier-1 exposure or ≥4 models.
SEV1/SEV2 always need a recorded human approval.
