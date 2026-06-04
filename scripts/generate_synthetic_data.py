"""Generate ~10 GB of synthetic OCSF events from the 43 reference mappings.

Strategy
--------
1. For each mapping, apply it once to the pinned sample to derive a
   set of OCSF *template* events (one per output class).
2. Amplify each template by varying ``time``, ``metadata.uid``,
   ``actor.user.name`` / ``user.name``, ``src_endpoint.ip``,
   ``cloud.account.uid`` across a 30-day window. Realistic-ish
   entropy from synthetic pools.
3. Inject 5 incident scenarios (brute force, GuardDuty chain, Vault
   permission spike, lateral movement, MFA fraud cluster) at known
   times so the exploration notebook can find them.
4. Write Hive-partitioned Parquet to ``data/synthetic/ocsf/``:
   ``class_uid=NNNN/event_day=YYYY-MM-DD/part-XXXX.parquet``.

Output layout matches what pyarrow.dataset.read can auto-discover —
the notebook just does ``ds.dataset(root, partitioning="hive")``.

Usage
-----
    python3 scripts/generate_synthetic_data.py [--target-gb 10] [--out data/synthetic/ocsf]

Deterministic up to ``--seed`` (default 42). Re-running with the same
seed reproduces the same dataset.
"""

from __future__ import annotations

import argparse
import copy
import json
import multiprocessing as mp
import os
import random
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ocsf_mapper.apply import apply_stream_with_class  # noqa: E402
from ocsf_mapper.registry import list_mappings        # noqa: E402


# ---------------------------------------------------------------------------
# entropy pools
# ---------------------------------------------------------------------------

USER_POOL = [
    # Realistic-looking corp users; mix of email + DOMAIN\user shapes.
    *[f"{name}@corp.example.com" for name in (
        "alice","bob","carol","dave","eve","frank","grace","henry",
        "irene","jack","kelly","leo","maya","nora","oscar","peter",
        "quinn","rachel","sam","tina","ursula","vince","wendy","xavier",
        "yara","zach","sales.lead","oncall-checkout","oncall-dba",
        "audit-ro","backup-svc","deploy-svc","app-svc","contractor",
        "intern1","intern2","ceo","cfo","cto","ciso","security-analyst",
        "ir-team","ops-admin","dev-lead",
    )],
    *[f"CORP\\{u}" for u in (
        "alice","bob","carol","dave","eve","jdoe","rdpuser","testdev",
    )],
]

# Synthetic IP pools — different "personas" so geo/threat-intel features
# in the notebook can find structure.
IP_POOL_CORP     = [f"10.0.{a}.{b}" for a in range(0, 5) for b in range(1, 50)]
IP_POOL_VPN      = [f"192.0.2.{n}" for n in range(1, 80)]
IP_POOL_CLOUD    = [f"203.0.113.{n}" for n in range(1, 100)]
IP_POOL_PARTNER  = [f"198.51.100.{n}" for n in range(1, 100)]
IP_POOL_BAD      = [
    # Known-bad ranges (Tor exits, scanner heavy, abusers).
    *[f"185.220.101.{n}" for n in range(1, 30)],
    *[f"45.155.205.{n}"  for n in range(1, 20)],
    *[f"194.32.122.{n}"  for n in range(1, 20)],
]
IP_POOL = (IP_POOL_CORP * 8) + (IP_POOL_VPN * 4) + (IP_POOL_CLOUD * 2) + IP_POOL_PARTNER + IP_POOL_BAD

AWS_ACCOUNTS = ["111122223333", "444455556666", "777788889999"]
AWS_REGIONS  = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-2"]


# ---------------------------------------------------------------------------
# per-mapping amplification targets — chosen so the distribution looks
# like a mid-size SOC's relative ingest volume, not uniform.
# ---------------------------------------------------------------------------

EVENT_COUNTS_PER_MAPPING = {
    # HTTP-heavy (web logs dominate real SIEMs)
    "nginx":              1_800_000,
    "apache":             1_200_000,
    "cloudflare":         1_500_000,
    "waf_logs":             400_000,
    # Cloud API activity
    "cloudtrail":         2_000_000,
    "aws_config":           200_000,
    "k8s_audit":            800_000,
    "github_audit":         300_000,
    "gitlab_audit":         200_000,
    "hashicorp_vault":      400_000,
    "google_workspace":     150_000,
    # Network non-HTTP
    "vpc_flow_logs":      1_400_000,
    "palo_alto":            500_000,
    "zeek_dns":             600_000,
    "m365_email":           200_000,
    # Authentication
    "okta":                 600_000,
    "duo_security":         300_000,
    "azure_ad_signin":      400_000,
    "sshd":                 350_000,
    "windows_event_log":    500_000,
    "slack_audit":          150_000,
    # System activity / EDR
    "sysmon_process":     1_000_000,
    "auditd_file":          700_000,
    "falco_kernel":         400_000,
    "windows_registry":     300_000,
    "cron":                  80_000,
    "dlp_events":           100_000,
    # Detection findings (sparse — alerts should be rare)
    "crowdstrike_falcon":    25_000,
    "microsoft_defender":    30_000,
    "aws_guardduty":         20_000,
    "wazuh":                 40_000,
    "suricata_alert":       150_000,
    "splunk_es_alert":       30_000,
    "ueba_alert":            15_000,
    "cef_generic":           80_000,
    "leef_generic":          40_000,
    "qualys_scan":           10_000,
    # Inventory / discovery (snapshot-shaped, low rate)
    "osquery_inventory":     20_000,
    "jamf_inventory":        10_000,
    "prisma_cloud":          15_000,
    # Remediation / niche
    "soar_remediation":      20_000,
    "pagerduty":             40_000,
    "drone_telemetry":        5_000,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def _day_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _set_path(obj: dict, path: str, value: Any) -> None:
    """Mutate ``obj`` so that the dotted ``path`` (e.g. 'a.b.c') is set
    to ``value``, but only if every intermediate key already exists.
    No-op for paths that don't already exist in ``obj`` — keeps the
    amplifier from inventing new fields the template never had."""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
        if not isinstance(cur, dict):
            return
    if isinstance(cur, dict) and parts[-1] in cur:
        cur[parts[-1]] = value


def _vary_event(template: dict, ts_ms: int, rng: random.Random) -> dict:
    """Produce one amplified OCSF event from a template.

    Mutates a deep copy so the template stays clean for the next call.
    Only fields that exist in the template are varied — we don't invent
    new fields the original mapping didn't populate.
    """
    e = copy.deepcopy(template)
    user = rng.choice(USER_POOL)
    ip   = rng.choice(IP_POOL)

    e["time"] = ts_ms
    _set_path(e, "metadata.uid", str(uuid.uuid4()))
    _set_path(e, "metadata.logged_time", ts_ms)

    # User-name slots across the various OCSF places it can live.
    for path in ("user.name", "actor.user.name"):
        _set_path(e, path, user)

    # Endpoint IPs.
    for path in ("src_endpoint.ip", "mfa_device.ip"):
        _set_path(e, path, ip)

    # Cloud-side variation for AWS-like events.
    _set_path(e, "cloud.account.uid", rng.choice(AWS_ACCOUNTS))
    _set_path(e, "cloud.region",      rng.choice(AWS_REGIONS))

    return e


# ---------------------------------------------------------------------------
# anomaly scenarios — hand-crafted incidents the notebook can find
# ---------------------------------------------------------------------------


def _make_brute_force_scenario(templates: dict[str, dict], base: datetime) -> list[dict]:
    """Day 14, 12:00 UTC: a 2-hour brute-force burst against carol@corp."""
    out: list[dict] = []
    if "sshd" not in templates:
        return out
    when = base + timedelta(days=14, hours=12)
    bad_ip = "185.220.101.42"
    target = "carol@corp.example.com"
    # 200 failed sshd attempts in the burst.
    for i in range(200):
        e = copy.deepcopy(templates["sshd"])
        ts = _ms(when + timedelta(seconds=i * 30))
        e["time"] = ts
        _set_path(e, "metadata.uid", str(uuid.uuid4()))
        _set_path(e, "metadata.logged_time", ts)
        _set_path(e, "user.name", target)
        _set_path(e, "actor.user.name", target)
        _set_path(e, "src_endpoint.ip", bad_ip)
        # Force failure where the mapping has a status_id field.
        if "status_id" in e:
            e["status_id"] = 2
        if "status" in e:
            e["status"] = "Failure"
        out.append(e)
    # 1 successful login at the end (the compromise).
    e = copy.deepcopy(templates["sshd"])
    ts = _ms(when + timedelta(hours=2))
    e["time"] = ts
    _set_path(e, "metadata.uid", str(uuid.uuid4()))
    _set_path(e, "metadata.logged_time", ts)
    _set_path(e, "user.name", target)
    _set_path(e, "actor.user.name", target)
    _set_path(e, "src_endpoint.ip", bad_ip)
    if "status_id" in e:
        e["status_id"] = 1
    if "status" in e:
        e["status"] = "Success"
    out.append(e)
    return out


def _make_guardduty_chain(templates: dict[str, dict], base: datetime) -> list[dict]:
    """Day 20: GuardDuty critical → PagerDuty triggered → ack → resolved."""
    out: list[dict] = []
    when = base + timedelta(days=20, hours=9)
    if "aws_guardduty" in templates:
        e = copy.deepcopy(templates["aws_guardduty"])
        ts = _ms(when)
        e["time"] = ts
        _set_path(e, "metadata.uid", "synthetic-gd-critical-day20")
        _set_path(e, "metadata.logged_time", ts)
        if "severity_id" in e: e["severity_id"] = 5
        if "severity" in e:     e["severity"] = "Critical"
        if "finding_info" in e and isinstance(e["finding_info"], dict):
            e["finding_info"]["title"] = "UnauthorizedAccess:IAMUser/MaliciousIPCaller.Custom"
            e["finding_info"]["desc"] = "API call from known-malicious IP 185.220.101.42 (synthetic scenario)."
        out.append(e)
    if "pagerduty" in templates:
        for label, off, status, status_id in (
            ("triggered",    0,    "Other",   0),
            ("acknowledged", 10,   "Other",   0),
            ("resolved",     120,  "Success", 1),
        ):
            e = copy.deepcopy(templates["pagerduty"])
            ts = _ms(when + timedelta(minutes=off))
            e["time"] = ts
            _set_path(e, "metadata.uid", f"synthetic-pd-{label}-day20")
            _set_path(e, "metadata.logged_time", ts)
            _set_path(e, "message", "GuardDuty critical: malicious IP API call")
            if "status_id" in e: e["status_id"] = status_id
            if "status" in e:    e["status"]    = status
            out.append(e)
    return out


def _make_vault_denied_spike(templates: dict[str, dict], base: datetime) -> list[dict]:
    """Day 10, 14:00 UTC: rogue-svc account hammers Vault, all denied."""
    out: list[dict] = []
    if "hashicorp_vault" not in templates:
        return out
    when = base + timedelta(days=10, hours=14)
    rogue_user = "rogue-svc-account@corp.example.com"
    bad_ip = "45.155.205.7"
    secret_paths = [
        "secret/data/prod/db", "secret/data/prod/api-keys/stripe",
        "secret/data/personal/alice", "secret/data/prod/jwt-signing",
        "secret/data/legacy/old-creds", "sys/policies/acl/admin",
    ]
    for i in range(50):
        e = copy.deepcopy(templates["hashicorp_vault"])
        ts = _ms(when + timedelta(seconds=i * 20))
        e["time"] = ts
        _set_path(e, "metadata.uid", str(uuid.uuid4()))
        _set_path(e, "metadata.logged_time", ts)
        _set_path(e, "actor.user.name", rogue_user)
        _set_path(e, "src_endpoint.ip", bad_ip)
        if "status_detail" in e:
            e["status_detail"] = "1 error occurred: permission denied"
        if "api" in e and isinstance(e["api"], dict):
            req = e["api"].get("request")
            if isinstance(req, dict):
                req["data"] = secret_paths[i % len(secret_paths)]
            if "response" in e["api"] and isinstance(e["api"]["response"], dict):
                e["api"]["response"]["error"] = "permission denied"
        out.append(e)
    return out


def _make_lateral_movement(templates: dict[str, dict], base: datetime) -> list[dict]:
    """Day 5: one principal calls APIs across 5 regions within 10 minutes."""
    out: list[dict] = []
    if "cloudtrail" not in templates:
        return out
    when = base + timedelta(days=5, hours=23)  # off-hours
    rogue_user = "compromised-svc@corp.example.com"
    api_calls = [
        ("us-east-1", "AssumeRole"),
        ("us-west-2", "ListBuckets"),
        ("eu-west-1", "DescribeInstances"),
        ("eu-central-1", "GetSecretValue"),
        ("ap-southeast-2", "GetCallerIdentity"),
    ]
    for i, (region, op) in enumerate(api_calls):
        e = copy.deepcopy(templates["cloudtrail"])
        ts = _ms(when + timedelta(minutes=i * 2))
        e["time"] = ts
        _set_path(e, "metadata.uid", f"synthetic-lm-day5-{i}")
        _set_path(e, "metadata.logged_time", ts)
        _set_path(e, "actor.user.name", rogue_user)
        _set_path(e, "cloud.region", region)
        if "api" in e and isinstance(e["api"], dict):
            e["api"]["operation"] = op
        out.append(e)
    return out


def _make_mfa_fraud_cluster(templates: dict[str, dict], base: datetime) -> list[dict]:
    """Day 25: 8 Duo 'fraud' responses from 5 users in one window."""
    out: list[dict] = []
    if "duo_security" not in templates:
        return out
    when = base + timedelta(days=25, hours=3)  # 3 AM — classic
    targeted = [
        "alice@corp.example.com","bob@corp.example.com","ceo@corp.example.com",
        "cfo@corp.example.com","cto@corp.example.com",
    ]
    for i, user in enumerate([*targeted, "alice@corp.example.com","ceo@corp.example.com","cfo@corp.example.com"]):
        e = copy.deepcopy(templates["duo_security"])
        ts = _ms(when + timedelta(minutes=i * 8))
        e["time"] = ts
        _set_path(e, "metadata.uid", str(uuid.uuid4()))
        _set_path(e, "metadata.logged_time", ts)
        _set_path(e, "user.name", user)
        _set_path(e, "actor.user.name", user)
        _set_path(e, "src_endpoint.ip", f"185.220.101.{50 + i}")
        if "severity_id" in e: e["severity_id"] = 5
        if "severity" in e:    e["severity"] = "Critical"
        if "status_id" in e:   e["status_id"] = 2
        if "status" in e:      e["status"] = "Failure"
        if "status_detail" in e: e["status_detail"] = "fraud"
        out.append(e)
    return out


SCENARIOS = [
    ("brute_force_burst",     _make_brute_force_scenario),
    ("guardduty_chain",       _make_guardduty_chain),
    ("vault_denied_spike",    _make_vault_denied_spike),
    ("lateral_movement",      _make_lateral_movement),
    ("mfa_fraud_cluster",     _make_mfa_fraud_cluster),
]


# ---------------------------------------------------------------------------
# generation pipeline
# ---------------------------------------------------------------------------


def _templates_for(mapping_name: str, mapping_path: Path, sample_path: Path) -> list[dict]:
    """Apply ``mapping_name`` to its pinned sample and return one template
    event per output class. We pick the *first* event of each class — the
    fields are stable enough across the sample for amplification."""
    cfg = json.loads(mapping_path.read_text())
    lines = sample_path.read_text().splitlines()
    by_class: dict[str, dict] = {}
    for event, cls in apply_stream_with_class(cfg, lines):
        if cls not in by_class:
            by_class[cls] = event
    return list(by_class.values())


def _iter_amplified(template: dict, n: int, base_ms: int, span_days: int,
                    rng: random.Random) -> Iterator[dict]:
    """Yield ``n`` amplified events from ``template`` with timestamps
    uniformly spread across the ``span_days``-day window."""
    span_ms = span_days * 86_400_000
    for _ in range(n):
        ts = base_ms + rng.randrange(span_ms)
        yield _vary_event(template, ts, rng)


def _generate_for_class(
    mapping_name: str,
    cls_name: str,
    cls_uid: int,
    template: dict,
    target: int,
    base_ms: int,
    days: int,
    out_root_str: str,
    chunk: int,
    seed: int,
) -> tuple[str, str, int, int]:
    """Worker: amplify ``template`` to ``target`` events and stream-write
    Parquet partitions under ``out_root_str``. Returns
    ``(mapping_name, cls_name, cls_uid, written)`` for the parent's tally.
    """
    rng = random.Random(seed)
    out_root = Path(out_root_str)
    written = 0
    buf: list[dict] = []
    for evt in _iter_amplified(template, target, base_ms, days, rng):
        buf.append(evt)
        if len(buf) >= chunk:
            _write_per_day(out_root, cls_uid, buf)
            written += len(buf)
            buf = []
    if buf:
        _write_per_day(out_root, cls_uid, buf)
        written += len(buf)
    return (mapping_name, cls_name, cls_uid, written)


def _write_per_day(out_root: Path, class_uid: int, events: list[dict]) -> None:
    """Group events by event_day, write one part file per day partition.

    Uses unique filenames per call so re-running appends rather than
    overwriting. Coerces OCSF integer fields to int64 explicitly so
    pyarrow.dataset can merge schemas across part files without
    "int32 vs int64" errors (Python int width varies; pyarrow infers
    the narrowest type per chunk which differs across writes).
    """
    by_day: dict[str, list[dict]] = {}
    for e in events:
        d = _day_str(e["time"])
        by_day.setdefault(d, []).append(e)
    for day, day_events in by_day.items():
        part_dir = out_root / f"class_uid={class_uid}" / f"event_day={day}"
        part_dir.mkdir(parents=True, exist_ok=True)
        # Unique-per-call part file so multiple invocations append cleanly.
        part_name = f"part-{uuid.uuid4().hex[:12]}.parquet"
        table = pa.Table.from_pylist(day_events)
        # Force all int columns to int64 so cross-file merges don't fail
        # on (int32, int64) field mismatches.
        casts = {}
        for fld in table.schema:
            if pa.types.is_integer(fld.type) and fld.type != pa.int64():
                casts[fld.name] = pa.int64()
        if casts:
            new_fields = [
                pa.field(f.name, casts.get(f.name, f.type)) for f in table.schema
            ]
            table = table.cast(pa.schema(new_fields))
        pq.write_table(table, part_dir / part_name, compression="snappy")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic/ocsf",
                    help="Output Parquet root (will be created).")
    ap.add_argument("--days", type=int, default=30,
                    help="Spread events across this many days ending today.")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="Multiplier on the per-mapping event counts. "
                         "Use scale=0.1 for a quick smoke test.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--chunk", type=int, default=200_000,
                    help="Per-write chunk size (rows in memory at once).")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    # 30-day window ending at start of today (UTC).
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    base = end - timedelta(days=args.days)
    base_ms = _ms(base)

    mappings = {m["name"]: m for m in list_mappings(REPO / "mappings")}
    print(f"discovered {len(mappings)} mappings; out={out_root}; days={args.days}; "
          f"scale={args.scale}; seed={args.seed}")

    # Collect templates first so the scenario injectors have what they need.
    print("\nBuilding OCSF templates (one apply per mapping)...")
    templates_by_mapping: dict[str, dict[str, dict]] = {}
    for name, m in mappings.items():
        sample = m.get("sample")
        if not sample:
            continue
        try:
            templates_by_mapping[name] = {
                e.get("class_name", f"unknown_{i}"): e
                for i, e in enumerate(_templates_for(name, Path(m["path"]), Path(sample)))
            }
        except Exception as ex:
            print(f"  ! {name}: template build failed: {ex!r}")
            continue
        print(f"  ✓ {name:<22} {len(templates_by_mapping[name])} class(es)")

    # ----- main amplification loop --------------------------------------
    # Per-mapping work is independent → fan out across processes for the
    # ~6× speedup that lets us actually hit 10 GB in minutes, not hours.
    print("\nGenerating amplified events (multiprocess)...")
    t0 = time.time()
    total_events = 0

    jobs = []
    for name, templates in templates_by_mapping.items():
        target = int(EVENT_COUNTS_PER_MAPPING.get(name, 50_000) * args.scale)
        if target <= 0:
            continue
        per_class = max(1, target // max(1, len(templates)))
        for cls_name, template in templates.items():
            cls_uid = template.get("class_uid")
            if cls_uid is None:
                continue
            jobs.append((
                name, cls_name, int(cls_uid), template, per_class,
                base_ms, args.days, str(out_root), args.chunk,
                args.seed ^ hash((name, cls_name)) & 0xFFFFFFFF,
            ))

    n_workers = max(2, (os.cpu_count() or 4) - 1)
    print(f"  fanning out {len(jobs)} (mapping × class) jobs across {n_workers} workers")
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(_generate_for_class, *j) for j in jobs]
        for fut in as_completed(futures):
            name, cls_name, cls_uid, written = fut.result()
            total_events += written
            print(f"  {name:<22} cls={cls_name:<28} class_uid={cls_uid:<6} wrote {written:>10,}")

    # ----- inject scenarios ---------------------------------------------
    print("\nInjecting 5 incident scenarios...")
    # Build a flat lookup: mapping name → template event (just pick the first
    # class's template per mapping — enough for the scenario hand-crafters).
    flat_templates = {
        name: next(iter(tpls.values()))
        for name, tpls in templates_by_mapping.items()
        if tpls
    }
    for sc_name, sc_fn in SCENARIOS:
        events = sc_fn(flat_templates, base)
        if not events:
            print(f"  · {sc_name}: 0 events (mapping unavailable)")
            continue
        # Group by class_uid for writing.
        by_cls: dict[int, list[dict]] = {}
        for e in events:
            by_cls.setdefault(int(e["class_uid"]), []).append(e)
        for cls_uid, evts in by_cls.items():
            _write_per_day(out_root, cls_uid, evts)
        total_events += len(events)
        print(f"  ✓ {sc_name:<25} {len(events)} event(s) across "
              f"{len(by_cls)} class partition(s)")

    elapsed = time.time() - t0
    print(f"\nDone. {total_events:,} events in {elapsed:.1f}s "
          f"({total_events/elapsed:,.0f} events/sec).")

    # Disk usage summary.
    total_bytes = 0
    n_files = 0
    for f in out_root.rglob("*.parquet"):
        total_bytes += f.stat().st_size
        n_files += 1
    print(f"On disk: {total_bytes/1024/1024/1024:.2f} GB across {n_files} part files "
          f"({total_bytes/total_events:.0f} bytes/event avg).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
