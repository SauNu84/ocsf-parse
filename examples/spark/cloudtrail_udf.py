"""Spark UDF reference: ocsf_mapper.apply() as a per-row UDF.

Reads raw log lines from --input, applies the mapping config at
--mapping, writes Parquet partitioned by (class_uid, event_day) to
--output. Runs against the in-repo cloudtrail sample by default so you
can verify the end-to-end flow without a cluster::

    python examples/spark/cloudtrail_udf.py \\
        --mapping mappings/cloudtrail.json \\
        --input   samples/cloudtrail.jsonl \\
        --output  /tmp/ocsf-spark-out

Same script runs on a cluster with `spark-submit --master ...`. See
``examples/spark/README.md`` for that path.

The two non-obvious things this example demonstrates:

1. The mapping config is broadcast once per job. The UDF closes over
   the broadcast variable so every executor sees the same config
   without re-reading the file per task.

2. Output is partitioned by ``class_uid`` and ``event_day`` — the same
   layout as the local :class:`ocsf_mapper.sinks.SecurityLakeSink`
   produces, which is what AWS Security Lake's custom-source ingest
   expects.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

# We deliberately import pyspark inside main() so that this file is
# importable (and the README's docs build) on machines without pyspark.


def _apply_to_line_factory(config_bc):
    """Build a row-level UDF that applies the broadcast mapping to a raw line.

    Returning a JSON string is the path of least resistance — Spark can
    then parse it back with ``from_json`` if you want a typed
    DataFrame, or just write the string column straight to Parquet via
    a sibling field. This example takes the structured path so the
    output partition columns can be extracted.
    """
    from pyspark.sql.types import IntegerType, StringType, StructField, StructType

    def _apply(raw: str):
        # Imports inside the UDF so they only fire on executors.
        from ocsf_mapper.apply import apply as _apply_fn

        event = _apply_fn(config_bc.value, raw)
        if event is None:
            return None
        cls_uid = event.get("class_uid")
        time_ms = event.get("time")
        event_day = "unknown"
        if isinstance(time_ms, int) and time_ms > 0:
            event_day = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return (
            int(cls_uid) if isinstance(cls_uid, int) else None,
            event_day,
            json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        )

    schema = StructType([
        StructField("class_uid", IntegerType(), nullable=True),
        StructField("event_day", StringType(),  nullable=True),
        StructField("ocsf_json", StringType(),  nullable=True),
    ])
    return _apply, schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True,
                    help="Path to a mappings/<name>.json file (local or s3://).")
    ap.add_argument("--input",   required=True,
                    help="Raw log input (directory or single file; local, s3://, hdfs://).")
    ap.add_argument("--output",  required=True,
                    help="Output Parquet root (local, s3://, hdfs://).")
    ap.add_argument("--app-name", default="ocsf-mapper-cloudtrail",
                    help="Spark application name.")
    args = ap.parse_args()

    # Lazy import so the file is importable without pyspark installed.
    from pyspark.sql import SparkSession, functions as F

    spark = (
        SparkSession.builder
        .appName(args.app_name)
        .getOrCreate()
    )
    sc = spark.sparkContext

    # 1. Load the mapping config on the driver, broadcast to executors.
    #    Using textFile + collect lets us read local AND s3:// uniformly.
    config_text = "\n".join(sc.textFile(args.mapping).collect())
    config = json.loads(config_text)
    config_bc = sc.broadcast(config)
    print(f"[ocsf-spark] broadcast mapping for source={config.get('source_name')!r}",
          file=sys.stderr)

    # 2. Read raw lines. spark.read.text gives a DataFrame with one
    #    column 'value' per line — perfect input shape for the UDF.
    raw_df = spark.read.text(args.input)

    # 3. Build the UDF + apply. We keep ocsf_json as a string so the
    #    write path doesn't need a schema-of-the-event (which would
    #    vary per source). Downstream queries can from_json() if they
    #    want a typed view.
    apply_fn, apply_schema = _apply_to_line_factory(config_bc)
    apply_udf = F.udf(apply_fn, apply_schema)

    mapped = (
        raw_df
        .filter(F.length(F.col("value")) > 0)
        .select(apply_udf(F.col("value")).alias("r"))
        .filter(F.col("r").isNotNull())
        .select(
            F.col("r.class_uid").alias("class_uid"),
            F.col("r.event_day").alias("event_day"),
            F.col("r.ocsf_json").alias("ocsf_json"),
        )
        .filter(F.col("class_uid").isNotNull())
    )

    # 4. Write partitioned Parquet. partitionBy(class_uid, event_day)
    #    produces the same `class_uid=XXX/event_day=YYYY-MM-DD/`
    #    layout that AWS Security Lake's custom-source ingest expects.
    (
        mapped.write
        .mode("overwrite")
        .partitionBy("class_uid", "event_day")
        .parquet(args.output)
    )

    # Cheap end-of-job sanity check.
    n_rows = spark.read.parquet(args.output).count()
    print(f"[ocsf-spark] wrote {n_rows} OCSF event(s) to {args.output}",
          file=sys.stderr)
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
