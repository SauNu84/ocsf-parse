# Changelog

## 0.3.0 — 2026-06-02 (Distribution + Buckets B & C complete)

Three themes since 0.2.0: **distribution** (PyPI, Docker, landing
page, Spark UDF reference), **mapping coverage** (Bucket B — 8/8
OCSF categories), and **production hardening** (Bucket C — audit
log, Prometheus, mapping versioning, provider test mocks, replay
tool).

### Distribution
- **PyPI publish workflow** (`6c2d27a`): tag-triggered Trusted
  Publisher pipeline. Version-mismatch sanity check refuses to build
  if pyproject, `__init__.py`, and the tag disagree.
- **Docker image** (`a76b42b`): `.github/workflows/docker.yml` pushes
  to `ghcr.io/saunu84/ocsf-mapper` on tag push and main. Single-stage
  python:3.12-slim + tini; default CMD is `serve --host 0.0.0.0`.
- **Spark UDF reference** (`75fe76c`): runnable PySpark job at
  `examples/spark/cloudtrail_udf.py` + companion README. Closes the
  blueprint → "you can copy this and run it" gap from
  DESIGN_DISTRIBUTED.md §3a.
- **Landing page** (`75fe76c`): static site at `docs/` deployed via
  GitHub Pages. Fetches `catalog.json` live so the table stays in
  sync with the repo without doc edits.
- **DESIGN_DISTRIBUTED.md** (`04d51fd`): architecture doc covering
  three runtime adapters (Spark / Flink / Vector) + CI-gate patterns.
- **RELEASE_CHECKLIST.md** (`c525d5e`): one-time setup + cut-release
  recipe.

### Bucket B — mapping coverage (8/8 OCSF categories)
- **GitHub Enterprise audit** (`1f6d6cc`): `github_audit`
- **GitLab audit_events** (`1f6d6cc`): `gitlab_audit`
- **Slack Enterprise Grid audit** (`1f6d6cc`): `slack_audit`
- **Kubernetes API server audit** (`1f6d6cc`): `k8s_audit`
- **CEF generic parser** (`516c5fd`): new `parser: "cef"` kind in the
  DSL. `cef_generic` mapping unlocks Fortinet / Symantec / Cisco ASA
  / McAfee / Trend Micro / Palo Alto without per-vendor mappings.
- **LEEF generic parser** (`516c5fd`): `parser: "leef"`. Supports
  LEEF 1.0 (tab-delimited) and LEEF 2.0 (custom delimiter).
- **Schema extensions loader** (`516c5fd`): `Schema.load_class` now
  walks `ocsf-schema/extensions/<ext>/events/`. `windows_registry`
  mapping uses the Windows extension's `registry_key_activity`
  (class_uid 201001).
- **`soar_remediation`** (`b5cf970`): closes OCSF category 7
  (Remediation). Generic SOAR playbook execution shape (Splunk SOAR
  / Tines / XSOAR).
- **`drone_telemetry`** (`b5cf970`): closes OCSF category 8
  (Unmanned Systems). ASTM F3411 Remote ID broadcast.

**Final state: 38 mappings, 20 OCSF classes, 8/8 categories.**

### Bucket C — production hardening
- **Audit log of mapping edits** (`0095301`): NDJSON log at
  `<root>/audit/mapping_edits.ndjson`. Web app writes one event per
  save (Mapping editor or wizard) — saved, rejected, or lint-failed.
  New `/audit` HTML view in the top nav. User attribution via
  `OCSF_AUDIT_USER` / `USER` / `USERNAME`.
- **Prometheus /metrics endpoint** (`0095301`): seven gauges and
  counters — mapping count, lint pass/fail, coverage average, edit
  counters, schema-version label. Stdlib output, no prometheus_client
  dep added.
- **`mapping_version` field** (`4841b75`): semver string at the top
  of each mapping. Lint emits a non-fatal warning when absent
  ("OVERALL: PASS (N warning(s))"). All 38 reference mappings
  backfilled to 1.0.0.
- **Provider test mocks** (`14fff23`): 15 new tests using sys.modules
  injection to fake the Anthropic / OpenAI SDKs. Brings the provider
  package from ~50% to 100% coverage.
- **Replay tool** (`62d1eb4`): `ocsf-mapper replay <input> <mapping>
  <output>` re-applies a new mapping over historical OCSF
  Parquet/JSONL output by walking `raw_data`. Skips events without
  raw_data; non-zero exit if 0 events remapped (CI-gate friendly).

### CLI surface
8 → **12 subcommands**: + `replay`, + `apply` flags `--workers`
and `--redact` (from 0.2.0).

### Quality
- Tests: 243 → ~310.
- Coverage: ~92%.
- New web routes: `/audit`, `/metrics`.
- All 38 mappings lint clean on Python 3.9 / 3.11 / 3.12 CI.

---

## 0.2.0 — 2026-05-30 (Phase D + perf series)

Closed most of the Phase D items + a five-commit perf series in response
to the "10 TB workload" scaling question. Everything still backwards
compatible with 0.1.0; the perf wins are opt-in.

### Phase D
- **Schema-bump diff** (`01904ee`): `ocsf-mapper schema-diff [<ref>]`
  compares the current OCSF schema against an older git ref of the
  submodule and joins per-class diffs against mappings to surface silent
  breakage before the next CI run.
- **PII redaction layer** (`6de0d9d`): `RedactingSink` wraps any sink;
  scrubs email / ipv4 / ssn / phone / jwt / Luhn-valid ccn by default or
  a chosen subset. CLI: `apply ... --redact [kind ...]`.
- **Live-tail UI** (`8cda605`): per-source Output tab gains a "Live tail"
  toggle. Server-Sent Events stream OCSF events to the browser as lines
  append to the source file.

### Performance series
- **Regex cache + streaming input** (`6cccb62`): `lru_cache` on
  `re.compile` in `parse_record`; CLI iterates input line-by-line.
  Bounded input memory + ~1.5-2× on regex sources.
- **orjson fast-path** (`619776b`): `pip install ocsf-mapper[fast]`
  pulls in orjson. New `_fastjson.py` shim routes JSON parse/dump
  through orjson when available; stdlib fallback. 5-10× per call.
- **Streaming SecurityLakeSink + Parquet schema** (`522ed29`):
  `SecurityLakeSink(flush_every=50_000, schema=…)` rolls a fresh
  `part-NNNNN.parquet` per partition every N events. Memory bounded
  regardless of input size. `infer_schema_from(sample_event)` builds a
  `pa.Schema` so subsequent flushes skip type inference.
- **Multiprocess apply** (`1b7039c`): `apply --workers N` splits input by
  line-aligned byte ranges, fans out via `ProcessPoolExecutor`. JSONL /
  CSV / Parquet sinks get per-worker output files; SecurityLakeSink
  uses a per-worker `file_prefix`. Linear speedup to CPU count.
- **Benchmark subcommand** (`8d4cdb5`): `ocsf-mapper benchmark
  <mapping> <sample>` reports events/sec, MB/sec, and per-phase wall
  time (parse / route / map / write). Surprise finding: `map_record`
  dominates at ~90% on both JSON and regex sources — the DSL
  dictionary-walking cost, not JSON parse.

### Combined throughput
~30-50× over the 0.1.0 single-process baseline on an 8-core box. 10 TB
moves from ~40 days to ~1-2 days with bounded memory. For larger
workloads, the tool is intended as a *mapping development* environment;
the JSON DSL travels into a distributed runtime for production (see
PLAN.md §12).

### CLI surface
8 → **10 subcommands**: `apply` (now with `--workers` and `--redact`),
`validate`, `list`, `catalog`, `lint`, `schema-diff` (new),
`benchmark` (new), `generate`, `tail`, `serve`.

### Quality
- Tests: 176 → ~225, coverage ~90% (with the orjson + parquet extras
  installed).

---

## 0.1.0 — Unreleased (initial feature set)

The first cohesive cut: SDK + 29 reference mappings + master-data catalog +
local web UI + CLI + sink layer + LLM-assisted onboarding + Security
Lake-compatible output + live tail mode.

### SDK
- DSL executor with 11 op kinds: `const`, `path`, `group`, `raw`, `lookup`,
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
