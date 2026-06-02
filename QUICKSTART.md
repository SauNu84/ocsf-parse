# Quickstart — first 5 minutes

This is the "I just heard about this, what does it actually do" walkthrough.
No Python toolchain needed — uses the Docker image at every step.

> If you'd rather work from source / a Python venv, jump to the bottom of
> [`README.md`](./README.md#install) for those install paths.

## 30 seconds — see the catalog

```bash
docker run --rm ghcr.io/saunu84/ocsf-mapper:0.3.0 list
```

This prints a table of every reference mapping shipped with the image —
38 sources across 8 OCSF categories, from Windows Event Log through
CloudTrail to ASTM drone telemetry. No login, no signup, no setup.

Output (truncated):

```
NAME                    DISPLAY                           VENDOR              PRIORITY   PARSER   CLASSES
------------------------------------------------------------------------------------------------------------------------------
apache                  Apache access log                 Apache              high       regex    http_activity
auditd_file             Linux auditd (file)               Linux Audit         critical   json     file_activity
aws_config              AWS Config                        AWS                 high       json     config_state
azure_ad_signin         Azure AD Sign-in                  Microsoft           critical   json     authentication
cef_generic             CEF (Common Event Format)         Various             high       cef      detection_finding
cloudflare              Cloudflare Logpush                Cloudflare          high       json     http_activity
...
```

## 1 minute — map your first log

You have a log file. You want OCSF events. Pick the closest reference
mapping from `list` above, mount your log into the container:

```bash
# Pretend you have an nginx access log.
docker run --rm -v $(pwd):/data ghcr.io/saunu84/ocsf-mapper:0.3.0 \
    apply mappings/nginx.json /data/access.log
```

You'll see one OCSF event per matched line, JSON-per-line on stdout:

```json
{"metadata": {"version": "1.9.0-dev", "product": {"name": "nginx", "vendor_name": "nginx"}, "logged_time": 1779891791000}, "time": 1779891791000, "category_uid": 4, "category_name": "Network Activity", "class_uid": 4002, "class_name": "HTTP Activity", "activity_id": 3, "activity_name": "Get", "type_uid": 400203, "severity_id": 1, ...}
```

Pipe to `jq` to read it:

```bash
docker run --rm -v $(pwd):/data ghcr.io/saunu84/ocsf-mapper:0.3.0 \
    apply mappings/nginx.json /data/access.log | jq .
```

## 2 minutes — drop a sample into the web UI

The interactive flow is the most useful if you're evaluating the tool
for the first time. Run it:

```bash
docker run --rm -p 8000:8000 ghcr.io/saunu84/ocsf-mapper:0.3.0
open http://127.0.0.1:8000
```

You'll land on a card grid of every mapping. Click any card → drop your
log file → see the mapped OCSF events side-by-side with the raw input,
plus per-event validation status. The "Mapping" tab shows the JSON DSL
config you'd edit to customize the mapping; "Coverage" shows how many
of the OCSF class's required + recommended fields the mapping
populates.

## 3 minutes — generate a new mapping with an LLM

If none of the 38 reference mappings fits your log, the LLM-assisted
generator can draft one. Bring your own Anthropic or OpenAI key:

```bash
# Persistent: drop a draft into your local mappings/ directory.
mkdir -p mappings samples
cp /path/to/your/log.jsonl samples/my_source.jsonl

docker run --rm \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    -v $(pwd)/mappings:/app/mappings \
    -v $(pwd)/samples:/app/samples \
    ghcr.io/saunu84/ocsf-mapper:0.3.0 \
    generate my_source samples/my_source.jsonl mappings/my_source.json
```

The generator does two LLM calls (one to pick OCSF classes, one to
draft the full mapping) and writes a JSON config you can review.
Re-run `apply` against the new mapping to see the output:

```bash
docker run --rm \
    -v $(pwd)/mappings:/app/mappings \
    -v $(pwd)/samples:/app/samples \
    ghcr.io/saunu84/ocsf-mapper:0.3.0 \
    apply mappings/my_source.json samples/my_source.jsonl | jq .
```

The web UI's `/new` page wraps this same flow visually, with a Monaco
editor for reviewing the draft before saving.

## 5 minutes — process a real volume

The single-process `apply` does ~5-12K events/sec depending on the
source (see [`BENCHMARKS.md`](./BENCHMARKS.md)). For backfills past
~1 GB you want the worker pool:

```bash
# 8 workers, output to Security Lake-compatible partitioned Parquet.
docker run --rm \
    -v $(pwd):/data \
    ghcr.io/saunu84/ocsf-mapper:0.3.0 \
    apply mappings/cloudtrail.json /data/cloudtrail.jsonl /data/lake/ \
    --workers 8 \
    --sink security-lake
```

Output layout matches AWS Security Lake's custom-source ingest
expectations:

```
lake/
  3002/eventDay=20260530/part-w00-00000.parquet
  3002/eventDay=20260530/part-w01-00000.parquet
  ...
  6003/eventDay=20260530/part-w03-00000.parquet
```

For >10 TB workloads the right answer is a distributed runtime —
see [`DESIGN_DISTRIBUTED.md`](./DESIGN_DISTRIBUTED.md) for the
Spark / Flink / Vector adapter blueprint.

## What's next

You've now seen the four things the tool does:

1. **Browse** the catalog of reference mappings (`list`)
2. **Apply** any mapping to your logs (`apply`)
3. **Draft** a new mapping for a new source (`generate`)
4. **Scale** for real-volume workloads (`apply --workers --sink security-lake`)

Other useful commands you'll meet next:

```bash
ocsf-mapper validate <events.jsonl> <class>   # OCSF schema validation
ocsf-mapper lint                              # CI gate: every mapping × its pinned sample
ocsf-mapper benchmark <mapping> <sample>      # per-phase throughput
ocsf-mapper diff <a.json> <b.json>            # side-by-side mapping diff
ocsf-mapper schema-diff                       # detect breaking OCSF schema changes
ocsf-mapper replay <historical> <new> <out>   # backfill new fields without re-ingesting raw
ocsf-mapper tail <mapping> <file>             # tail -f live log → OCSF
ocsf-mapper serve                             # web UI
```

Full reference: [`README.md`](./README.md). Architecture and design
decisions: [`PLAN.md`](./PLAN.md). Per-version changes: [`CHANGELOG.md`](./CHANGELOG.md).
