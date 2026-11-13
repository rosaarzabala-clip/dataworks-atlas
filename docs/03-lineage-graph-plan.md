# dataworks-atlas: Plan

> **Status:** draft for review. Nothing in this plan is built yet except the current `ingest_dataworks.py`.
> **Goal:** a graph that stays in sync with DataWorks, groups every pipeline into a business domain, and lets us find duplicated logic, starting with one domain.

**Contents**

1. [Where we are](#1-where-we-are)
2. [Target architecture](#2-target-architecture)
3. [Phases](#3-phases)
4. [Domain classification design](#4-domain-classification-design)
5. [Handling new nodes](#5-handling-new-nodes)
6. [Duplication analysis](#6-duplication-analysis)
7. [Repository layout](#7-repository-layout)
8. [Open decisions](#8-open-decisions)
9. [Risks](#9-risks)

---

## 1. Where we are

| Aspect | Today | Problem |
|---|---|---|
| Data source | `ingest_dataworks.py` imports `data_mcp_server` from the `data-knowledge-base` repo | Breaks if that repo moves or changes. Reads its `.env` |
| What is stored | 1,589 scheduler nodes + `FEEDS` edges | No tables, no SQL, no domain. Cannot detect duplicated logic |
| Update mode | Delete everything, reload | Any manual annotation (domain, notes) is lost on every run |
| Trigger | Manual, on VPN | No record of what changed between runs |

## 2. Target architecture

Three independent steps, each a script, connected by files and the graph:

```
 DataWorks API ──► [1 extract] ──► data/snapshots/<date>/*.json
                                          │
                                          ▼
                                    [2 load] ──► Neo4j  ◄── [3 classify] ◄── domains.yaml
                                                   │
                                                   ▼
                                           [4 analyze] ──► reports/<domain>.md
```

Design rules:

- **Extract is separate from load.** Only extract needs the VPN. Snapshots are plain files, so we can re-load, diff two dates, or debug offline.
- **Load is incremental.** It never deletes our own properties (domain, notes).
- **Classification is rule-based and versioned.** Rules live in `domains.yaml` in the repo, not in the database. The graph can be rebuilt from snapshots plus rules at any time.
- **Read-only against DataWorks.** The client only allows `List*`, `Get*`, `Describe*`, `Query*`, `Check*`, `Search*` actions, as the MCP does.

## 3. Phases

### Phase 0. Foundations

- Own `.env` in this repo with `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`, `DATAWORKS_ENDPOINT`, `DATAWORKS_REGION`, `DATAWORKS_PROJECT_ID`, `DATAWORKS_VERSION`, `NEO4J_PASSWORD`. Add `.env.example` with no values.
- Python package `dwgraph/` with its own `pyproject.toml` and virtualenv (stdlib client, so few dependencies: `pyyaml`, and optionally `sqlglot` in phase 4).
- `git init` this folder. The README says it is part of the `data` repo, but this directory is not a git repository today.
- `.gitignore`: `.env`, `neo4j/`, `data/snapshots/`.

**Done when:** `python -m dwgraph --help` runs and no import touches `data-knowledge-base`.

### Phase 1. Extract (own client)

`dwgraph/client.py`: signed RPC v1 request (HMAC-SHA1), read-only whitelist, retry with backoff, GET/POST fallback (some actions only accept POST). Based on the logic already proven in the MCP, but written and owned here.

`dwgraph/extract.py` writes one snapshot folder per run:

| File | Content | API (to confirm against the MCP code) |
|---|---|---|
| `nodes.json` | All PROD nodes with their attributes | `ListNodes` |
| `edges.json` | Parent-to-child scheduling dependencies | `GetNodeParents` per node |
| `code/<node_id>.sql` | Source code of each node | `ListFiles` + file content, as `get_node_code` does |
| `table_io.json` | Tables each node reads and writes | Node input/output and table lineage APIs (to verify which exist) |
| `datasources.json` | Datasource name, engine, host (**no credentials**) | `ListDataSources`, with an allowlist of fields |
| `meta.json` | Run time, counts, failed calls | n/a |

Rules: fetch in parallel (8 workers like today), record failures instead of silently dropping nodes, and never persist datasource `username`, `password`, `accessKey` or Kafka SASL values (see inconsistency #19 in doc 02).

**Done when:** one command produces a complete snapshot and `meta.json` shows zero unexplained failures. Full run time is measured and documented.

### Phase 2. Load (incremental sync)

`dwgraph/load.py` reads a snapshot and updates Neo4j:

- `MERGE` on `Node.id` and `SET` only the DataWorks-owned properties. Curated properties (`domain`, `domain_source`, `notes`) are never touched.
- Every node gets `first_seen`, `last_seen`. Nodes missing from the snapshot get `deleted_at` instead of being removed.
- Edges are synchronized the same way (add new, mark missing).
- New model: `(:Table)`, `(:Node)-[:READS]->(:Table)`, `(:Node)-[:WRITES]->(:Table)`, `(:Datasource)`.
- Each run writes a `(:SyncRun {at, added, removed, changed})` node and a `reports/sync-<date>.md` summary: new, removed, and modified nodes.

**Done when:** running load twice in a row reports zero changes, and a manual `domain` value survives a reload.

### Phase 3. Classify into domains

See [section 4](#4-domain-classification-design) and [section 5](#5-handling-new-nodes). Output: every active node has a domain, a confidence and the rule that decided it.

**Done when:** the first domain is classified with an agreed accuracy (spot check of a sample reviewed by you) and the unclassified count is reported for the rest.

### Phase 4. Analyze one domain for duplication

See [section 6](#6-duplication-analysis). Output: `reports/<domain>-duplicates.md`.

### Phase 5. Documentation and operations

- Update `README.md` (new run commands, no MCP dependency).
- `docs/04-domains.md`: one section per domain (scope, source systems, tables per layer, owners, known issues). Generated in part from the graph, reviewed by hand.
- Schedule the sync (cron or launchd) with a note that it needs the VPN, and a warning when it cannot connect.

## 4. Domain classification design

**Domain list.** The starting taxonomy is the table in doc 02, section 3.2 (Member, Acquiring, Settlement, Fund, Promotion, Payment, Accounting, Financial, Boss, Real Time, Mobile, Merchant). It was written from the ODS side, so it must be checked against the full node list: names like `expo*`, `risk*`, `dash*`, `rule*`, `cred*`, `file*` and `app_*` do not obviously belong to any of them. See open decision 1.

**Rules, applied in this order (first match wins), all in `domains.yaml`:**

1. **Manual override.** Explicit `node name or table → domain` entries. Always wins.
2. **Datasource / source schema.** An ODS sync node inherits the domain of its source schema (e.g. `aqc00~09` → Acquiring).
3. **Table-name prefix.** Prefix map from doc 02 (`aqc_` → Acquiring, `smb_`/`smt_` → Settlement, `ap_fund_` → Fund, ...). Also covers DWD/DWS/ADM entity words (`acquiring`, `settle`, `fnd`, `fee`, `mbr`).
4. **Lineage propagation.** A node without a direct match takes the domain of its upstream tables if they agree (majority with a threshold). If upstream spans several domains, it is flagged as **cross-domain**, not forced into one.
5. **Baseline.** Only as a weak hint: the biggest baseline holds 711 nodes, so it is too coarse to decide a domain.
6. Otherwise: `unclassified`.

Each node stores `domain`, `domain_source` (which rule) and `domain_confidence` (`high` for override/datasource/prefix, `medium` for propagation, `low` for baseline hint). A rule change followed by `classify` recomputes everything except manual overrides.

**Why not do this once by hand:** with about 1,600 nodes and new ones every week, the rules must be re-runnable, and the same logic must tell us what to do with a node that appears tomorrow.

## 5. Handling new nodes

After every sync, `classify` produces a **new-nodes report**:

| Case | What happens | What you do |
|---|---|---|
| New node, rule matched with high confidence | Domain assigned automatically, listed in the report | Skim the list |
| New node, medium or low confidence | Domain proposed, status `needs_review` | Confirm or add an override |
| New node, no rule matched | Status `unclassified`, listed with its upstream tables and name | Add a rule (preferred if it is a pattern) or an override |
| Node name breaks the naming standard (doc 02, section 4) | Flagged in the same report | Tell the owner, or document the exception |
| Node whose upstream spans several domains | Flagged `cross_domain` | Decide the owning domain |

The goal is that a rule added for one new node also covers the next ones, so the review list gets shorter over time. The `unclassified` count is the health metric to track.

## 6. Duplication analysis

Run per domain, from cheapest and most certain to most speculative:

| Level | Method | Confidence |
|---|---|---|
| Same output | Two nodes write the same table | Certain (usually a bug or a rename in progress) |
| Same code | Normalized SQL hash is identical (strip comments, whitespace, case, `${bizdate}`-style parameters) | High |
| Same sources + same logic shape | Same set of source tables and similar columns, SQL structure compared via `sqlglot` | Medium. Candidate list for human review |
| Parallel naming | Same entity in `adm_` vs `ads_`, `_di` vs `_dd` versions of the same table, `ods_x` and `ods_x_delta` both consumed downstream | Pattern, reported as a list |
| Unused outputs | Tables written but with no downstream reader | Related finding, useful for cleanup |

Output per domain: a ranked list with the nodes, tables, evidence, and a suggested action (merge, retire, document as intentional).

## 7. Repository layout

```
dataworks-atlas/
├── README.md                 # how to run (update in phase 5)
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── domains.yaml              # taxonomy + rules + overrides (versioned)
├── dwgraph/
│   ├── client.py             # signed read-only DataWorks client
│   ├── extract.py            # phase 1
│   ├── load.py               # phase 2
│   ├── classify.py           # phase 3
│   ├── analyze.py            # phase 4
│   └── __main__.py           # python -m dwgraph extract|load|classify|analyze
├── data/snapshots/           # git-ignored
├── reports/                  # sync, new-nodes and duplication reports
└── docs/
    ├── 01-...  02-...        # existing
    ├── 03-lineage-graph-plan.md
    └── 04-domains.md         # phase 5
```

The existing `ingest_dataworks.py` stays until phase 2 reaches parity, then is removed.

## 8. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Domain taxonomy: use the 12 domains from doc 02 as is, or revise first | Start with those, add domains for what does not fit (risk, credit, exports, dashboards?) after seeing the unclassified list |
| 2 | First domain to analyze | Acquiring (`aqc_`): clearest prefix and several documented DWD/ADM tables |
| 3 | Where `domains.yaml` lives | This repo, committed. Not the shared catalog, since it is operational config |
| 4 | Can a node belong to two domains | One primary domain per node plus a `cross_domain` flag, to keep reports unambiguous |
| 5 | Who reviews the new-nodes report and how often | Weekly, by the data lead |
| 6 | Whether to store SQL code in the graph or only in files | Files in the snapshot, hash and table I/O in the graph |
| 7 | Credentials: personal RAM key in `.env` or a shared read-only service key | A read-only key, never committed |

## 9. Risks

- **API coverage.** Table-level read/write info may not be available for every node type (shell, controller, virtual nodes). Mitigation: fall back to parsing the SQL, and report the coverage percentage.
- **Apsara Stack quirks.** The API is private and partly undocumented (method per action, errors that look like permission problems). Mitigation: keep the retry and method-fallback behavior from the MCP client.
- **Runtime.** Fetching code for about 1,600 nodes is slower than the current 30-second run. Mitigation: fetch code only for nodes whose version or modified time changed since the last snapshot.
- **Sensitive data.** Datasource configuration and code can contain secrets. Mitigation: field allowlist at extract time, and scan snapshots for credential patterns before committing anything.
- **Graph without owners.** Without a reviewer, the unclassified list grows. See decision 5.
