# Spark UDF reference implementation

A runnable PySpark job that uses `ocsf_mapper.apply()` as a UDF, exactly
as sketched in [`DESIGN_DISTRIBUTED.md`](../../DESIGN_DISTRIBUTED.md) §3a.

The job reads raw log lines, applies a mapping config (broadcast once
per job, not per record), and writes Parquet partitioned by `class_uid`
and `event_day` — the same layout as `SecurityLakeSink` produces locally.

## Why this exists

The local `ocsf-mapper apply --workers N` tops out at one host. For
real production volume (>10 TB/day) you want a distributed runtime.
This example proves the bet from `DESIGN_DISTRIBUTED.md`: the JSON DSL
config travels into Spark unchanged; only the runtime shell is new.

## Quickstart

The example runs against the in-repo `samples/cloudtrail.jsonl` so you
can verify it end-to-end without a cluster.

### Prerequisites

```bash
pip install 'pyspark>=3.5' 'ocsf-mapper>=0.2.0'
# Or from the repo root: pip install -e '.[parquet,fast]' && pip install pyspark
```

### Local single-machine run

```bash
# From the repo root
python examples/spark/cloudtrail_udf.py \
    --mapping mappings/cloudtrail.json \
    --input   samples/cloudtrail.jsonl \
    --output  /tmp/ocsf-spark-out
```

You should see ~100 events written to
`/tmp/ocsf-spark-out/class_uid=3002/event_day=2026-05-27/` and
`/tmp/ocsf-spark-out/class_uid=6003/event_day=2026-05-27/` — same
partition layout as the local `SecurityLakeSink`.

### Cluster run

Adapt the args for S3 / HDFS paths:

```bash
spark-submit \
    --master yarn \
    --deploy-mode cluster \
    --py-files dist/ocsf_mapper-0.3.0-py3-none-any.whl \
    examples/spark/cloudtrail_udf.py \
    --mapping s3://config-bucket/mappings/cloudtrail.json \
    --input   s3://raw-logs/cloudtrail/year=2026/ \
    --output  s3://ocsf-lake/cloudtrail/
```

Two things to wire up that the local example skips:

1. **`ocsf-mapper` must be installed on every executor.** Either bake
   it into your Spark image, or use `--py-files dist/...whl`. Same
   version everywhere — different versions of `apply()` across
   executors produce nondeterministic output.

2. **Mapping config is broadcast once per job.** The example does this
   with `sc.broadcast(json.load(...))`. The UDF closes over the
   broadcast variable; per-task imports of `ocsf_mapper.apply` happen
   on each executor.

## Why no validation in the UDF

`validate()` doubles per-event latency (see `BENCHMARKS.md`). At
distributed scale, the recommended pattern is:

- **Lint at PR time** (`ocsf-mapper lint` in CI) — re-validates every
  mapping against its pinned sample.
- **Sample-validate in production** — run `validate()` on a small
  random sample of the output, alert if failure rate drifts.

That's why this example doesn't call `validate()` per row. If you do
want it on the hot path, drop in another UDF; the cost is real but
configurable.

## What the example does NOT cover

- **Schema evolution** — when `ocsf-schema` bumps, you need to
  re-deploy the wheel + the mapping config. See `DESIGN_DISTRIBUTED.md`
  §4 for the schema-bump CI gate that catches this before it lands.
- **Backpressure / retries** — Spark handles task retries natively;
  for streaming you'd want a Structured Streaming job, not this
  batch reference. The mapping UDF stays the same.
- **Replay** — re-processing historical Parquet through a new mapping
  is a separate tool (see v0.3 backlog).

## See also

- [`DESIGN_DISTRIBUTED.md`](../../DESIGN_DISTRIBUTED.md) — the full
  architecture for distributed runtimes.
- [`BENCHMARKS.md`](../../BENCHMARKS.md) — per-mapping throughput on a
  single core; multiply by your executor count for a rough sizing.
