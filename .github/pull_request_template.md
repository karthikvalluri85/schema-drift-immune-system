## What & why
<!-- Agent PRs fill this automatically with the incident summary. -->

## Checklist
- [ ] Only `dbt/sdis_maps/`, source docs or generated staging files changed for a drift fix
- [ ] Staging aliases / contract unchanged (except an intended widening)
- [ ] `sdis codegen --check` and `pytest` pass
- [ ] Slim CI built the same models the blast radius lists
- [ ] SEV1/SEV2: approval recorded in Paperclip
