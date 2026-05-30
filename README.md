# ocsf-parse

Self-service tool that maps any log source into [OCSF](https://github.com/ocsf/ocsf-schema)
events through a small declarative JSON DSL. One Python engine, JSON config
per source, master-data catalog, CI-linted, schema-validated, with a local
web UI and LLM-assisted onboarding.

[![CI](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml/badge.svg)](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml)

## What it does

**29 reference mappings**, **17 OCSF event classes**, **6 of 8 OCSF
categories** — from Windows Event Log / Sysmon / auditd through CloudTrail /
Okta / Azure AD to Suricata / Wazuh / CrowdStrike. Each mapping ships with
a paired ~100-event sample, is lint-checked on every PR, and validates
against the vendored OCSF schema.

| OCSF category | Classes covered | Sources |
|---|---|---|
| System Activity | `file_activity`, `kernel_activity`, `process_activity`, `scheduled_job_activity` | auditd_file, dlp_events, falco_kernel, sysmon_process, cron |
| Findings | `security_finding`, `detection_finding`, `vulnerability_finding` | wazuh, splunk_es_alert, crowdstrike_falcon, suricata_alert, qualys_scan, ueba_alert |
| IAM | `authentication`, `entity_management` | okta, sshd, cloudtrail (ConsoleLogin), windows_event_log, azure_ad_signin |
| Network | `network_activity`, `http_activity`, `dns_activity`, `email_activity` | nginx, apache, cloudflare, palo_alto, vpc_flow_logs, waf_logs, zeek_dns, m365_email, google_workspace |
| Discovery | `inventory_info`, `config_state`, `device_config_state_change` | osquery_inventory, aws_config, jamf_inventory, prisma_cloud |
| Application Activity | `api_activity` | cloudtrail (non-login) |

Browse the master-data view with `ocsf-mapper catalog` or
[`catalog.json`](./catalog.json).

## Quickstart

### From PyPI (once 0.3.0 ships)

```bash
pip install 'ocsf-mapper[web,parquet,fast]'
ocsf-mapper list
```

### From source (for development)

```bash
git clone --recurse-submodules https://github.com/SauNu84/ocsf-parse
cd ocsf-parse
pip install -e '.[dev,web,parquet,fast]'    # full feature set incl. orjson
```

### From Docker (zero-install)

```bash
docker run --rm -p 8000:8000 ghcr.io/saunu84/ocsf-mapper:0.3.0
# → web UI on http://127.0.0.1:8000

# or pin to "latest" tag:
docker run --rm ghcr.io/saunu84/ocsf-mapper:latest list
```

> `[fast]` pulls in `orjson` — 5-10× faster JSON parse/dump than stdlib.
> Drop-in via `ocsf_mapper._fastjson`; falls back to stdlib if absent.

### CLI

```bash
# Browse what's available
ocsf-mapper list                       # table of mappings
ocsf-mapper catalog                    # master-data: vendor + priority + OCSF class

# Map a log to OCSF events (sink inferred from output extension)
ocsf-mapper apply mappings/cloudtrail.json samples/cloudtrail.jsonl out.jsonl
ocsf-mapper apply mappings/okta.json       samples/okta.jsonl       out.csv
ocsf-mapper apply mappings/sshd.json       samples/sshd.log         # → stdout

# Pipe stdin → stdout
cat samples/cloudtrail.jsonl | ocsf-mapper apply mappings/cloudtrail.json - | jq .

# Partitioned Parquet for AWS Security Lake
ocsf-mapper apply mappings/cloudtrail.json samples/cloudtrail.jsonl out/ --sink security-lake
# → out/<class_uid>/eventDay=YYYYMMDD/part-NNNNN.parquet

# tail -f a live log
ocsf-mapper tail mappings/nginx.json /var/log/nginx/access.log out.jsonl

# Validate already-OCSF events
ocsf-mapper validate out.jsonl authentication

# CI gate — re-lint every mapping against its pinned sample
ocsf-mapper lint                       # exits 0 iff all mappings pass

# LLM-assisted mapping draft (needs ANTHROPIC_API_KEY or OPENAI_API_KEY)
ocsf-mapper generate my_new_source samples/my_new_log.jsonl mappings/my_new.json

# Detect breaking OCSF schema changes before a submodule bump bites
ocsf-mapper schema-diff [<git-ref>]    # default: HEAD~1 of ocsf-schema submodule

# Throughput diagnostic: per-phase timing (parse / route / map / write)
ocsf-mapper benchmark mappings/cloudtrail.json samples/cloudtrail.jsonl

# Scale up: fan out across N worker processes (linear speedup to CPU count)
ocsf-mapper apply mappings/cloudtrail.json input.log out.jsonl --workers 8

# Redact PII before writing (email / ipv4 / ssn / phone / jwt / Luhn-valid ccn)
ocsf-mapper apply mappings/X.json input.log out.jsonl --redact

# Local web UI (127.0.0.1 only) — includes live-tail mode on the Output tab
ocsf-mapper serve                      # → http://127.0.0.1:8000
```

For a 10 TB-class workload, combine `--workers N` with `--sink security-lake`
and the streaming SecurityLakeSink (auto-rolls part files every 50 000 events
per partition). See [§"At scale"](#at-scale) below for the architecture.

If you cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

### Web UI

`ocsf-mapper serve` launches a FastAPI + HTMX + Monaco app on `127.0.0.1`.

- **Homepage** — card grid of every mapping with priority badge, OCSF
  class+uid, lint status, and a coverage bar.
- **Per-source page** — five HTMX-swappable tabs:
  - *Sample* — raw lines of the pinned sample.
  - *Output* — drop any log file → side-by-side raw / mapped OCSF /
    per-event validation.
  - *Mapping* — Monaco JSON editor. Save runs the linter against the
    pinned sample server-side and only writes the file if it passes.
  - *Validation* — full validator report across the pinned sample with a
    recurring-issues rollup.
  - *Coverage* — per-class bars (required + recommended attrs populated)
    + lists of missing fields.
- **`/new` wizard** — upload a sample, fill in vendor / priority, the
  generator drafts a mapping via the configured LLM provider, you review
  the JSON in Monaco, hit save, the linter gate runs before the file is
  written.

## SDK

```python
from ocsf_mapper import apply_stream, validate, list_mappings
from ocsf_mapper.sinks import JsonlSink
from ocsf_mapper.sinks.security_lake import SecurityLakeSink, infer_schema_from
from ocsf_mapper.coverage import coverage
from ocsf_mapper.stream import stream_apply
from ocsf_mapper.parallel import apply_parallel
from ocsf_mapper.benchmark import benchmark
from ocsf_mapper.redact import RedactingSink
import json

config = json.loads(open("mappings/cloudtrail.json").read())

# Batch
with JsonlSink("out.jsonl") as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Partitioned Parquet for downstream Security Lake ingest. Streams to disk
# every flush_every events per (class_uid × eventDay) partition — memory
# is bounded regardless of input size.
schema = infer_schema_from(next(iter(apply_stream(config, open("samples/cloudtrail.jsonl")))))
with SecurityLakeSink("out_dir", flush_every=50_000, schema=schema) as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Multi-process for 10 TB-class workloads
apply_parallel(config, "huge.log", "out.jsonl", n_workers=8, sink_kind="jsonl")

# Coverage scoring (what % of required + recommended attrs are populated)
print(coverage(config))

# Per-phase timing
print(benchmark(config, "samples/cloudtrail.jsonl"))

# PII redaction wrapper around any sink
with RedactingSink(JsonlSink("scrubbed.jsonl")) as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Live tail
import threading
stop = threading.Event()
with JsonlSink("live.jsonl") as sink:
    stream_apply(config, "/var/log/cloudtrail.log", sink, stop=stop)
```

## At scale

For 10 TB-class workloads on a single box:

| Knob | Effect |
|---|---|
| `pip install ocsf-mapper[fast]` | orjson swap — 5-10× faster JSON parse/dump |
| `--workers N` | Linear speedup to CPU count |
| `--sink security-lake` | Streaming partitioned Parquet, memory bounded |
| `infer_schema_from(...)` + `schema=` | Skip pyarrow type re-inference per flush |

Combined: ~30-50× over the single-threaded baseline. On an 8-core box this
puts 10 TB at ~1-2 days instead of ~40 days. See
[`BENCHMARKS.md`](./BENCHMARKS.md) for per-mapping throughput numbers
and the phase breakdown that motivates the multi-process gain.

For genuinely large workloads, the tool is intended as a **mapping
*development*** environment — develop the JSON DSL config locally, then
ship the *same config* to a real distributed runtime (Spark / Flink /
Beam / Vector) for production. See [`DESIGN_DISTRIBUTED.md`](./DESIGN_DISTRIBUTED.md)
for the architecture: how the JSON DSL travels into a Spark UDF, a Flink
streaming job, or Vector/Fluentbit agents, plus CI-gate patterns for
schema-bump pre-flight and coverage drift.

## Repository layout

```
mappings/                JSON DSL configs per log source
samples/                 Paired sample log files (used by lint and tests)
catalog.json             Master-data: vendor + priority + OCSF target per source
ocsf-schema/             Vendored ocsf/ocsf-schema (git submodule, pinned)
src/ocsf_mapper/
  apply.py               DSL executor + public apply()/apply_stream()
  ops.py                 11 op kinds (const, path, group, raw, lookup, time,
                          range, int, bool, expr, for_each)
  validate.py            Structural validator
  registry.py            Programmatic mapping inventory
  catalog.py             catalog.json reader + table printer
  coverage.py            Per-class completeness scoring
  lint.py                CI gate
  schema.py              OCSF schema loader (categories, classes, dictionary)
  generate.py            LLM-assisted two-phase mapping generator
  stream.py              tail -f-style streaming helpers
  providers/             LLM provider abstraction (Anthropic, OpenAI, fixture)
  sinks/                 Output destinations (jsonl, csv, parquet,
                          security-lake, stdout)
  web/                   FastAPI + Jinja2 + HTMX app
  cli.py                 ocsf-mapper CLI entry point
  parallel.py            apply_parallel: multi-process fan-out
  benchmark.py           Per-phase throughput diagnostic
  stream.py              tail -f-style streaming helpers
  schema_diff.py         Detect schema-bump breakage vs older git ref
  redact.py              PII redaction (email/ipv4/ssn/phone/jwt/ccn)
  _fastjson.py           orjson-or-stdlib JSON shim
scripts/
  generate_samples.py    Deterministic sample-data generator
  lint_mappings.py       Thin wrapper around python -m ocsf_mapper.lint
tests/                   pytest suite (~225 tests, 90% coverage)
```

## Adding a new log source

1. Drop your sample (JSON / regex-parseable text) into `samples/<name>.<ext>`.
2. Write `mappings/<name>.json` per the DSL in [`PLAN.md`](./PLAN.md) §4 —
   *or* use `ocsf-mapper generate <name> samples/<name>.<ext>` (or the
   web UI's `/new` wizard) to draft one with LLM assistance.
3. Add an entry to `catalog.json` with vendor + priority + OCSF target.
4. `ocsf-mapper lint mappings/` — must exit 0.
5. `pytest` — must stay green.

## Status

- [x] **Phase A — SDK**: pip-installable package, CLI (11 subcommands —
      `apply`, `validate`, `list`, `catalog`, `lint`, `schema-diff`,
      `benchmark`, `diff`, `generate`, `tail`, `serve`), 29 reference
      mappings, master-data catalog, GitHub Actions CI on Python 3.9 /
      3.11 / 3.12.
- [x] **Phase B — Web UI**: homepage card grid (with priority badges and
      coverage bars), per-source page with 5 HTMX-swappable tabs
      (Sample, Output, Mapping editor with Monaco, Validation, Coverage),
      new-source wizard at `/new`.
- [x] **Phase C — LLM-assisted onboarding**: Anthropic / OpenAI / fixture
      provider abstraction, two-phase generator (`suggest_classes` →
      `draft_mapping`), `ocsf-mapper generate` CLI, UI wizard with
      server-side lint gate.
- [~] **Phase D — Polish**:
  - [x] Per-mapping coverage scoring (required + recommended attrs)
  - [x] Partitioned Parquet sink for AWS Security Lake
        (`<root>/<class_uid>/eventDay=YYYYMMDD/*.parquet`)
  - [x] `tail -f` live streaming mode (`ocsf-mapper tail`)
  - [x] Schema-bump diff (`ocsf-mapper schema-diff` joins schema deltas
        against mappings to surface silent breakage)
  - [x] PII redaction layer (`apply ... --redact`; scrubs email / ipv4
        / ssn / phone / jwt / Luhn-valid ccn)
  - [x] WebSocket live-tail UI mode (Server-Sent Events from the Output
        tab stream new OCSF events as the source file appends)
  - [x] Mapping comparison (`ocsf-mapper diff <a.json> <b.json>`)

See [`CHANGELOG.md`](./CHANGELOG.md) for the per-feature commit timeline
and [`PLAN.md`](./PLAN.md) for the original architecture and design
decisions.
