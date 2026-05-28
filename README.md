# ocsf-parse

Self-service tool that maps any log source into [OCSF](https://github.com/ocsf/ocsf-schema) events
through a small declarative JSON DSL. One Python engine, JSON config per source,
LLM-assisted onboarding (Phase C), CI-linted, schema-validated.

[![CI](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml/badge.svg)](https://github.com/SauNu84/ocsf-parse/actions/workflows/ci.yml)

## What it does

29 reference mappings cover 14 OCSF event classes across 6 categories — from
Windows Event Log / Sysmon / auditd through CloudTrail / Okta / Azure AD to
Suricata / Wazuh / CrowdStrike. See [`catalog.json`](./catalog.json) for the
full master-data view, or run `ocsf-mapper catalog` after install.

## Quickstart

```bash
git clone --recurse-submodules https://github.com/SauNu84/ocsf-parse
cd ocsf-parse
pip install -e .[dev]

# Browse what's available
ocsf-mapper list                       # table view
ocsf-mapper catalog                    # master-data table (vendor + priority + OCSF class)

# Map a log to OCSF events (JSONL by default; sink inferred from extension)
ocsf-mapper apply mappings/cloudtrail.json samples/cloudtrail.jsonl out.jsonl
ocsf-mapper apply mappings/okta.json       samples/okta.jsonl       out.csv
ocsf-mapper apply mappings/sshd.json       samples/sshd.log         # stdout

# Pipe stdin → stdout
cat samples/cloudtrail.jsonl | ocsf-mapper apply mappings/cloudtrail.json - | jq .

# Validate the output against the OCSF schema
ocsf-mapper validate out.jsonl authentication

# CI gate — lint every mapping against its pinned sample
ocsf-mapper lint                       # exits 0 iff all mappings pass
```

If you cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

## Repository layout

```
mappings/         JSON DSL configs per log source
samples/          Paired sample log files (used by lint and tests)
catalog.json      Master-data: vendor + priority + OCSF target per source
ocsf-schema/      Vendored ocsf/ocsf-schema (git submodule, pinned)
src/ocsf_mapper/  SDK
  apply.py        DSL executor + public apply()/apply_stream()
  ops.py          11 op kinds (const, path, lookup, time, range, expr, ...)
  validate.py     Structural validator
  registry.py     Programmatic mapping inventory
  catalog.py      catalog.json reader + table printer
  lint.py         CI gate
  schema.py       OCSF schema loader (categories, classes, dictionary)
  sinks/          Output destinations (jsonl, csv, parquet, stdout)
  cli.py          ocsf-mapper CLI entry point
scripts/
  generate_samples.py   Deterministic sample-data generator
  lint_mappings.py      Thin wrapper around python -m ocsf_mapper.lint
tests/            pytest suite (120 tests, ~95% coverage)
```

## SDK

```python
from ocsf_mapper import apply_stream, validate, list_mappings
from ocsf_mapper.sinks import JsonlSink

# Load a mapping config (JSON DSL — see DSL reference in PLAN.md §4)
import json
config = json.loads(open("mappings/cloudtrail.json").read())

# Apply it to a stream of raw lines
events = list(apply_stream(config, open("samples/cloudtrail.jsonl")))

# Validate
for ev in events:
    issues = validate(ev, class_name="api_activity")
    assert not issues, issues

# Or use a sink
with JsonlSink("out.jsonl") as sink:
    sink.write_many(apply_stream(config, open("samples/cloudtrail.jsonl")))
```

## Adding a new log source

1. Drop your sample (JSON / regex-parseable text) into `samples/<name>.<ext>`.
2. Write `mappings/<name>.json` per the DSL in [`PLAN.md`](./PLAN.md) §4.
3. Add an entry to `catalog.json` with vendor + priority + OCSF target.
4. `ocsf-mapper lint mappings/` — must exit 0.
5. `pytest` — must stay green.

Phase C (the LLM-assisted generator) will automate steps 1–4. See the plan.

## Status

- [x] **Phase A — SDK** (this branch): pip-installable package, CLI, sinks, lint,
      29 reference mappings, master-data catalog, GitHub Actions CI.
- [ ] **Phase B — Web UI** (FastAPI + HTMX + Monaco): per-source detail page,
      live drop-log-to-OCSF flow, mapping editor.
- [ ] **Phase C — LLM wizard**: Anthropic / OpenAI provider abstraction;
      `ocsf-mapper generate <source> <sample>` produces a draft mapping.
- [ ] **Phase D — Polish**: coverage report, schema-bump diff, stream/tail mode,
      Parquet partitioning for Security Lake compatibility.

See [`PLAN.md`](./PLAN.md) for the full architecture and design decisions.
