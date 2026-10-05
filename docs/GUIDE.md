# Schema Drift Immune System — the complete guide

This guide walks you through the whole solution. It covers what each piece of technology does, what happens in each of the five drift scenarios, and how to run it **entirely free**: GitHub Actions as the always-on runtime, Streamlit in Snowflake and Streamlit Community Cloud for dashboards, and Paperclip on your laptop to show the org. FastAPI is explained from zero. The guide ends with a tour of the Paperclip org, the Streamlit dashboard, and what FastAPI and the free stack add. A paid server (DigitalOcean) is an optional appendix.

---

## 1. The 60-second version

An upstream team changes a table and doesn't tell anyone. dbt models break, or worse, keep running with wrong numbers. SDIS runs as a small **company of agents inside Paperclip**:

1. **Sentinel** notices that a new batch of Bronze data looks different.
2. **Diagnostician** works out *what kind* of change it is, *how much would break*, and *what to do*. It does this with rules, not guesses.
3. The **load gate** stops the bad batch from reaching dashboards. Bronze itself is never touched.
4. **Surgeon** opens a one-line pull request with the fix.
5. **Diplomat** opens a Jira ticket and asks the producing team the questions code can't answer.
6. You approve anything serious. The **Auditor** releases the gate, rebuilds, and logs time and cost.

The rest of this guide is the detail behind those six lines.

---

## 2. The tech stack — what each piece does and why it's there

| Layer | Technology | Role in SDIS | Where it runs | Cost to you |
|---|---|---|---|---|
| Data platform | **Snowflake** | Bronze tables (`RAW`), control plane (`DRIFT`), dbt target (`ANALYTICS`) | Your trial account | Trial credits; XS warehouse with 60-second auto-suspend and a 10-credit resource monitor |
| Fingerprints | Snowflake `MINHASH`, `HLL`, `APPROX_TOP_K`, `MEDIAN` | Profiles every load so a rename can be proven by its *values* | Inside Snowflake | One aggregate query per load |
| Narrative | **Snowflake Cortex** `AI_COMPLETE` | Writes the plain-English summary for Jira and the PR, *after* the route is decided | Inside Snowflake | A few cents per incident |
| Evidence | Snowflake **Time Travel** + zero-copy **CLONE** | Preserves the table as it was before a breaking change | Inside Snowflake | ≈ free (metadata only) |
| Transformation | **dbt** (contracts, exposures, `state:modified`) | Staging and marts; lineage gives the blast radius; Slim CI builds only what changed | GitHub Actions + the Auditor | Free |
| Orchestration (showcase) | **Paperclip** | Org chart, issues, heartbeats, budgets, approvals, audit trail | Your laptop, when you present | Free (open source) |
| Runtime (always on) | **GitHub Actions** | Runs the same agents on a schedule and on events: PR merged, producer answered | GitHub | Free on public repos |
| Agents | Python (`sdis`) | 5 deterministic agents: Paperclip's `process` adapter or `sdis run` in Actions | Paperclip or Actions | $0 in LLM tokens |
| Manager agent | **Claude Code** (`claude_local` adapter) | Head of Data Reliability: reviews SEV1/SEV2 PRs, runs the weekly review | Same host | Your Anthropic usage, capped at $20/month by the Paperclip budget |
| Code + CI | **GitHub** + Actions | PRs, Slim CI, prod deploy, security scans | GitHub | Free for public repos |
| Ticketing | **Jira Cloud** | One ticket per incident with blast radius and a producer note | karthikvalluri1985.atlassian.net | Free tier |
| API | **FastAPI** | Webhooks, read API, on-demand scan; in the free setup GitHub Actions takes the events instead | Your laptop | Free |
| Dashboard (live) | **Streamlit in Snowflake** | Command center on your real incidents | Snowflake | Trial credits while open |
| Dashboard (public) | **Streamlit Community Cloud** | Shareable demo-mode dashboard for LinkedIn | share.streamlit.io | Free; sleeps after 12 h idle |

**Why no LangGraph?** Paperclip *is* the orchestration layer. Its issues and heartbeats are the state machine, its org chart is the routing between agents, and it adds budgets, approvals and a UI that LangGraph doesn't have. The routing *inside* an incident is a YAML table (`policies/playbooks.yaml`), which keeps it reproducible and auditable.

---

## 3. One incident, end to end (the common path)

```
Producer changes a table ──► new batch lands in RAW (_LOAD_ID = L20261003)
        │
🛰️ Sentinel  (every 15 min — Paperclip routine "Schema watch")
        │  INFORMATION_SCHEMA snapshot + profile (MINHASH, HLL, medians, top-k)
        │  clean → gate PASSED          drift → gate PENDING + "Diagnose drift" issue
        ▼
🩺 Diagnostician  (woken by the issue assignment)
        │  classify  →  blast radius (dbt manifest)  →  severity  →  gate
        │  saves INCIDENTS + DRIFT_EVENTS + full decision JSON in Snowflake
        │  Cortex narrative (after routing, informational only)
        ├────────────────────────────┐
        ▼                            ▼
🔧 Surgeon                     📣 Diplomat
   edits dbt/sdis_maps            Jira ticket + producer note
   → codegen → PR                 (semantic: asks producer to confirm)
   → Slim CI                            │
   SEV1/2: waits for your approval      │  "/sdis confirm" in Jira → FastAPI → Surgeon
   SEV3/4: merges on green              │
        ▼                               │
🧾 Auditor  (woken by the merge — via Surgeon handoff or the GitHub webhook → FastAPI)
   gate → PASSED  →  dbt build <staging>+  →  RESOLVED, minutes + credits logged
```

Under Paperclip, every arrow above is a **Paperclip issue** (parent incident and child tasks). In the free GitHub Actions runtime the same handlers run inside one workflow run; the merge and the producer's answer each start their own workflow. Either way, every decision is written to Snowflake and to `traces/trace_events.jsonl` (kept as a workflow artifact).

---

## 4. The five scenarios — what happens, step by step

All five use the ShopVerse `RAW.ORDERS` table. The baseline is 7 daily loads of about 3,000 orders, `AMOUNT` in INR with a median around ₹2,400, and 2,000 customers. Each scenario lands a new load `L20261003` after the producer changes something. The numbers below are the actual engine output (`sdis simulate <name>`).

### 4.1 ➕ Additive — `DISCOUNT_CODE` added

| Step | What happens | Tech |
|---|---|---|
| You run | `snowflake/scenarios/01_additive.sql` (`ALTER TABLE … ADD COLUMN DISCOUNT_CODE`) | Snowflake worksheet |
| Sentinel | Schema diff: `added: [DISCOUNT_CODE]` → load held PENDING, issue to the Diagnostician | Snowflake INFORMATION_SCHEMA |
| Diagnostician | `additive/column_added`. Nothing reads the column → **blast radius LOW (0 models)** → **SEV4**, gate **PASS** | Python rules + dbt manifest |
| Surgeon | Documents the column in `_sources.yml`, tagged `sdis: new_unused`. PR **auto-merges** when CI is green | GitHub + Slim CI |
| Diplomat | Nothing. No ticket noise for a safe change | — |
| Auditor | Gate already PASSED → `dbt build` → RESOLVED | dbt |
| You see | One small PR, one resolved Paperclip incident, no Jira ticket | Paperclip, GitHub |

### 4.2 🔁 Rename — `CUST_ID` → `CUSTOMER_ID` ★ demo scenario (highest blast radius)

| Step | What happens | Tech |
|---|---|---|
| You run | `02_rename_DEMO.sql` (`ALTER TABLE … RENAME COLUMN CUST_ID TO CUSTOMER_ID`) | Snowflake |
| Sentinel | To the catalog this looks like `dropped: CUST_ID` + `added: CUSTOMER_ID` | INFORMATION_SCHEMA |
| Diagnostician | Pairs them by **value fingerprint**: containment 0.998 (MINHASH Jaccard + HLL cardinalities), null similarity 1.0, distinct ratio 0.997 → score **0.998 ≥ 0.90 → rename** | `MINHASH_COMBINE`, `APPROXIMATE_SIMILARITY`, `HLL_COMBINE` |
| Blast radius | `stg_orders` → **5 marts** (fct_orders, dim_customers, customer_ltv, fct_revenue_daily, mart_finance_kpis) → **4 dashboards**, including 2 tier-1 (Executive Revenue, Finance Month-End Close) → **HIGH → SEV1** | dbt `manifest.json` + exposures |
| Gate | **HOLD**. The new load stays out; marts keep yesterday's tables instead of erroring | `DRIFT.LOAD_GATE` + `sdis_load_gate()` macro |
| Surgeon | One-line PR: `cast(CUSTOMER_ID as number(38,0)) as cust_id`. The alias doesn't change, so **0 downstream edits**. Slim CI builds exactly those 5 marts | GitHub, `state:modified+ --defer` |
| Diplomat | Jira SEV1 with blast radius: *"we detected CUST_ID was renamed to CUSTOMER_ID — please confirm it is intentional"* | Jira REST |
| Approval | SEV1 → Paperclip asks **you** "approve merging PR #n?". The Head of Data Reliability (Claude) posts a review recommendation first | Paperclip `request_confirmation` |
| Auditor | Merge → gate PASSED → `dbt build stg_orders+` → RESOLVED. Time to contain is under a minute; time to resolve is your approval time | dbt, Snowflake |

### 4.3 ↔️ Type widening — `AMOUNT NUMBER(10,2)` → `NUMBER(18,2)`

| Step | What happens | Tech |
|---|---|---|
| You run | `03_type_widening.sql` (enterprise orders of ₹15 crore no longer fit) | Snowflake |
| Diagnostician | Type lattice: precision grows and scale is kept → `lossless_widening` → **SEV3**, gate **HOLD** (the old contract cast would fail on the new values) | `sdis/sftypes.py` |
| Surgeon | Updates the contract type in the map → `cast(AMOUNT as number(18,2))`. **Auto-merges** on green CI (SEV3 doesn't need a human) | dbt contracts |
| Diplomat | FYI note only | — |
| Auditor | Releases the gate, rebuilds, resolves | dbt |

Snowflake can increase a NUMBER's precision in place but can't change its scale. SDIS treats any scale *decrease* as narrowing, which is breaking.

### 4.4 💥 Breaking — `STATUS` dropped, `IS_ACTIVE BOOLEAN` added

| Step | What happens | Tech |
|---|---|---|
| You run | `04_breaking.sql` | Snowflake |
| Diagnostician | `STATUS` gone. `IS_ACTIVE` rejected as a rename because the type family is VARCHAR vs BOOLEAN → `breaking/column_dropped` + `additive` → **SEV1**, gate **QUARANTINE** | Python rules |
| Evidence | **Time Travel clone** `DRIFT.ORDERS_PRE_INC_…` of RAW as it was before the change, so the lost STATUS history can be backfilled | `CREATE TABLE … CLONE … AT(TIMESTAMP => …)` |
| Surgeon | **Draft** PR that keeps the contract (`status` stays `'A'/'C'`) and *proposes* `case when not IS_ACTIVE then 'C' when IS_ACTIVE then 'A' end`. The mapping comes from value shares: baseline C=80%/A=20% matches new false=80%/true=20%. It is flagged for review | value-share matching |
| Diplomat | Jira SEV1: *"STATUS disappeared — confirm the replacement and whether history can be re-sent"* | Jira |
| Approval | You (and the Head of Data Reliability's review) decide whether the mapping is right | Paperclip |

### 4.5 🧠 Semantic — `AMOUNT` silently switches INR → USD (the silent killer)

| Step | What happens | Tech |
|---|---|---|
| You run | `05_semantic.sql` — same column name and type; values ÷ 84; 300 orders from yesterday re-sent | Snowflake |
| Why it's dangerous | Every dbt test still passes. Revenue dashboards would show a ~99% drop | — |
| Diagnostician | Median ₹2,400 → 28.57 (**×84.0**, robust z = 266). The factor matches the known hypothesis **INR→USD (≈84)**. The 300 restated orders show a constant old/new ratio of ≈84 with very low variation → `semantic/unit_change`, confidence 0.99 → **SEV1**, **QUARANTINE** | `MEDIAN`, robust z-score, restatement join |
| Diplomat | Jira + asks the producer to **confirm the unit**. They can answer in Paperclip, or by commenting `/sdis confirm` on the Jira ticket, which reaches the FastAPI webhook | Jira → FastAPI |
| Surgeon | Only after confirmation: `iff(_LOAD_ID >= 'L20261003', AMOUNT * {{ var('fx_inr_per_usd') }}, AMOUNT)`, so the contract stays INR | dbt var |
| If the producer says no | The load stays quarantined for manual triage. Nothing is guessed | — |

**Reset between takes:** re-run `02_seed_baseline.sql` (producer role), then `scenarios/99_reset.sql` (agent role), then `sdis bootstrap`.

---

## 5. Running it — from zero

### Step A · Offline (5 minutes, nothing to sign up for)

```bash
git clone https://github.com/karthikvalluri85/schema-drift-immune-system && cd schema-drift-immune-system
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api,dashboard,dev]"
sdis simulate all            # every scenario: decision, blast radius, the PR diff
pytest -q                    # 85 tests
```

### Step B · Snowflake (your trial account)

1. In a Snowsight worksheet run `snowflake/00_setup_account.sql` (as ACCOUNTADMIN). It creates the roles, the XS warehouse, the resource monitor, the database and schemas, and the service users.
2. Create the two key pairs and paste the public keys where the script says:
   ```bash
   mkdir -p keys && cd keys
   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out sdis_agent.p8 && openssl rsa -in sdis_agent.p8 -pubout -out sdis_agent.pub
   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out sdis_dbt.p8   && openssl rsa -in sdis_dbt.p8   -pubout -out sdis_dbt.pub
   ```
3. Run `01_control_plane.sql`, then `02_seed_baseline.sql`.
4. `cp .env.example .env`, then fill in `SNOWFLAKE_ACCOUNT` (for example `abc12345.ap-south-1`) and the key paths, and run `set -a; source .env; set +a`.
5. Run `cd dbt && dbt build --profiles-dir . && cd ..` to build the baseline marts.
6. Run `sdis bootstrap`. The Sentinel profiles the 7 clean loads and marks them PASSED.

### Step C · Paperclip on your laptop

```bash
npx paperclipai onboard --yes                    # http://localhost:3100 (needs Node 20+)
npx paperclipai company import ./company --target new --yes
python scripts/paperclip_setup.py                # process adapters, heartbeats, routine schedules, budget
```

Start Paperclip from the terminal where your venv and `.env` are loaded, so the agents can find `sdis` and the credentials. When you're ready for the agents to run on their own, use `python scripts/paperclip_setup.py --activate`.

### Step D · GitHub + Jira

- **GitHub:** create a fine-grained token with *Contents* and *Pull requests* read/write on this repo, and set it as `GITHUB_TOKEN`. In the repo, go to Settings → Secrets → Actions and add `SNOWFLAKE_ACCOUNT` and `SNOWFLAKE_DBT_PRIVATE_KEY` (the contents of `sdis_dbt.p8`) so Slim CI and deploy run. Turn on branch protection for `main` requiring the `ci` checks.
- **Jira:** create a project with key **SDIS**. Create an API token at id.atlassian.com → Security → API tokens, then set `JIRA_EMAIL` and `JIRA_API_TOKEN`.

### Step E · The demo with Paperclip (rename)

1. Run `snowflake/scenarios/02_rename_DEMO.sql` in a worksheet.
2. Wait for the Schema watch routine, or trigger it now: `curl -X POST localhost:8000/scan -H "Authorization: Bearer $SDIS_API_TOKEN"`.
3. Watch the Paperclip Issues page: Sentinel → Diagnostician → Surgeon + Diplomat.
4. Open the PR (one-line diff) and the Jira ticket (blast radius).
5. Approve in Paperclip → merge → the Auditor resolves.
6. Open the Streamlit dashboard: incident timeline, blast-radius graph, minutes to contain and resolve.

Without Paperclip, `sdis run --approve` does the same pass synchronously in your terminal. It's handy for a screen recording. To run the demo on the free GitHub Actions runtime instead, see section 7.

---

## 6. FastAPI from zero

**What it is.** FastAPI is a Python library for building web APIs. You write normal Python functions with type hints, and FastAPI turns them into HTTP endpoints. It validates inputs and generates interactive documentation at `/docs` automatically.

**Why it's in SDIS.** Paperclip runs the agents, but the outside world has to be able to *reach* the company when it runs on a server:

| Need | Without FastAPI | With FastAPI |
|---|---|---|
| React the moment a PR is merged | Auditor polls GitHub every few minutes | GitHub **webhook** → `/webhooks/github` → Auditor woken instantly |
| Producer answers in Jira, where they already work | They must log into Paperclip | Comment `/sdis confirm` → `/webhooks/jira` → Surgeon applies the fix |
| Other teams and tools need the incident data | Give them Snowflake credentials | `GET /incidents`, `GET /gate` behind one service |
| Trigger a scan on demand (demo button, chat-ops) | SSH in and run a command | `POST /scan` with a bearer token |

**The endpoints** (`sdis/api.py`):

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | — | Liveness + which integrations are configured (for health checks) |
| GET | `/scenarios`, `/scenarios/{name}` | — | Run the real engine on a built-in scenario (great for learning) |
| GET | `/incidents`, `/incidents/{id}`, `/gate` | — (put behind Caddy/VPN in prod) | Read incidents and the load gate from Snowflake |
| POST | `/scan` | `Bearer SDIS_API_TOKEN` | Ask the Sentinel to scan now |
| POST | `/webhooks/github` | HMAC signature (`GITHUB_WEBHOOK_SECRET`) | Merged `sdis/inc-…` PR → wake the Auditor |
| POST | `/webhooks/jira?secret=…` | shared secret (`JIRA_WEBHOOK_SECRET`) | `/sdis confirm` comment → Surgeon normalises |

**Try it in 3 commands:**

```bash
uvicorn sdis.api:app --reload --port 8000
open http://localhost:8000/docs           # click GET /scenarios/{name} → Try it out → "rename" → Execute
curl -s localhost:8000/scenarios/semantic | python -m json.tool | head -30
```

**How the webhook security works.** GitHub signs every delivery with your secret (HMAC-SHA256) and sends the signature in `X-Hub-Signature-256`. The API recomputes the signature and rejects anything that doesn't match, so nobody can fake "PR merged". `tests/test_api.py` shows this with a good and a bad signature.

**Do you need to host it for free deployment?** No. In the free setup (section 7) GitHub Actions receives these events natively: `pull_request: closed` replaces the GitHub webhook and `repository_dispatch` replaces the Jira webhook. FastAPI stays valuable for learning (`/docs`), for local Paperclip demos, and for the day you move to an always-on server.

**Testing webhooks from your laptop without a server.** Run `cloudflared tunnel --url http://localhost:8000` (free, no account needed). It prints an `https://….trycloudflare.com` URL. Paste `<url>/webhooks/github` into the GitHub repo under Settings → Webhooks → *Pull requests* events, with your secret.

---

## 7. Free deployment — no servers

The company runs on **GitHub Actions** (free on public repos). There is no server to pay for or patch: each workflow run starts, does its work against Snowflake, GitHub and Jira, and stops. All state lives in Snowflake (`DRIFT`).

### 7.1 Where each piece lives

| Piece | Free home | What triggers it | Limits to know |
|---|---|---|---|
| Sentinel → Diagnostician → Surgeon / Diplomat | `.github/workflows/sdis-company.yml` | Every 3 hours, or **Run workflow** | GitHub pauses schedules on public repos with no activity for 60 days; a commit re-enables them |
| Human approval (SEV1/SEV2) | **Merging the PR** (labelled `needs-human`) | You | The Surgeon never merges these itself |
| SEV3/SEV4 fixes | GitHub **auto-merge** | Required checks go green | Turn on "Allow auto-merge" in repo settings |
| Auditor | `.github/workflows/sdis-on-merge.yml` | An `sdis/inc-…` PR is merged | — |
| Producer answer (semantic) | `.github/workflows/sdis-producer.yml` | **Run workflow** (incident + confirm/reject), or Jira Automation | Jira Free includes 100 automation runs/month |
| Live dashboard | **Streamlit in Snowflake** | Open it in Snowsight | Uses warehouse credits while open |
| Public dashboard | **Streamlit Community Cloud** (demo mode) | A public URL | Sleeps after 12 h without visitors; a click wakes it |
| Org chart and agent work | **Paperclip on your laptop** | When you present | Set `SDIS_RUNTIME=paperclip` (below) so Actions doesn't double-run |
| FastAPI | Your laptop (`uvicorn`) | `/docs` | Not needed for the free runtime |
| Snowflake | Trial account | — | The trial is time-limited; the 10-credit monitor keeps spend capped |

**Why every 3 hours?** Each run wakes the XS warehouse for about a minute. Every 3 hours keeps the schedule well inside the 10-credit monitor. For demos, land a scenario and press **Run workflow**, so you never wait for the schedule.

### 7.2 One-time setup (about 20 minutes)

1. **Secrets.** Repo → Settings → Secrets and variables → Actions → *New repository secret*:

   | Secret | Value |
   |---|---|
   | `SNOWFLAKE_ACCOUNT` | e.g. `abc12345.ap-south-1` |
   | `SNOWFLAKE_AGENT_PRIVATE_KEY` | contents of `keys/sdis_agent.p8` |
   | `SNOWFLAKE_DBT_PRIVATE_KEY` | contents of `keys/sdis_dbt.p8` |
   | `SDIS_GITHUB_TOKEN` | fine-grained PAT on this repo: Contents + Pull requests read/write |
   | `JIRA_EMAIL`, `JIRA_API_TOKEN` | your Atlassian login + API token |

   Why a PAT and not the built-in token: PRs opened with the built-in `GITHUB_TOKEN` don't trigger other workflows, so CI and the Auditor would never run on the Surgeon's PRs.
2. **Merging rules.** Settings → General → tick **Allow auto-merge**. Settings → Branches → protect `main`: require a pull request and the status checks `test`, `slim-ci` and `scan`. Don't require an approving review: that would block SEV3/SEV4 auto-merge, and SEV1/SEV2 PRs are never merged by the bot anyway.
3. **Live dashboard: Streamlit in Snowflake.** Snowsight → Projects → Streamlit → **+ Streamlit App** → database `SDIS_DB`, schema `DRIFT`, warehouse `SDIS_WH` → paste `streamlit/streamlit_app.py` → Run. (The scripted version is `snowflake/03_streamlit_in_snowflake.sql`.)
4. **Public dashboard: Streamlit Community Cloud.** Sign in at share.streamlit.io with GitHub → **Create app** → repo `karthikvalluri85/schema-drift-immune-system`, branch `main`, file `streamlit/streamlit_app.py` → Deploy. With no Snowflake secrets it runs in demo mode on the real engine. That's the safe link to share on LinkedIn, because visitors never wake your warehouse.
5. **Optional: producer answers from Jira.** Jira → Project settings → Automation → *Create rule*:
   - Trigger: **Comment added**.
   - Condition: *Advanced compare*, `{{comment.body}}` contains `/sdis confirm`.
   - Action: **Send web request**, POST `https://api.github.com/repos/karthikvalluri85/schema-drift-immune-system/dispatches`, headers `Authorization: Bearer <PAT>` and `Accept: application/vnd.github+json`, custom body:
     `{"event_type":"sdis-producer","client_payload":{"incident":"{{issue.summary.match("\[(INC-[0-9A-F]{8})\]")}}","answer":"confirm"}}`

   Ticket summaries start with `[INC-…]` so the rule can read the incident id. If your plan doesn't offer *Send web request*, use **Run workflow** on `sdis-producer`.

### 7.3 The demo on the free runtime (rename)

1. Run `snowflake/scenarios/02_rename_DEMO.sql` in Snowsight.
2. GitHub → Actions → **sdis-company** → *Run workflow*. In about 2 minutes the job summary shows Sentinel → Diagnostician → Surgeon → Diplomat.
3. A PR appears: `[SEV1] Rename on ORDERS: CUST_ID → CUSTOMER_ID`, a one-line diff labelled `needs-human`, with Slim CI building exactly the 5 marts. The Jira ticket `[INC-…]` has the blast radius.
4. **Merge the PR. That is your approval.** **sdis-on-merge** runs the Auditor: gate → PASSED → `dbt build stg_orders+` → RESOLVED.
5. Open Streamlit in Snowflake: the incident with minutes to contain and resolve.

For the semantic scenario, step 4 comes after the producer answers: Actions → **sdis-producer** → incident `INC-…`, answer `confirm`. The Surgeon then opens the normalisation PR, and you merge it.

### 7.4 Presenting with Paperclip on your laptop

Paperclip is how you *show* the company: the org chart, issues filling in, approvals and costs. Before you start it, set repo variable **`SDIS_RUNTIME=paperclip`** (Settings → Secrets and variables → Actions → Variables). The scheduled Actions run then stands down, so the two runtimes never process the same load. Delete the variable afterwards to hand the company back to Actions. The steps are in section 5, Step C.

### 7.5 Later: an always-on server (optional, paid)

If you ever want Paperclip itself running 24×7 with a public URL, `deploy/digitalocean/` has a ready Droplet setup: cloud-init, Caddy HTTPS via sslip.io, and systemd units. It costs about $12–24/month, billed hourly. Nothing in the free setup depends on it.

---

## 8. The final tour

### 8.1 Paperclip: visualize your org and everything that happens

This was verified against a live Paperclip (v2026.824) during the build: the package imports cleanly, and a real Diagnostician heartbeat checked out an issue and posted its result.

- **Org:** Head of Data Reliability at the top (Claude Code, $20/month), with five reports: Sentinel, Diagnostician, Surgeon, Diplomat and Auditor (process adapter, $1/month each). Company budget: $25/month.
- **Routines:** *Schema watch* every 15 minutes, *Weekly cost ledger* Monday 09:00 IST, *Weekly reliability review* Monday 09:30 IST.
- **Issues:** each incident is a thread. The parent is "Diagnose drift…", with children for Remediate, Notify and Verify & close. Each agent comment lists its **decisions and the alternatives it rejected**.
- **Approvals:** SEV1/SEV2 show an **Approve / Request changes** card on the Surgeon's issue. That card is your human-in-the-loop.
- **Costs:** spend per agent against budget. Five agents stay at $0; only the manager uses tokens. Agents auto-pause at 100%.
- **Activity:** the full audit log of who did what and when.

### 8.2 The Streamlit command center (review checklist)

| Tab | What to look for |
|---|---|
| 📈 Overview | Incidents, open, loads quarantined, **average time to contain** (drift → gated) and **time to resolve** |
| 🚨 Incidents | Pick an incident: gate, route, blast tier, approval, Cortex summary, links to Jira and the PR, events table, **blast-radius graph** (column → staging → marts → dashboards), and the deterministic decision trail |
| 🚦 Load gate | Every batch with PASSED / PENDING / QUARANTINED. Proof that quarantine hides data instead of deleting it |
| 🧪 Scenario lab | Run any of the five scenarios through the real engine and see the story, graph and PR diff |
| 💰 Cost & MTTR | Credits per incident and agent (from `QUERY_TAG`), and minutes to contain / resolve by class |

Run it with `streamlit run streamlit/streamlit_app.py`. Without Snowflake env vars it opens in **demo mode** with the five simulated incidents.

### 8.3 What FastAPI adds (when you run a server)

- **Event-driven instead of polling:** merges and producer replies act in seconds.
- **Meets people where they work:** producers answer in Jira; nobody needs a Paperclip login.
- **One safe front door:** read APIs and scan triggers without handing out Snowflake credentials. Webhooks are signature-verified.
- **Self-documenting:** `/docs` is a live, clickable contract for other teams.

### 8.4 What the free stack adds

- **Always on, at $0:** GitHub Actions wakes the company on a schedule and on events. No server to pay for, patch or keep awake.
- **Event-driven:** a merge or a producer answer starts its own workflow within seconds. Nobody polls.
- **Approvals where engineers already are:** a SEV1 approval is a PR merge, protected by required checks.
- **Shareable for free:** the Streamlit Community Cloud link shows the real engine to anyone, without touching your warehouse. Streamlit in Snowflake shows live incidents to you.
- **Auditable:** every run's summary and trace are kept on the Actions tab; decisions live in Snowflake.
- **Same code, bigger runtime later:** the agents are identical under Paperclip, Actions, or a server.

---

### 8.5 The 17-second demo GIF (and demo mode)

`docs/assets/sdis-demo.gif` (top of the README) and `docs/assets/sdis-demo.mp4` were recorded from a real Paperclip
run. The six scenes are: org chart → task tree → the Diagnostician's decisions → the approval card → live heartbeats → resolved + budget.
They come from **demo mode** (`SDIS_DEMO=rename`): the real agents, playbooks and heartbeats, with Snowflake, GitHub
and Jira swapped for recorded data, so anyone can watch the company work without accounts.

- **Try it:** see "Watch the company work in Paperclip" in the README.
- **Re-record:** `python scripts/record_demo.py` (needs a throwaway Paperclip, Playwright Chromium and ffmpeg). It
  re-imports the company, lands the rename scenario, clicks Approve like a human would, and cuts the take to exactly
  17 s. Captions are overlaid in the page; scene boundaries are found from colour markers in the video itself.
- **LinkedIn:** the GIF is under 5 MB. Upload it with the **photo** button so it stays animated, or post the MP4 as a
  video. In a blog or GitHub README, the GIF autoplays.

## 9. Taking it to a real production platform

Adopt it in three steps of increasing trust (`SDIS_MODE`):

1. **observe:** read-only on your RAW tables. Detect, classify, ticket, ledger. No dbt changes, no gate.
2. **advise:** add Surgeon PRs against your dbt repo. Humans merge.
3. **protect:** add `where {{ sdis_load_gate('<table>') }}` to staging models. Bad batches are now held automatically.

What you change for your company:
- Tag tables with `sdis_watch: true` and `sdis_key` in `sources.yml`.
- Write a column map per staging model (`dbt/sdis_maps/`).
- Declare dashboards as dbt **exposures** with a `tier`, because that's what drives severity.
- Tune `policies/playbooks.yaml` thresholds.

**Known limits (say them before someone else does):**
- Blast radius is model-level, not column-level.
- Low-cardinality renames go to a human by design.
- `ACCOUNT_USAGE` cost data lags by hours.
- Paperclip's import CLI needs `scripts/paperclip_setup.py` afterwards.
- Profiling cost grows with columns × loads. It's one aggregate query per load, but measure it on wide tables.

---

## 10. A 60-second LinkedIn demo script

1. **(0–10 s)** Show the Paperclip org chart: "A data-reliability company with 6 employees. Five of them cost $0."
2. **(10–20 s)** In Snowflake, run the rename: "At 2 a.m. someone renames a join key."
3. **(20–35 s)** Paperclip issues fill in. Show the Diagnostician comment: rename proven by values (0.998), 5 marts and 4 dashboards at risk, SEV1, load held.
4. **(35–45 s)** Show the PR, a one-line diff that Slim CI built exactly, and the Jira ticket.
5. **(45–55 s)** Click Approve. The Auditor resolves. The Streamlit dashboard shows *contained in < 1 min*.
6. **(55–60 s)** "Deterministic routing, LLMs only for words, humans for judgment. Repo in comments."
