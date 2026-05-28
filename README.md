# ocsf-parse

Self-service tool that maps any log source into [OCSF](https://github.com/ocsf/ocsf-schema)
events through a small declarative JSON DSL. One Python engine, JSON config
per source, master-data catalog, CI-linted, schema-validated, with a local
web UI and LLM-assisted onboarding.

[![CI](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml/badge.svg)](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml)

## What it does

**29 reference mappings**, **14 OCSF event classes**, **6 of 8 OCSF
categories** — from Windows Event Log / Sysmon / auditd through CloudTrail /
Okta / Azure AD to Suricata / Wazuh / CrowdStrike. Each mapping ships with
a paired ~100-event sample, is lint-checked on every PR, and validates
against the vendored OCSF schema.

Browse the master-data view with `ocsf-mapper catalog` or
[`catalog.json`](./catalog.json).

## Quickstart

```bash
git clone --recurse-submodules https://github.com/SauNu84/ocsf-parse
cd ocsf-parse
pip install -e '.[dev,web,parquet]'    # full feature set
```

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

# Local web UI (127.0.0.1 only)
ocsf-mapper serve                      # → http://127.0.0.1:8000
```

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
from ocsf_mapper.sinks.security_lake import SecurityLakeSink
from ocsf_mapper.coverage import coverage
from ocsf_mapper.stream import stream_apply
import json

config = json.loads(open("mappings/cloudtrail.json").read())

# Batch
with JsonlSink("out.jsonl") as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Partitioned Parquet for downstream Security Lake ingest
with SecurityLakeSink("out_dir") as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Coverage scoring (what % of required + recommended attrs are populated)
print(coverage(config))

# Live tail
import threading
stop = threading.Event()
with JsonlSink("live.jsonl") as sink:
    stream_apply(config, "/var/log/cloudtrail.log", sink, stop=stop)
```

## Repository layout

```
mappings/                JSON DSL configs per log source
samples/                 Paired sample log files (used by lint and tests)
catalog.json             Master-data: vendor + priority + OCSF target per source
ocsf-schema/             Vendored ocsf/ocsf-schema (git submodule, pinned)
src/ocsf_mapper/
  apply.py               DSL executor + public apply()/apply_stream()
  ops.py                 12 op kinds (const, path, lookup, time, range, expr, for_each, ...)
  validate.py            Structural validator
  registry.py            Programmatic mapping inventory
  catalog.py             catalog.json reader + table printer
  coverage.py            Per-class completeness scoring
  lint.py                CI gate
  schema.py              OCSF schema loader (categories, classes, dictionary)
  generate.py            LLM-assisted two-phase mapping generator
  stream.py              tail -f-style streaming helpers
  providers/             LLM provider abstraction (Anthropic, OpenAI, fixture)
  sinks/                 Output destinations (jsonl, csv, parquet, security-lake, stdout)
  web/                   FastAPI + Jinja2 + HTMX app
  cli.py                 ocsf-mapper CLI entry point
scripts/
  generate_samples.py    Deterministic sample-data generator
  lint_mappings.py       Thin wrapper around python -m ocsf_mapper.lint
tests/                   pytest suite (160+ tests, ~91% coverage)
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

- [x] **Phase A — SDK**: pip-installable package, CLI (8 subcommands),
      sinks (5 kinds incl. Security Lake), lint, 29 reference mappings,
      master-data catalog, GitHub Actions CI on 3.9/3.11/3.12.
- [x] **Phase B — Web UI**: homepage card grid, per-source page with 5
      tabs (Sample, Output, Mapping editor with Monaco, Validation,
      Coverage), `/new` wizard.
- [x] **Phase C — LLM-assisted onboarding**: Anthropic / OpenAI / fixture
      provider abstraction, `ocsf-mapper generate` CLI, UI wizard.
- [x] **Phase D — Polish (in progress)**: per-mapping coverage scoring,
      partitioned Parquet sink for AWS Security Lake, `tail -f` live
      streaming mode. Still open: schema-bump diff, WebSocket live-tail
      UI mode.

See [`CHANGELOG.md`](./CHANGELOG.md) for the per-feature commit timeline
and [`PLAN.md`](./PLAN.md) for the original architecture and design
decisions.
