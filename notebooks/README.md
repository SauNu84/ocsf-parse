# notebooks/

Research notebooks against the synthetic OCSF dataset produced by
`scripts/generate_synthetic_data.py`. Not part of the package proper —
this directory is for one-off exploration, demos, and SIEM research.

## Contents

| File | What |
|---|---|
| `siem_exploration.ipynb` | 4-section SIEM research notebook: capacity / threat-hunt / detection-rule prototyping / compliance. Operates on the partitioned Parquet under `data/synthetic/ocsf/`. |
| `_build_notebook.py` | Generator for the notebook above. Easier to edit than the raw `.ipynb` JSON — declare cells as Python strings, re-run to rebuild. |

## Running the notebook

### One-time setup

```bash
# Already covered by the [web] extra; if you only installed core:
pip install pyarrow pandas matplotlib jupyterlab
```

### Generate the dataset

```bash
# From the repo root. ~5 GB Parquet, ~50 min wall-clock on an 8-core box.
python3 scripts/generate_synthetic_data.py --scale 8 --out data/synthetic/ocsf

# Quick smoke test if you just want to validate the notebook (~50 MB):
python3 scripts/generate_synthetic_data.py --scale 0.05 --out data/synthetic/ocsf
```

### Launch the notebook

```bash
jupyter lab notebooks/siem_exploration.ipynb
# or
jupyter notebook notebooks/siem_exploration.ipynb
```

## How the data is laid out

Hive-partitioned Parquet so `pyarrow.dataset` auto-discovers partitions:

```
data/synthetic/ocsf/
  class_uid=2004/                       ← Detection Finding
    event_day=2026-05-15/
      part-abc123.parquet
      part-def456.parquet
    event_day=2026-05-16/
      ...
  class_uid=3002/                       ← Authentication
    event_day=2026-05-15/
      ...
  class_uid=4002/                       ← HTTP Activity
    ...
```

The two partition keys (`class_uid`, `event_day`) are the columns most
queries filter on first — pruning happens at directory level, no scan
of unrelated partitions. Same layout AWS Security Lake expects.

## What's in the data

| | |
|---|---|
| Sources | 43 OCSF mappings — Windows / CloudTrail / Okta / Duo / Defender / GuardDuty / Vault / PagerDuty / etc. |
| Classes | 20 OCSF event classes across all 8 OCSF categories |
| Volume distribution | Realistic — HTTP & API dominate; findings & inventory sparse |
| Time range | 30 days ending today (UTC) |
| Entropy | Synthetic pools — ~1000 users, ~500 IPs (corp / VPN / cloud / partner / known-bad buckets), 3 AWS accounts, 5 regions |
| Anomalies | 5 hand-injected incident scenarios (see notebook for details) |

## Editing the notebook

Edit `_build_notebook.py` (declares cells as plain Python strings) and
re-run `python3 notebooks/_build_notebook.py` to regenerate the `.ipynb`.
The build script makes the cells diff-friendly in git — easier to
review changes than raw notebook JSON.

## Gitignore

`data/` is gitignored. The 5 GB of synthetic Parquet never lands in
the repo. Re-running the generator script is the canonical way to
recreate it.
