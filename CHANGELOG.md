# Changelog

## 0.1.0 — Unreleased (initial feature set)

The first cohesive cut: SDK + 29 reference mappings + master-data catalog +
local web UI + CLI + sink layer + LLM-assisted onboarding + Security
Lake-compatible output + live tail mode.

### SDK
- DSL executor with 12 op kinds: `const`, `path`, `group`, `raw`, `lookup`,
  `time` (iso8601 / epoch_ms / epoch_s / strptime), `range`, `int`, `bool`,
  `expr`, **`for_each`** (array fan-out into OCSF object arrays).
- Structural validator (`required`, `at_least_one`, `activity_id` enum,
  `category_uid` sanity).
- OCSF schema loader over a vendored `ocsf-schema` submodule (pinned to
  1.9.0-dev).
- Coverage scoring (`required` + `recommended` populated × class).
- Tail / stream helpers (`tail_file`, `stream_apply`).
- Registry, lint (CI gate), catalog join.
- LLM provider abstraction (Anthropic, OpenAI, fixture for offline / CI).
- Two-phase mapping generator (`suggest_classes` → `draft_mapping`).

### CLI — `ocsf-mapper <subcommand>`
```
apply     <mapping> <input> [output] [--sink ...]
validate  <events.jsonl> <class>
list      [--folder ...] [--format table|json]
catalog   [<catalog.json>]
lint      [<folder>]
generate  <source> <sample> [output] [--provider ...]
tail      <mapping> <file> [output] [--sink ...] [--from-start]
serve     [--port ...] [--host ...]
```

### Sinks
- `JsonlSink`, `CsvSink` (flattened), `StdoutSink`.
- `ParquetSink` (single-file, requires `pyarrow`).
- `SecurityLakeSink` — partitioned Parquet at
  `<root>/<class_uid>/eventDay=YYYYMMDD/*.parquet`. Compatible with AWS
  Security Lake's custom-source ingest layout.

### Web UI — `ocsf-mapper serve`
FastAPI + Jinja2 + HTMX + Monaco (CDN). Bound to 127.0.0.1.
- Homepage card grid with priority badges and coverage bars.
- Per-source page with five HTMX-swappable tabs:
  *Sample · Output · Mapping · Validation · Coverage*.
- Output tab: drop a log → side-by-side raw / OCSF / validation.
- Mapping tab: Monaco JSON editor + server-side save that re-lints
  against the pinned sample before writing.
- Validation tab: full report across the pinned sample with a recurring-
  issues rollup.
- Coverage tab: per-class bars + missing required/recommended lists.
- New-source wizard at `/new`: upload sample → LLM drafts mapping →
  review in Monaco → save (lint gate same as the editor).

### Mappings (29 sources)
Critical: `windows_event_log`, `sysmon_process`, `auditd_file`,
`palo_alto`, `cloudtrail`, `azure_ad_signin`, `crowdstrike_falcon`,
`splunk_es_alert`. High: `nginx`, `apache`, `cloudflare`, `okta`,
`sshd`, `vpc_flow_logs`, `wazuh`, `falco_kernel`, `zeek_dns`,
`suricata_alert`, `google_workspace`, `m365_email`, `qualys_scan`,
`aws_config`. Medium: `osquery_inventory`, `jamf_inventory`,
`prisma_cloud`, `waf_logs`, `dlp_events`, `ueba_alert`. Low: `cron`.

Hit OCSF classes across 6 of 8 categories:
- System Activity: `process_activity`, `kernel_activity`,
  `scheduled_job_activity`, `file_activity`
- Findings: `security_finding`, `detection_finding`,
  `vulnerability_finding`
- IAM: `authentication`, `entity_management`
- Network: `network_activity`, `http_activity`, `dns_activity`,
  `email_activity`
- Discovery: `inventory_info`, `config_state`,
  `device_config_state_change`
- Application: `api_activity`

Not yet mapped: `remediation` and `unmanned_systems` categories;
Windows-extension classes (`registry_key_activity` etc).

### Catalog
- `catalog.json` is the master-data file: vendor + priority + OCSF
  category/class + description per source. All 29 mappings carry the
  same metadata fields at the top of their JSON.
- `ocsf-mapper catalog` prints the screenshot-style summary table.

### Quality
- 165+ tests across SDK, sinks, providers, generator, web, stream.
- ~91% line coverage.
- GitHub Actions CI on Python 3.9 / 3.11 / 3.12.
- Deterministic sample generator (`scripts/generate_samples.py`).

### Commit timeline (this branch)

```
99afe59 feat(stream): tail mode
efec7e7 feat(sinks): SecurityLakeSink
c6b97c7 feat(web): step 4 — new-source wizard
8decc99 feat(web): step 3 — coverage bars + Coverage tab
1d2163b feat(web): step 2 — Validation tab + recurring issues
eb13785 feat(web): step 1 — Monaco mapping editor + save+lint
adcb112 fix(web): migrate to new Jinja2 TemplateResponse signature
eed5922 feat(web): Phase B session 1 — local web UI
0b2d37e feat(for_each, providers, generate): close Phase A engine gaps
bffc0c8 feat(cli,sinks,ci): ship Phase A — CLI, sinks, GitHub Actions
854290d feat(catalog): master-data catalog + 12 vendor mappings
660ddbe feat(mappings): 9 mappings covering all major OCSF categories
9b3b805 feat(mappings): 5 new sources, scale samples to ~100 events
6c4070a feat(phase-a): port SDK from prototype with tests
afff28a init: repo scaffold, plan doc, ocsf-schema submodule
```
