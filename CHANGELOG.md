# Changelog

## 0.4.3 — 2026-06-03 (Regenerate-with-AI + LLM cache + UX polish)

Three follow-on UX wins on top of the v0.4.2 Fix-with-AI feature.

### ♻ Regenerate-with-AI button (`64685d0`)

The natural complement to Fix-with-AI:

  Fix-with-AI    →  "this is mostly right; repair the lint errors"
  Regenerate     →  "this is a wreck; throw it out and re-draft from
                     the pinned sample"

New endpoint `POST /sources/{name}/regenerate-with-ai` reads the
pinned sample and calls the existing two-phase `generate.generate()`
flow — same machinery the `/new` wizard uses, just bound to an
existing source. Returns the fresh draft as a JSON string the
frontend stuffs into Monaco. The user still hits Save; the linter
is always the final gate.

Always-enabled button next to Fix-with-AI. JS `confirm()` warns
about discarding edits before firing the endpoint.

### In-memory LRU cache for AI results (`c672f1a`)

Identical Regenerate/Fix-with-AI inputs return the previous LLM
result instantly — no second API call, no second token spend.

- OrderedDict-based LRU, cap 16, lives for the lifetime of the
  server process. Restart wipes it (intentional — no TTLs or
  persistence to reason about).
- Key shape: `(op, source, content_hash, schema_version)`. Editing
  the sample / switching the OCSF dropdown / typing in Monaco all
  miss correctly; otherwise hit.
- Cache stores **successful results only**. Failures and 503s don't
  poison the cache.
- API response gains `cached: bool`; UI renders *"(cached — no API
  call)"* on the AI notice so users see when they didn't burn a
  spend.

### Monaco loading spinner (`2b25af6`)

The Monaco editor loads its ~5 MB JS bundle from `cdn.jsdelivr.net`
on first paint. Cold-cache that takes 5-10 seconds during which the
editor area was a blank rectangle — looked broken, prompted *"is it
caching anything?"* confusion.

Add a CSS spinner + *"Loading editor… (downloading Monaco JS bundle
on first visit)"* placeholder inside `#monaco-host`. When
`monaco.editor.create()` runs, it replaces the host's children with
its own DOM, so the placeholder auto-vanishes — no JS lifecycle
coupling. Applied to both `partials/mapping_editor.html` (existing
sources) and `partials/wizard_draft.html` (new-source wizard).

### Operational hygiene

- `.env` and `.env.*` files now gitignored (`94d0569`) — defense in
  depth for the LLM provider keys (`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`) that the Fix/Regenerate flows read from env.
- `docs/release/`: OIDC publish re-run pattern documented in
  `RELEASE_CHECKLIST.md` §2.4 (`d888ff2`). Symptom: build job green,
  publish step silently fails, PyPI doesn't list the new version.
  Fix: Actions UI → re-run failed jobs.

---

## 0.4.2 — 2026-06-03 (Fix-with-AI + README screenshots)

Two user-visible additions on top of v0.4.1.

### ✨ Fix-with-AI button in the Mapping tab (`201509b`)

The Mapping-tab toolbar gains a third button next to **Save**:
**✨ Fix with AI**. It's disabled by default; after a save fails the
linter, it enables.

Click it → the server posts the current Monaco buffer + the chosen
schema version to `/sources/<n>/fix-with-ai`. The endpoint re-runs
the linter to capture the current errors, calls the configured LLM
provider (Anthropic / OpenAI / Fixture per `get_provider()`) with
the current mapping + errors + first 5 sample events + the
required-attribute list for the target class(es), and returns the
repaired JSON. The frontend stuffs it into Monaco; the user still
hits **Save**, which re-runs the linter as the final gate.

Why the gate matters: AI is a typing accelerator, not an oracle. The
human reads the diff and the linter validates against the real schema
before anything lands on disk.

New SDK surface — `ocsf_mapper.generate.fix_mapping(current, errors,
sample_lines, *, provider, schema)`. Prompt caps error list at 30
lines and schema context at required + recommended attributes so
token spend stays bounded.

UX details:
- Without a key configured the button still works, but the LLM call
  returns 503 with `code=no_provider`; the UI surfaces a friendly
  orange notice (*"No LLM key found. Set ANTHROPIC_API_KEY or
  OPENAI_API_KEY, or OCSF_LLM_PROVIDER=fixture for offline use."*).
- Schema-version aware: if you've picked OCSF 1.8.0 in the "Lint
  against OCSF" dropdown, the fix call uses 1.8.0's required-attr
  list (so the AI doesn't suggest 1.9.0-only fields).
- Invalid JSON in the buffer short-circuits before the LLM is called
  — *"Fix the syntax first"*. No wasted tokens.

### README screenshots + capture script (`d750a95`)

The README now embeds five UI captures alongside the relevant
sections:

- Homepage (two-pane catalog) under "What it does".
- Snippets tab + Mapping tab in the Web UI section.
- Audit log in a new Audit-trail subsection.
- (Sample-tab capture also produced; held in reserve for the Pages
  landing page.)

`scripts/capture_screenshots.py` is a Playwright headless harness
that re-shoots all five in one command. Seeds the audit log via the
HTTP API before the audit capture so the screenshot isn't an empty
state. Clips the Mapping capture to the toolbar since the jsdelivr
Monaco bundle isn't always reachable in headless.

Also folded in (README only): refresh "38 → 43 mappings", add the
five new sources to the OCSF category table, bump Docker tag
0.3.0 → 0.4.1, drop the stale "once 0.3.0 ships" PyPI heading.

---

## 0.4.1 — 2026-06-03 (Patch: first PyPI release of the v0.4 series)

CI-only patch. No code-level behaviour changes; same feature surface
as 0.4.0. The 0.4.0 git tag and Docker image shipped, but `publish.yml`
failed at its test step before reaching PyPI — so 0.4.0 never landed
there. This release ships the same feature surface to PyPI.

### Fixes
- **CI materialises pinned OCSF schema worktrees** (`5fb313e`): the
  schema-version-selector tests added in 0.4.0 expect
  `ocsf-schema-1.8.0/` + `ocsf-schema-1.7.0/` as sibling worktrees of
  the submodule. Local dev runs `scripts/setup_schema_versions.sh`;
  CI never did. Both `ci.yml` and `publish.yml` now run the script
  right after checkout.
- **Deepen submodule on missing commit** (`1d6ea50`):
  `actions/checkout@v4` does shallow submodule clones, so the pinned
  commits (`3dcb905d` for v1.8.0, `dc6359b4` for v1.7.0) weren't in
  CI's local ocsf-schema. `setup_schema_versions.sh` now checks with
  `git cat-file -e <ref>^{commit}` and runs `fetch --unshallow` on
  miss.

### Why no 0.4.0 on PyPI

The 0.4.0 git tag (`53b3fb4`) and Docker image both exist and are
canonical for that snapshot. PyPI users skip 0.4.0 and install 0.4.1
to get the v0.4 feature surface (5 new mappings, OCSF schema version
selector, two-pane homepage).

---

## 0.4.0 — 2026-06-03 (Coverage + UX: 5 new sources, schema versioning, two-pane homepage)

Three feature areas, each independently shippable but bundled because
they were all written in one focused session and share the v0.4 surface.

### New mappings — 38 → 43 sources (`651f746`)

Five high-value SOC additions:

- **`duo_security`** → IAM › Authentication (3002). Duo Authentication
  Log v2: MFA challenge results (success / denied / fraud / push timeout).
  `fraud` maps to severity_id 5 (Critical) — strongest signal Duo gives.
- **`aws_guardduty`** → Findings › Detection (2004). UnauthorizedAccess,
  CryptoCurrency, Recon, etc. GuardDuty's numeric 1-10 severity is
  table-lookup'd to OCSF's 1-5 scale (1-3 → Low, 4-6 → Medium, 7-8 →
  High, 9-10 → Critical).
- **`microsoft_defender`** → Findings › Detection (2004). Defender for
  Endpoint alerts via AlertEvidence / Graph Security API shape;
  status mapping covers `new` / `inProgress` / `resolved`.
- **`hashicorp_vault`** → Application Activity › API Activity (6003).
  Vault audit log file/syslog shape — every request/response against a
  secrets engine path. `request.operation` (read/list/create/update/
  delete) → OCSF activity_id.
- **`pagerduty`** → Remediation › Remediation Activity (7001). Webhook
  event lifecycle (triggered / acknowledged / escalated / resolved).
  Doubles the Remediation category which previously had a single source.

Real-world event shapes verified against vendor docs; each ships with
a 10-event sample. Discovery: OCSF 1.9.0-dev's Remediation Activity
enum tops out at activity_id=5 (no Investigate=9), so PagerDuty's
triggered/acknowledged map to Detect (5) / Other (99).

### OCSF schema versioning (`bae55cc`)

Until now mappings could only be linted against the single OCSF
version pinned in the `ocsf-schema/` submodule. v0.4 adds a
pinned-version cache so the same mapping can be lint-checked against
older schema releases.

- `Schema(version="1.8.0")` resolves to `<repo>/ocsf-schema-1.8.0/` —
  a git worktree of the submodule at the v1.8.0 release commit
  (`3dcb905d`). Same shape for v1.7.0. Default unchanged (current
  submodule, `1.9.0-dev`).
- Worktrees share the submodule's `.git` directory — disk impact is
  a few MB per pinned version, not a full clone.
- `scripts/setup_schema_versions.sh` is idempotent: re-materialises
  worktrees after a fresh clone. Add new versions by appending
  `<v>:<sha>` to its `SCHEMA_VERSIONS` array.
- `list_available_versions()` auto-discovers sibling `ocsf-schema-*/`
  dirs and reports each one's declared `version.json` string.
- Worktrees are gitignored — derived state, not source.

### Web UI: two-pane homepage, Snippets tab, schema dropdown (`4ee65be`)

The homepage flat 38-card grid is replaced by a navigable two-pane
layout, and each source page gains a per-mapping copy-paste-ready
code tab.

**Homepage**
- KPI strip: sources / OCSF categories / classes / lint-clean.
- Sticky left rail: collapsible OCSF category tree (8 categories →
  20 classes) with per-node counts. Extensions like `windows_registry`
  (class_uid 201001) fold into their parent category.
- Right pane: live search box (matches name / vendor / description)
  + filtered card grid. Filter state syncs to the URL hash
  (`#cat=…&cls=…&q=…`) so views are shareable. Narrow viewport
  collapses to single-column.

**Source page**
- New **Snippets** tab — per-mapping CLI / Python SDK / PySpark UDF /
  Pandas code blocks, each with a copy button. Mapping paths and
  sample filenames templated in so the snippets are ready to paste.
- **Mapping tab** gains a "Lint against OCSF" dropdown listing every
  available schema version. Selection POSTs as `schema_version` to
  `/sources/<n>/save`; the result banner names the version that ran
  the gate so the user knows which schema rejected/accepted the edit.

**Misc fixes**
- `/audit` hint text rewritten — previous wording claimed a cap that
  the template didn't enforce; now reads "showing N events (capped at
  500)".
- `/static/main.css` cache-busted with file mtime via a Jinja
  `asset_v` global — no more `Cmd+Shift+R` after CSS edits.

### Operational hygiene

- `audit/` is now gitignored (`84a57b4`) — per-install runtime state,
  not source. Existing audit log truncated.
- `lint_one()` coalesces identical per-event errors into one summary
  line (`f8c91f4`); 100-event-sample failures drop from ~33 KB to
  ~700 B per audit record.
- PLAN.md Bucket C marked done with commit refs (`c1b9806`) — all
  five production-engineering items had shipped but the scoreboard
  was stale.

### Tests

51+ web/schema tests green; 326 total in the suite. New test coverage:
snippets endpoint shape, mapping-editor version dropdown, save against
pinned schema, missing-version error message, schema registry, pinned
1.8.0 schema loads cleanly.

---

## 0.3.1 — 2026-06-02 (Patch: release pipeline + docs polish)

First-run hardening for the v0.3 release pipeline. No code-level
behaviour changes; just things you'd discover trying to actually
ship v0.3.0.

### Fixes
- **`_fastjson` stdlib fallback test now works on Python 3.12** (`8264127`):
  the stdlib-only fixture used the deprecated `find_module` /
  `load_module` meta_path API to mask orjson. Python 3.12 stopped
  calling that legacy API, so the test couldn't actually hide orjson
  — the assertion `HAS_ORJSON is False` failed. Switched to the
  documented `sys.modules[name] = None` idiom for masking modules.
- **CI now installs every optional extra** (`8264127`): `ci.yml` was
  installing `[dev,parquet]` — no orjson, so the stdlib branch was
  the natural path and the bug above was invisible until the publish
  workflow tried to run with `[fast]` installed. CI now installs
  `[dev,web,parquet,fast]` to match publish.yml.

### Docs
- **`QUICKSTART.md`** (`b07500f`): concrete 5-minute walkthrough using
  the Docker image. Browse the catalog → map a log → web UI →
  generate a new mapping → process at volume. Shows actual output
  instead of abstract flag lists. README points to it at the top of
  "Quickstart".
- **README OCSF category table refresh** (`2ff3dea`): the coverage
  table had 6 rows from before Bucket B finished; now matches the
  headline "8 of 8 OCSF categories" and includes the sources added in
  Bucket B (github_audit, gitlab_audit, slack_audit, k8s_audit,
  cef_generic, leef_generic, windows_registry, soar_remediation,
  drone_telemetry).

---

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
