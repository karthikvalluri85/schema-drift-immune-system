---
name: drift-review
description: Review an SDIS Surgeon pull request for a SEV1/SEV2 drift incident and recommend approve or changes.
metadata:
  paperclip:
    tags: [review, dbt, data-reliability]
---

# Reviewing an SDIS remediation PR

Checklist — answer each in your comment:

1. **Scope.** Only `dbt/sdis_maps/`, `dbt/models/staging/_sources.yml` and generated staging files
   changed? Any edit to marts is a red flag.
2. **Contract.** Do all staging aliases and contract types stay the same (except an intended
   widening)? Downstream models must not need edits.
3. **Evidence.** Does the PR's decision list support the class? Rename: value containment ≥ 0.9.
   Semantic: a named hypothesis and, ideally, a restatement ratio with low variation.
4. **CI.** Slim CI (`state:modified+ --defer`) green, and the models it built match the blast
   radius listed in the PR.
5. **Breaking/proposed mappings.** Is the proposed mapping plausible from the value shares, and is
   the history backfill question (Time Travel clone) answered?

End with exactly one line: `Recommendation: APPROVE` or `Recommendation: CHANGES — <why>`.
You can reproduce any decision offline with `sdis simulate <scenario>`.
