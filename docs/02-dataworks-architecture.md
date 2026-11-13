# DataWorks Data Warehouse Architecture: Current State and Planned Corrections

> **Status:** working draft.
> **Sources:** *Data Assets* (architecture, naming, table inventory), *Data Warehouse Development SOP* (development process, governance, security) and *Data Warehouse Table Lifecycle Standard* (naming and lifecycle).
> All three are DingTalk documents. The full ODS and DWD table inventory lives in *Data Assets*. This document only summarizes it.

**Contents**

1. [Overview](#1-overview)
2. [Layered architecture](#2-layered-architecture)
3. [Data sources and ingestion](#3-data-sources-and-ingestion)
4. [Naming conventions](#4-naming-conventions)
5. [Lifecycle and retention](#5-lifecycle-and-retention)
6. [Development standards](#6-development-standards)
7. [Development and deployment process](#7-development-and-deployment-process)
8. [Data governance](#8-data-governance)
9. [Security and permissions](#9-security-and-permissions)
10. [Current table inventory](#10-current-table-inventory)
11. [Detected inconsistencies and pending corrections](#11-detected-inconsistencies-and-pending-corrections)

---

## 1. Overview

| Component | Technology |
|---|---|
| Orchestration and development | DataWorks (Data Studio, Operation Center, Deployment Center, DataMap) |
| Data warehouse | MaxCompute (ODPS), columnar storage |
| Source systems | Relational databases (registered in DataWorks with engine type `mysql`, documented as OceanBase), Kafka, SLS log projects (`loghub`) and OSS buckets. See section 3.1 |
| BI consumption | Quick BI. Hologres or AnalyticDB are considered for high-frequency data |
| Cold archive | OSS (proposed, not yet implemented) |
| Environments | DEV and PROD, with code approval between them |

General flow:

```
OceanBase (sources) --Offline Sync--> ODS --> DWD --> DWS --> ADM --> BI / APIs
                                               ^
                                              DIM (shared dimensions for all layers)
```

## 2. Layered architecture

| Layer | Principle | What it does | What it does NOT do |
|---|---|---|---|
| **ODS** | *Mirror the source* | Replicates the source schema. Converts types to MaxCompute standards (`DATETIME`→`TIMESTAMP`, `VARCHAR`→`STRING`) | Business logic, aggregation, filtering (except obvious garbage data) |
| **DWD** | *One fact, one record* | Cleaning, standardization, PII masking (e.g. `MD5(phone)`), wide tables joined with DIM | Aggregations |
| **DWS** | *Reuse & summarize* | Pre-calculated common metrics by subject (user, merchant, product, region) and time grain (`1d`, `7d`, `1m`). E.g. DAU, AAU, order count | Report-specific structure |
| **ADM** | *Business ready* | One table per report, widget or data product. Small and fast for BI | Store long history |
| **DIM** | *Consistency & stability* | Unified reference data (e.g. country, calendar), usually a full daily snapshot | — |

Metrics must be registered in the *Business Glossary* with their formula (atomic and derived) and must state exclusions explicitly (test accounts, refunded orders). The definition is stored as metadata linked to the code.

## 3. Data sources and ingestion

### 3.1 Source systems registered in DataWorks (verified in PROD)

Verified with `list_dataworks_datasources`: 269 datasources registered. **Partial view:** 200 were listed and the last 69 (alphabetically after `ifcgotone06`) are still to be reviewed.

| Type | What it is | Examples |
|---|---|---|
| `mysql` | Relational databases, sharded `00~09` (and some `00~99`). Most connect to host `172.20.173.115` (ports `13306`, `3304`-`3309`). A few use managed RDS hosts (`*.mysql.minirds.ops.apsara.clipcash.mx`) | `apbiz`, `aqc`, `settlement`, `fee`, `idt`, `bizcenter`, `collection`, `dwdqc`, `dwide`, `dwphoenix` |
| `loghub` (SLS) | Backend business logs, one SLS project per service | `backend_acquiring`, `backend_customer`, `backend_fund`, `backend_risk`, `backend_wireless` |
| `oss` | Buckets for file exchange and exports | `daas_export_mexprod`, `daas_export_mexprod_csb`, `flux_inst_file_core_1_mexprod`, `aml_bucket_mexprod`, `credit_sync_oss`, `public_cloud_oss` |
| `kafka` | AWS MSK cluster (us-west-2) | `aws_kafka_b1` |

Observations to validate:

- The engine type is `mysql`, but the ODS inventory documents the sources as OceanBase (see 3.2). OceanBase in MySQL mode connects this way, so both statements can be true.
- Some datasources point to another schema than their name suggests (e.g. `ar03`-`ar09` point to `aqc01`). Possible misconfiguration.
- Some shard groups use different ports (`bp_common_biz_order_*`, `collection*`).
- There are `_writer` variants (e.g. `settlebill_writer`).

### 3.2 Domains and documented source databases (from *Data Assets*)

The inventory in *Data Assets* documents these as OceanBase with JDBC host `ob.mtndev.com:2883`, which differs from the hosts registered in DataWorks.

| Domain | Source schemas | ODS prefixes |
|---|---|---|
| Member | `apbiz00~09`, `apcif`, `accenter00~09`, `limitcenter`, `cumulate_biz_00~09` | `bp_`, `cu_`, `acc_`, `atc_`, `fc_`, `lc_` |
| Acquiring | `aqc00~09`, `aqc_pre00~09` | `aqc_` |
| Settlement | `settlement00~09`, `settlebill` | `smb_`, `smt_` |
| Fund | `apfund00~09` | `ap_fund_` |
| Promotion | `appromo00~09`, `appromocfg`, `igrowth` | `sd_`, `bcvoucher_`, `marketing_`, `rule_` |
| Payment | `assetflux00~09` | `af_` |
| Accounting | `debittrans00~09`, `innertrans`, `accshare`, `recon`, `apreserve` | `det_`, `int_`, `ren_`, `rc_`, `atc_` |
| Financial | `fluxnet00~09`, `fluxbatch00~09`, `supergw00~09`, `fluxshare` | `flux_`, `gw_` |
| Boss (billing and contracts) | `pf`, `ar00-99`, `fee00~09` | `ar_`, `pf_`, `fee_` |
| Real Time | `idt00~09`, `ifcgotone00~09` | `idt_`, `gt_` |
| Mobile | `apmobileprod` | `mcore_` |
| Merchant | (undocumented) | — |

### 3.3 Synchronization strategies

- **Daily full snapshot:** table `ods_<table>`. Overwritten every day (T+1).
- **Daily incremental:** table `ods_<table>_delta`. Initial full load with no `WHERE`, then daily incremental (T+1).
- **Hourly incremental:** case `ods_ap_fund_order_delta_hh` (Fund), refresh `HH`.
- Some tables live in a single OceanBase table (no sharding) and are annotated as such in the inventory.
- Each table documents its primary key (usually includes `tnt_inst_id`).

### 3.4 Steps to onboard a source

1. **Create the datasource** in DataWorks: JDBC URL, database name, username, password and a connectivity test.
2. **Create the ODS table** (by SQL or visual tool) with `PARTITIONED BY (dt STRING)` and a `LIFECYCLE`.
3. **Create the Offline Sync node** (source OceanBase, destination ODPS). The node name matches the table name, e.g. `ods_aqc_base_delta`.
4. **Map fields** so the ODPS schema matches the source schema.
5. **Configure scheduling and dependencies**, and assign the Data Integration *Resource Group*.
6. **Save, commit, run manually and verify** that the ODS table is populated.

## 4. Naming conventions

General rules: lowercase `snake_case`, `_` separator, names under 30 characters.

| Layer | Pattern | Example |
|---|---|---|
| ODS | `ods_{source_table}[_delta]` | `ods_acc_entry`, `ods_acc_entry_delta` |
| DWD | `dwd_{entity}_{attribute}_{cycle}` | `dwd_mbr_role_user_dd`, `dwd_acquiring_information_di` |
| DWS | `dws_{entity}_{attribute}_{cycle}` | `dws_mbr_user_active_dd` |
| ADM | `adm_{entity}_{attribute}_{cycle}` | `adm_merchant_growth_report` |
| DIM | `dim_{name}` | `dim_calendar` |
| Temporary | `tmp_*` | Only inside a single node |
| Cold archive | `history_{original_table}_{archive_date}` | — |

### Cycle suffix (2 characters: frequency + type)

| Frequency | | Type | |
|---|---|---|---|
| `d` daily | `h` hourly | `i` incremental | `d` detail / full snapshot |
| `w` weekly | `q` quarterly | `s` summary / aggregate | |
| `y` yearly | `m` monthly | | |

Common combinations: `di` (daily incremental), `dd` (daily full snapshot), `ds` (daily summary), `hs` (hourly summary), `ms` (monthly summary), `wd` (weekly snapshot). The lifecycle standard also uses `hi`, `thi` (accumulated from 00:00 to the current hour), `1d/nd/td`, `qs`, `ys`.

### Business entity abbreviations

| Entity | Abbrev. | Entity | Abbrev. |
|---|---|---|---|
| Member | `mbr` | Accounting | `acct` |
| Acquiring | `acq` | Financial | `flux` |
| Settlement | `stlmt` | Boss | `boss` |
| Fund | `fund` | Real Time | `idt` |
| Promotion | `mkt` | Customer | `crm` |
| Payment | `pay` | | |

## 5. Lifecycle and retention

Current standard (*Table Lifecycle Standard*):

| Table | Content | Lifecycle | Notes |
|---|---|---|---|
| `ods_*_delta` | Daily incremental | Permanent | May be shortened if business and regulation allow |
| `ods_*_hi` | Hourly incremental | Permanent | |
| `ods_*_thi` | Accumulated since 00:00 | 1 year | Rebuildable from `_hi` |
| `ods_*` | Full snapshot | 60 days | If slices are required, keep one day per month. Type not recommended |
| `tmp_*` | Temporary | 7 days (1 recommended) | No dependencies outside the node. Prefer script mode |
| `dwd_*_di` | Incremental detail | 10 years | |
| `dwd_*_hi`, `dwd_*_thi` | Hourly detail | 1 year | |
| `dwd_*_dd` | Full detail | 3 years | |
| `dws_*_1d/nd/td`, `dws_*_ms` | Summaries | 3 years | `hs`, `qs`, `ys`, `dd` have no value defined |
| `adm_*_di` | Incremental detail | 32 days by default | More than 32 days requires approval |
| `adm_*_dd` | Full snapshot | Undefined | Architecture approval. One day per month if slices are needed |
| `adm_*_1d/nd/td` | Summaries | Undefined | More than 180 days requires approval |
| `dim_*` | Full dimension | Permanent | |
| `history_*` | Cold archive | Cold storage | Retrieval takes about 2 to 3 hours |

## 6. Development standards

**SQL**

- `SELECT *` is prohibited. List columns explicitly (columnar storage: less I/O, lower cost, immune to schema changes).
- In every `JOIN`, qualify columns with each table's alias.
- Complex logic must use **CTEs** (`WITH`) instead of nested subqueries.

**Performance**

- Partition filter is mandatory (`dt = '${bizdate}'` or a range) to enable partition pruning.
- Avoid Cartesian products between large tables without filters.

**Prior analysis (dashboards)**

Before writing SQL, build a mapping table: `Field | Source system | Source table | Source column | Logic`.

## 7. Development and deployment process

1. **Account:** request an Alibaba Cloud account (SOP.6 / *Mex Prod Env White Paper*).
2. **Source → ODS** (section 3.4).
3. **DWD:** ODPS SQL node with `INSERT OVERWRITE`, main ODS table joined with related ones. Depends on the ODS node.
4. **DWS:** aggregates from DWD. Depends on the DWD node.
5. **ADM:** dashboard fields. Depends on the DWS node.
6. **Submit to DEV:** specify the code approver (optionally a smoke test date and a change description). Backfills can be run in Operation Center.
7. **Deploy to PROD:** from Deployment Center. If the status is *Pending Approval*, copy the detail link to the approver. After approval, select **Deploy Task**.

Dependencies between layers: `ODS sync → DWD → DWS → ADM`.

## 8. Data governance

- **Historical retention (SOP):** see the inconsistencies in section 11, since it differs from the lifecycle standard.
- **Hot / cold storage:** proposal to export old partitions from MaxCompute to OSS (offline export node, delete the original partitions, schedule periodically, e.g. monthly). Not in the scope of MTN MVP 0 and 1. The policy is defined according to data volume.
- **Asset decommissioning:** assets not accessed for 90 days enter an evaluation process. The *Data Asset Governance Center* generates a report of tables with no access in 90 days, no lineage dependencies and high storage consumption. It supports notifying owners or decommissioning in bulk.
- **Table and metric search:** DataMap module.

## 9. Security and permissions

**Roles**

| Role | Access |
|---|---|
| Developer | Read and write on all layers |
| Analyst | Read only on DWS and ADM |
| Visitor | Metadata only, cannot query data |

**ODS table access**

- By default, business users have no access to ODS.
- A permission request is required, approved by the data owner or an administrator.
- Fine-grained permissions can be set on approval: read-only, validity period and field-level masking.
- Request flow: workspace → table, fields and permissions → account and duration → reason and business context.

## 10. Current table inventory

Per-table detail (primary key, strategy, frequency) is in *Data Assets*, section 4.

| Layer | Documented tables |
|---|---|
| ODS | About 190 inventory rows across 11 domains. Almost all T+1, and several have a snapshot and `_delta` pair |
| DWD | `dwd_mbr_role_user_dd`, `dwd_mbr_role_merchant_dd`, `dwd_acquiring_information_di`, `dwd_acq_refund_detail_di`, `dwd_settle_real_time_order_detail_di`, `dwd_fnd_order_detail_hh`, `dwd_trans_detail_di`, `dwd_fee_bill_info_dd` |
| DWS | `dws_mbr_user_active_dd`, `dws_mbr_merchant_active_dd` (plus `dws_acquiring_transaction_summary_di` in the SOP) |
| ADM | `adm_user_growth_report`, `adm_user_growth_active_report`, `adm_merchant_growth_dist_report`, `adm_merchant_growth_report`, `adm_settle_real_time_settlement_di`, `adm_fnd_order_detail_hh`, `adm_user_trans_sum_report`, `adm_fee_analysis_report`, `adm_account_analysis_report` |
| DIM | `dim_calendar`, `dim_switch_code` |

## 11. Detected inconsistencies and pending corrections

> Proposals to validate. Each item needs a decision before becoming a rule.

| # | Topic | Today (documents) | Proposed correction |
|---|---|---|---|
| 1 | **ODS delta retention** | SOP: 10 years. Lifecycle Standard: permanent. Example DDL: `LIFECYCLE 1096` (3 years) | A single policy, and align the DDLs |
| 2 | **ODS snapshot retention** | SOP: 1 year. Standard: 60 days | Define (the Standard reflects the more recent cost intent) |
| 3 | **DWD retention** | SOP: permanent or 3-5+ years. Standard: 10 years (`di`), 3 years (`dd`). Example: 365 days | Adopt the Standard and review the example |
| 4 | **ADM retention** | SOP: 1-3 years. Example: 730 days. Standard: 32 days by default | Define; the Standard requires approval above 32 days |
| 5 | **DWS/ADM retention with no value** | Several suffixes (`hs`, `qs`, `ys`, `dd`, ADM `1d/nd/td`) are empty | Complete the lifecycle table |
| 6 | **ADM layer name** | `ADM`, `ADS` and `ads_` (e.g. `ads_traffic_hs`) are used interchangeably | Fix on `adm` and correct examples and roles |
| 7 | **DIM pattern** | Appears as `adm_{...}_dd` in the naming table, but the example is `dim_calendar` | `dim_{name}` |
| 8 | **Suffixes** | `ds` is used in the guide and `1d/nd/td` in the Standard. `m` is not in the base list but `ms` is | Unify the suffix catalog |
| 9 | **Entity abbreviations** | The guide says `acq`, `stlmt`, `mkt`, `fund`, but real tables use `acquiring`, `settle`, `fnd`, `marketing`, `fee` | Decide whether to rename tables or update the guide |
| 10 | **Tables outside the pattern** | `adm_merchant_growth_report` and `adm_user_growth_report` have no cycle suffix | Add a suffix or document the exception |
| 11 | **Merchant domain** | ODS section is empty | Document or remove |
| 12 | **Metrics catalog** | Metrics table is almost empty | Populate it or point to the Business Glossary |
| 13 | **Hot / cold storage** | Proposal only, outside MTN MVP 0 and 1 scope | Decide scope and date |
| 14 | **Cross-references** | The SOP refers to a "Data Synchronization Access SOP" and to external documents (Yuque, White Paper) | Consolidate and verify links |
| 15 | **Source host mismatch** | *Data Assets* documents `ob.mtndev.com:2883` (a dev-looking name). DataWorks PROD datasources use `172.20.173.115` and RDS hosts | Confirm which is real, document DEV and PROD separately |
| 17 | **Non-relational sources undocumented** | Kafka, SLS and OSS datasources exist in DataWorks, but docs only mention OceanBase | Document them (what they feed and how) |
| 18 | **Datasource inconsistencies** | `ar03`-`ar09` point to `aqc01`. Mixed ports across shard groups | Review and fix |
| 19 | **Secret exposed by a tool** | `list_dataworks_datasources` returned a Kafka SASL password for `aws_kafka_b1` in plain text, although it is documented as omitting credentials | Report to the MCP owner, rotate the credential, redact in the tool |
| 16 | **SOP text** | Minor typos ("lifecyle", "inculding"). The Analyst role describes DWS as "Data Warehouse Service", but DWS is a layer, not a service | Fix when migrating |

## Pending

- Define the new structure (the current DingTalk section has 17 folders, from "0. Recruitment" to "99. Discard"; this README should cover the data lead view of the warehouse).
- Review the remaining 69 datasources not yet listed.
- Document how we use the platform: workspaces and projects (e.g. `Basehub_mx`, `Basehub_mx_dev`) and their DEV/PROD mapping (hypothesis to verify: a standard-mode workspace binds one MaxCompute project per environment), resource groups, VPN and access, domains, entities, layers and databases, one by one.
- Keep the table and datasource inventory (sections 3.1 and 10) for the final review step, comparing it against the rules defined here.
- Decide where this section is published.
- Confirm the corrections in section 11 and mark them as the *target state*.
- Add diagrams (flow, layers, roles). The original material only has screenshots.
- Add the local (Docker) setup once the installation is finished.
