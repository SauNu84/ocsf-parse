# Distributed runtime — design doc

> **Status:** architecture only. No code in this repo yet; this is a
> blueprint for shipping `ocsf-parse` mappings into a real distributed
> ingest pipeline. The local CLI + UI in this repo is the *mapping
> development* environment; production runtimes consume the JSON DSL
> as configuration.
>
> **Audience:** platform / data engineering teams considering ocsf-parse
> for ingest workloads above ~1 TB/day.

---

## 1. Why this doc exists

The local tool's perf series (regex cache, orjson, multiprocess, streaming
Security Lake) pushed single-host throughput from ~2-6 KB/s to ~5-12 KB
events/sec per core. On an 8-core box that's ~50-100 KB events/sec, or
~1-2 days for 10 TB. See [`BENCHMARKS.md`](./BENCHMARKS.md).

That's enough for backfills and dev iteration. It is **not enough** for
production ingest of large fleets (hundreds of TB/day, multi-region,
sub-second e2e latency requirements). The right place to run apply()
at that scale is a distributed compute platform with the runtime
already solved: Spark, Flink, Beam, Vector, or one of the streaming SIEM
adapters.

The architectural bet: **the JSON DSL config travels, the engine
doesn't.** Anything that can load JSON and run a deterministic
per-record transform can be a runtime for ocsf-parse mappings.

## 2. Component model

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       ocsf-parse repo (this)                            │
│                                                                          │
│  mappings/*.json     ──── source of truth for transforms                │
│  catalog.json        ──── master-data: vendor, priority, OCSF target    │
│  ocsf-schema/        ──── pinned schema (submodule)                     │
│                                                                          │
│  CI: ocsf-mapper lint  (re-validates every mapping on every PR)         │
│       ocsf-mapper schema-diff  (breaking-change detector)               │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              │ (mapping JSON + schema bundle)
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       Runtime adapters (NOT in this repo)               │
│                                                                          │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│   │ Spark UDF    │    │ Flink        │    │ Vector / FB  │               │
│   │ (Python)     │    │ (Java/Py)    │    │ (Rust/Lua)   │               │
│   └──────────────┘    └──────────────┘    └──────────────┘               │
│         │                    │                   │                       │
│         └────────────────────┼───────────────────┘                       │
│                              ▼                                           │
│                  S3 / GCS / Azure Blob (Parquet, partitioned)            │
│                  Kafka / Kinesis (NDJSON, streaming)                     │
│                  SIEM / data lake (OpenSearch, Snowflake, BigQuery)      │
└─────────────────────────────────────────────────────────────────────────┘
```

Three boundaries are stable; everything in between is replaceable:

1. **Input boundary** — raw log lines (text or JSON).
2. **Mapping DSL** — JSON config conforming to `PLAN.md` §4.
3. **Output boundary** — OCSF events (JSON dicts).

The runtime's job is to (a) load the mapping JSON on startup, (b) apply
it to records, (c) write to a sink. Steps (a) and (c) are
runtime-specific. Step (b) is what `apply()` does in this repo — and
it's a pure function over a dict, easy to embed.

## 3. Three concrete adapters

### 3a. Spark / PySpark — batch backfills

Best fit: rebuilding a year of logs, one-shot conversions, historical
SIEM ingest. Mostly a Python UDF that imports `ocsf_mapper` and applies
the same mapping JSON to each row.

```python
# pyspark_ocsf.py — Spark UDF using ocsf_mapper's apply()
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import StringType
import json

# Broadcast the mapping config once per job, not per record.
mapping_path = "s3://ocsf-mappings/v0.2.0/cloudtrail.json"
config = spark.sparkContext.broadcast(
    json.loads(spark.sparkContext.textFile(mapping_path).collect()[0])
)

# UDF: ocsf_mapper installed on every executor (pip / dependency wheel).
def apply_to_row(raw: str) -> str:
    from ocsf_mapper import apply
    import json as _json
    event = apply(config.value, raw)
    return _json.dumps(event) if event else None

apply_udf = F.udf(apply_to_row, StringType())

(
    spark.read.text("s3://raw-logs/cloudtrail/year=2026/")
        .withColumn("ocsf", apply_udf(F.col("value")))
        .filter(F.col("ocsf").isNotNull())
        .write.mode("append")
        .partitionBy("class_uid", "event_day")
        .parquet("s3://ocsf-lake/cloudtrail/")
)
```

**Notes**

- Single broadcast → mapping JSON ships once, not per task.
- `ocsf_mapper` pip-installed on every executor — same module on every
  node guarantees deterministic output.
- Spark handles partitioning by `class_uid` / `event_day` natively, so
  the local `SecurityLakeSink` translates 1:1 into a Spark write spec.
- Linear scaling to executor core count; tested patterns get you to TB/hour.

### 3b. Flink — sub-second streaming

Best fit: real-time SOC, low-latency detection pipelines, when "live
tail" needs to scale beyond one machine.

```java
// FlinkOcsfMapper.java — sketch
DataStream<String> raw = env
    .addSource(new FlinkKafkaConsumer<>("cloudtrail-raw", new SimpleStringSchema(), props));

raw.map(new RichMapFunction<String, String>() {
    private transient PythonInterpreter ocsfMapper;

    @Override
    public void open(Configuration parameters) throws Exception {
        // Boot a long-lived Jython / GraalPy / py4j worker per task,
        // load mapping JSON from broadcast state, keep apply() warm.
        ocsfMapper = OcsfMapperBridge.load("/configs/cloudtrail.json");
    }

    @Override
    public String map(String raw) throws Exception {
        return ocsfMapper.apply(raw);  // returns OCSF JSON string
    }
})
.sinkTo(KafkaSink.<String>builder()
    .setBootstrapServers("kafka:9092")
    .setRecordSerializer(KafkaRecordSerializationSchema.builder()
        .setTopic("ocsf-events")
        .setValueSerializationSchema(new SimpleStringSchema())
        .build())
    .build());
```

**Notes**

- Stateless `apply()` means Flink parallelism is unbounded (no shuffles
  needed for the mapping step itself; routing-by-key happens at the
  Kafka sink if you want per-class topics).
- Schema-bump diff (`ocsf-mapper schema-diff`) becomes a deploy-gate
  step — fail the rolling deploy if the new schema breaks any active
  mapping.
- Python ↔ JVM bridge is the awkward part. Alternatives: port `apply()`
  to Java (~500 lines; the DSL is small), or run the Python mapper as a
  sidecar process behind Flink Async I/O.

### 3c. Vector / Fluentbit — host-level shippers

Best fit: agent-on-every-host topologies. No Python required at the
edge; mapping JSON gets *transpiled* into the agent's native DSL
(Vector's VRL or Fluentbit's Lua) at build time.

```
$ ocsf-mapper transpile cloudtrail.json --target vector  > cloudtrail.vrl
$ ocsf-mapper transpile cloudtrail.json --target fluentbit > cloudtrail.lua
```

Mapping → VRL is mostly mechanical: each DSL op maps to a VRL
expression. The hard parts (LLM-generated tables, `for_each`) need
either expansion at transpile time or runtime helper functions.
Recommended scope:

- Keep transpile output **deterministic** — same input JSON, same VRL
  output. So that a `git diff` on the generated VRL is readable.
- **Round-trip the lint**: `ocsf-mapper lint --transpiled` runs the
  generated VRL against the same pinned sample and compares output
  to the Python apply()'s output. Catches transpiler bugs in CI.

This adapter is the most work (transpiler + per-target test harness +
runtime helper library), but it's also the one that unlocks
host-level ingest without paying for a Python runtime per host.

## 4. The CI gate at runtime scale

The local repo's CI already runs `ocsf-mapper lint` and (when bumped)
`ocsf-mapper schema-diff`. A distributed runtime needs three more
gates, all reusing this repo's logic:

1. **Pre-deploy mapping diff** — `ocsf-mapper diff old.json new.json`
   shipped as a deploy-time comment. Reviewers see exactly what changes
   in the OCSF output before merge.

2. **Schema-bump pre-flight** — when bumping the `ocsf-schema`
   submodule, run `schema-diff` against the *production* mapping set
   (not just the in-repo references). Non-zero exit blocks the bump.

3. **Coverage drift alerts** — `coverage()` over the prod mapping set,
   nightly, into a metric. Alert if coverage on a critical-priority
   source drops below threshold (e.g., a Vector rule change quietly
   stopped populating `actor.user.uid`).

All three are dollars-per-PR cheap and prevent the slow drift that
turns OCSF "we're compliant" into "we're producing technically-valid
events with no useful fields."

## 5. Partitioning and storage at TB scale

The local `SecurityLakeSink` writes:

    <root>/<class_uid>/eventDay=YYYYMMDD/part-NNNNN.parquet

This is identical to what AWS Security Lake ingests for custom
sources. At distributed scale, you get this for free from
Spark / Flink / Iceberg / Delta — just declare the partition columns
in the write step and let the runtime handle the file rollovers.

The non-obvious bit: **partition by `class_uid` first, eventDay
second**. The opposite order is more common (date-first) but defeats
class-based pruning. A SOC analyst running "show me all `4002`
HTTP activity in the last 24 h" should hit at most one date partition;
class-first puts that on a single S3 prefix and lets you scan only
that subtree.

Recommended Parquet settings (Iceberg / Delta defaults are close):

- Row group size: ~512 KB-1 MB (default ~128 MB is too coarse for
  per-class queries)
- Compression: zstd level 3-5 (faster than gzip, smaller than snappy)
- Column statistics ON — lets predicate pushdown skip whole row groups
- Bloom filter on `actor.user.name` and `src_endpoint.ip` if those
  show up in typical analyst queries

## 6. Cost / performance model

Rough sizing for a 10 TB/day pipeline:

| Layer | Throughput | Cluster size | Cost / day (AWS us-east-1) |
|---|---:|---:|---:|
| Spark batch | ~50 MB/s/core sustained | 50 cores | ~$30 |
| Flink streaming | ~5 MB/s/core sustained | 100 cores | ~$120 |
| Vector agent (per host) | depends on host log volume | n/a | included in host cost |

These are order-of-magnitude. Actual numbers depend heavily on:

- Mapping complexity (see `BENCHMARKS.md` — DSL `map_record` cost
  ranges from ~50 µs to ~200 µs per event)
- Output format (JSONL ~3× cheaper than Parquet on write)
- Whether `[fast]` (orjson) is installed

For a quick gut-check: take the per-mapping `events/sec` from
`BENCHMARKS.md`, multiply by your core count, divide your daily
volume by the result. That's your hours-per-day of compute.

## 7. What's not in scope for this design

- **Mapping authoring inside the distributed runtime** — keep that in
  the local repo. The UI's `/new` wizard, the LLM provider abstraction,
  Monaco editor, etc. are all dev-time tools.
- **Schema management beyond OCSF** — we vendor `ocsf-schema` and stop
  there. If you need cross-schema (CIM, ECS) routing, that's an
  application-level concern on top.
- **Multi-tenancy / RBAC** — the local UI binds to 127.0.0.1 and runs
  as one user. A distributed runtime presumably has its own RBAC
  (Spark on EMR, Flink on Kubernetes, etc.); we don't reinvent that.

## 8. Open questions

- **Should `ocsf-mapper transpile` ship in this repo or as a separate
  package?** Argument for here: it shares the schema loader + lint.
  Argument against: VRL / Lua codegen is large enough to deserve its
  own test surface and may want a different release cadence.
- **Streaming validation cost** — `validate()` on the hot path doubles
  per-event latency. Recommendation: opt-out in production runtimes
  (run validate on a sampled stream instead).
- **Backwards-incompatible OCSF bumps** — when 2.0.0 ships, mappings
  pinned to 1.9.0-dev need explicit migration. Open: do we keep a
  `schema_version_compat` matrix per mapping, or hard-cut?

## 9. Suggested implementation order

If a team wants to take this on, the cheapest first slice that proves
the model:

1. **Spark UDF** (3a above) running one mapping end-to-end against a
   real production data set. 1-2 weeks. Validates the broadcast pattern,
   the executor pip-install story, and the partitioning layout.
2. **Schema-bump pre-flight in CI** (§4 #2). Half a sprint. High
   leverage — prevents the silent-breakage failure mode.
3. **Vector transpile, single-mapping prototype**. 1-2 weeks. Proves
   that the JSON DSL really does translate cleanly into VRL.
4. **Flink integration** (3b above). Largest of the four. Only worth
   doing if streaming SLOs are firm and Spark micro-batching isn't
   enough.

---

For the live, in-repo Python implementation that this doc references,
see [`PLAN.md`](./PLAN.md) for architecture and
[`CHANGELOG.md`](./CHANGELOG.md) for what's shipped.
