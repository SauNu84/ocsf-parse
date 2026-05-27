# ocsf-parse

Self-service tool to map any log source into [OCSF](https://github.com/ocsf/ocsf-schema) events.
One Python engine. JSON mapping configs per source. LLM-assisted onboarding. Schema-validated.

> **Status:** Phase A in progress — see [`PLAN.md`](PLAN.md).

## Quickstart

```bash
git clone --recurse-submodules <repo>
cd ocsf-parse
pip install -e .[dev]

# Lint all reference mappings against their pinned samples
python -m ocsf_mapper.lint mappings/

# Apply one mapping to a sample
python -m ocsf_mapper.cli apply mappings/cloudtrail.json samples/cloudtrail.jsonl out.jsonl
```

If you cloned without `--recurse-submodules`, fetch the schema:

```bash
git submodule update --init --recursive
```

## Layout

See [`PLAN.md`](PLAN.md) §2 for the repository layout and architecture, and §3 for phase
acceptance criteria.
