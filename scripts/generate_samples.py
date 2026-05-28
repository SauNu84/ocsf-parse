#!/usr/bin/env python3
"""Regenerate the samples/ directory with ~100 events per source.

Deterministic (fixed random seed). Run from the repo root:

    python scripts/generate_samples.py

Edit ``COUNT`` to change the events-per-source target.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

random.seed(20260527)

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "samples"
COUNT = 100  # events per source


# `secrets.token_*` is non-deterministic (uses os.urandom). To keep this
# generator reproducible from the seed alone, we substitute random-backed
# replacements for ID-shaped strings.
_HEX = "0123456789abcdef"
_URLSAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def token_hex(n_bytes: int) -> str:
    """Drop-in for ``token_hex``; uses the seeded ``random`` module."""
    return "".join(random.choices(_HEX, k=n_bytes * 2))


def token_urlsafe(n_bytes: int) -> str:
    """Drop-in for ``token_urlsafe``."""
    return "".join(random.choices(_URLSAFE, k=n_bytes))


# ---------------------------------------------------------------------------
# shared pools
# ---------------------------------------------------------------------------


USERS_OK = ["alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi"]
USERS_BAD = ["root", "admin", "ubuntu", "ec2-user", "oracle", "test", "guest", "postgres"]
HOSTS = ["web-01", "web-02", "api-01", "db-01", "bastion", "edge-01"]
IPS_INT = ["10.0.1.42", "10.0.1.99", "10.0.2.55", "10.0.2.110", "10.0.3.15", "10.0.3.200", "192.168.4.17"]
IPS_EXT = ["203.0.113.7", "203.0.113.42", "203.0.113.50", "198.51.100.7", "198.51.100.42",
           "192.0.2.1", "192.0.2.55", "198.51.100.150", "203.0.113.99"]
COUNTRIES = ["us", "de", "fr", "jp", "br", "in", "ru", "cn", "gb"]
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.123 Safari/537.36",
    "curl/8.4.0",
    "python-requests/2.31.0",
    "Wget/1.20.3 (linux-gnu)",
    "Go-http-client/1.1",
    "Mozilla/5.0 (compatible; bot/1.0)",
]
URI_OK = ["/", "/index.html", "/api/v1/users", "/api/v1/users/42", "/api/v1/products",
         "/dashboard", "/static/css/main.css", "/static/js/app.js", "/health", "/favicon.ico"]
URI_4XX = ["/wp-login.php", "/admin/.env", "/.git/config", "/server-status", "/phpmyadmin/",
           "/api/v1/internal/keys", "/.aws/credentials"]
REFERERS = ["https://example.com/", "https://search.example.com/", "https://app.example.com/dashboard",
            "https://google.com/", "-"]
HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]


def pick(seq):
    return random.choice(seq)


def ip_external():
    return pick(IPS_EXT)


def ip_internal():
    return pick(IPS_INT)


# ---------------------------------------------------------------------------
# generators (one per source)
# ---------------------------------------------------------------------------


def _http_status(weight_ok: float = 0.8) -> int:
    if random.random() < weight_ok:
        return pick([200, 200, 200, 201, 204, 301, 302, 304])
    return pick([400, 401, 403, 404, 404, 405, 429, 500, 502, 503])


def _http_uri(status: int) -> str:
    if status >= 400 and random.random() < 0.4:
        return pick(URI_4XX)
    return pick(URI_OK)


def _nginx_or_apache_line(ts: datetime, vendor: str) -> str:
    ip = pick(IPS_EXT + IPS_INT)
    user = pick(USERS_OK + ["-", "-", "-"])  # weight to "-"
    method = pick(HTTP_METHODS)
    status = _http_status()
    uri = _http_uri(status)
    bytes_sent = random.randint(80, 8192)
    ts_str = ts.strftime("%d/%b/%Y:%H:%M:%S %z")
    proto = pick(["HTTP/1.1", "HTTP/1.1", "HTTP/2.0", "HTTP/1.0"])
    referer = pick(REFERERS)
    ua = pick(USER_AGENTS)
    return f'{ip} - {user} [{ts_str}] "{method} {uri} {proto}" {status} {bytes_sent} "{referer}" "{ua}"'


def gen_nginx() -> list[str]:
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    return [_nginx_or_apache_line(base + timedelta(seconds=i * 3), "nginx") for i in range(COUNT)]


def gen_apache() -> list[str]:
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    return [_nginx_or_apache_line(base + timedelta(seconds=i * 4), "apache") for i in range(COUNT)]


def gen_sshd() -> list[str]:
    out: list[str] = []
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    pid = 12000
    # Generate a mix; ~10% are "Invalid user" lines (regex won't match → linter skips).
    for i in range(COUNT):
        ts = (base + timedelta(seconds=i * 7)).strftime("%b %d %H:%M:%S")
        host = pick(HOSTS)
        pid += 1
        roll = random.random()
        if roll < 0.5:
            user = pick(USERS_OK)
            outcome, method = "Accepted", pick(["publickey", "password"])
            ip = pick(IPS_INT)
            port = random.randint(40000, 65000)
            tail = ": RSA SHA256:" + token_urlsafe(20) if method == "publickey" else ""
            out.append(f"{ts} {host} sshd[{pid}]: {outcome} {method} for {user} from {ip} port {port} ssh2{tail}")
        elif roll < 0.9:
            user = pick(USERS_BAD + USERS_OK)
            outcome, method = "Failed", pick(["password", "publickey", "none"])
            ip = pick(IPS_EXT)
            port = random.randint(40000, 65000)
            out.append(f"{ts} {host} sshd[{pid}]: {outcome} {method} for {user} from {ip} port {port} ssh2")
        else:
            # Invalid user: skipped by the regex on purpose.
            user = pick(USERS_BAD)
            ip = pick(IPS_EXT)
            out.append(f"{ts} {host} sshd[{pid}]: Invalid user {user} from {ip}")
    return out


def gen_vpc_flow_logs() -> list[str]:
    out: list[str] = []
    base_epoch = 1716818000
    for i in range(COUNT):
        start = base_epoch + i * 12
        end = start + random.randint(1, 60)
        src, dst = (pick(IPS_INT), pick(IPS_EXT)) if random.random() < 0.7 else (pick(IPS_EXT), pick(IPS_INT))
        srcport = random.randint(1024, 65000)
        dstport = pick([22, 53, 80, 443, 443, 443, 3306, 5432, 8080])
        proto = pick([6, 6, 6, 17, 1])  # mostly TCP
        pkts = random.randint(1, 100)
        bytes_ = pkts * random.randint(60, 1500)
        action = pick(["ACCEPT", "ACCEPT", "ACCEPT", "REJECT"])
        out.append(
            f"2 123456789012 eni-0a1b2c3d4e5f {src} {dst} {srcport} {dstport} {proto} "
            f"{pkts} {bytes_} {start} {end} {action} OK"
        )
    return out


def gen_cloudflare() -> list[str]:
    out: list[str] = []
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    for i in range(COUNT):
        ts = (base + timedelta(seconds=i * 2)).isoformat().replace("+00:00", "Z")
        status = _http_status(weight_ok=0.75)
        method = pick(HTTP_METHODS)
        path = _http_uri(status)
        waf = pick(["allow", "allow", "allow", "allow", "block", "challenge"])
        if status >= 400 and random.random() < 0.4:
            waf = "block"
        ev = {
            "ClientIP": pick(IPS_EXT),
            "ClientCountry": pick(COUNTRIES),
            "ClientRequestHost": pick(["example.com", "api.example.com", "www.example.com"]),
            "ClientRequestMethod": method,
            "ClientRequestPath": path,
            "ClientRequestURI": path + (f"?id={random.randint(1, 9999)}" if random.random() < 0.3 else ""),
            "ClientRequestUserAgent": pick(USER_AGENTS),
            "ClientRequestReferer": pick(REFERERS),
            "EdgeResponseStatus": status,
            "EdgeResponseBytes": random.randint(0, 16384),
            "EdgeStartTimestamp": ts,
            "EdgeEndTimestamp": ts,
            "RayID": token_hex(8),
            "WAFAction": waf,
        }
        out.append(json.dumps(ev))
    return out


def gen_cloudtrail() -> list[str]:
    out: list[str] = []
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    api_calls = [
        ("s3.amazonaws.com",       "GetObject",       "Read"),
        ("s3.amazonaws.com",       "PutObject",       "Create"),
        ("s3.amazonaws.com",       "DeleteObject",    "Delete"),
        ("ec2.amazonaws.com",      "RunInstances",    "Create"),
        ("ec2.amazonaws.com",      "DescribeInstances","Read"),
        ("ec2.amazonaws.com",      "StopInstances",   "Delete"),
        ("iam.amazonaws.com",      "CreateUser",      "Create"),
        ("iam.amazonaws.com",      "AttachRolePolicy","Update"),
        ("iam.amazonaws.com",      "ListUsers",       "Read"),
        ("sts.amazonaws.com",      "AssumeRole",      "Create"),
        ("kms.amazonaws.com",      "Decrypt",         "Read"),
        ("logs.amazonaws.com",     "PutLogEvents",    "Create"),
    ]
    for i in range(COUNT):
        ts = (base + timedelta(seconds=i * 5)).isoformat().replace("+00:00", "Z")
        # ~10% ConsoleLogin events to exercise the routing.
        if random.random() < 0.1:
            ev = {
                "eventVersion": "1.08",
                "eventTime": ts,
                "eventSource": "signin.amazonaws.com",
                "eventName": "ConsoleLogin",
                "awsRegion": pick(["us-east-1", "us-west-2", "eu-west-1"]),
                "sourceIPAddress": pick(IPS_EXT),
                "userIdentity": {
                    "type": "IAMUser",
                    "principalId": "AIDA" + token_hex(8).upper(),
                    "arn": f"arn:aws:iam::123456789012:user/{pick(USERS_OK)}",
                    "accountId": "123456789012",
                    "userName": pick(USERS_OK),
                },
                "responseElements": {"ConsoleLogin": pick(["Success", "Success", "Failure"])},
                "additionalEventData": {"MFAUsed": pick(["Yes", "No"])},
                "eventID": token_hex(16),
                "recipientAccountId": "123456789012",
            }
        else:
            src, name, _ = pick(api_calls)
            failed = random.random() < 0.05
            ev = {
                "eventVersion": "1.08",
                "eventTime": ts,
                "eventSource": src,
                "eventName": name,
                "awsRegion": pick(["us-east-1", "us-west-2", "eu-west-1"]),
                "sourceIPAddress": pick(IPS_EXT + IPS_INT),
                "userIdentity": {
                    "type": pick(["IAMUser", "AssumedRole"]),
                    "principalId": "AIDA" + token_hex(8).upper(),
                    "arn": f"arn:aws:iam::123456789012:user/{pick(USERS_OK)}",
                    "accountId": "123456789012",
                    "userName": pick(USERS_OK),
                },
                "requestParameters": {"bucketName": "example-bucket"} if "s3" in src else {},
                "responseElements": None if failed else {"ok": True},
                "eventID": token_hex(16),
                "recipientAccountId": "123456789012",
            }
            # ~40% of API events include a `resources[]` array. Multi-resource
            # events exercise the for_each op in the mapping.
            if random.random() < 0.4:
                n = random.choice([1, 1, 2, 3])
                rtype = {"s3.amazonaws.com": "AWS::S3::Object",
                         "ec2.amazonaws.com": "AWS::EC2::Instance",
                         "iam.amazonaws.com": "AWS::IAM::Role",
                         "sts.amazonaws.com": "AWS::IAM::Role",
                         "kms.amazonaws.com": "AWS::KMS::Key",
                         "logs.amazonaws.com": "AWS::Logs::LogGroup"}.get(src, "AWS::Resource")
                ev["resources"] = [
                    {
                        "ARN": f"arn:aws:{src.split('.')[0]}::{ev['userIdentity']['accountId']}:resource/{token_hex(6)}",
                        "accountId": ev["userIdentity"]["accountId"],
                        "type": rtype,
                    }
                    for _ in range(n)
                ]
            if failed:
                ev["errorCode"] = pick(["AccessDenied", "NoSuchBucket", "Throttling"])
                ev["errorMessage"] = "request failed"
        out.append(json.dumps(ev))
    return out


def gen_okta() -> list[str]:
    out: list[str] = []
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    for i in range(COUNT):
        ts = (base + timedelta(seconds=i * 6)).isoformat().replace("+00:00", "Z")
        event_type = pick([
            "user.session.start",
            "user.session.start",
            "application.user_membership.add",
            "application.user_membership.remove",
        ])
        actor_id = "00u" + token_hex(8).lower()
        outcome = pick(["SUCCESS", "SUCCESS", "SUCCESS", "FAILURE"])
        ev = {
            "uuid": token_hex(16),
            "published": ts,
            "eventType": event_type,
            "severity": pick(["INFO", "INFO", "WARN", "ERROR"]),
            "displayMessage": {
                "user.session.start": "User login to Okta",
                "application.user_membership.add": "Add user to application membership",
                "application.user_membership.remove": "Remove user from application membership",
            }[event_type],
            "actor": {
                "id": actor_id,
                "type": "User",
                "alternateId": pick(USERS_OK) + "@example.com",
                "displayName": pick(USERS_OK).capitalize() + " Example",
            },
            "client": {
                "ipAddress": pick(IPS_EXT),
                "userAgent": {"rawUserAgent": pick(USER_AGENTS)},
            },
            "outcome": {"result": outcome, "reason": None if outcome == "SUCCESS" else "INVALID_CREDENTIALS"},
            "authenticationContext": {"externalSessionId": token_hex(16)},
            "target": [
                {
                    "id": "0oa" + token_hex(8).lower(),
                    "type": "AppInstance",
                    "displayName": pick(["Slack", "GitHub", "Salesforce", "Datadog"]),
                }
            ],
        }
        out.append(json.dumps(ev))
    return out


def gen_palo_alto() -> list[str]:
    out: list[str] = []
    base = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    for i in range(COUNT):
        ts = (base + timedelta(seconds=i * 4)).strftime("%Y/%m/%d %H:%M:%S")
        serial = "01234567890"
        src, dst = (pick(IPS_INT), pick(IPS_EXT)) if random.random() < 0.6 else (pick(IPS_EXT), pick(IPS_INT))
        srcport = random.randint(1024, 65000)
        dstport = pick([22, 53, 80, 443, 443, 443, 8080, 8443])
        proto = pick(["tcp", "tcp", "tcp", "udp"])
        action = pick(["allow", "allow", "allow", "deny", "drop"])
        application = pick(["web-browsing", "ssl", "dns", "ssh", "ms-rdp", "smtp", "unknown-tcp"])
        bytes_tot = random.randint(100, 50000)
        bytes_sent = random.randint(50, bytes_tot)
        bytes_recv = bytes_tot - bytes_sent
        subtype = pick(["start", "end", "drop", "deny"])
        out.append(
            f"1,{ts},{serial},TRAFFIC,{subtype},{src},{dst},trust,untrust,"
            f"{srcport},{dstport},{proto},{action},{application},"
            f"{bytes_tot},{bytes_sent},{bytes_recv}"
        )
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


GENERATORS: dict[str, tuple[str, Callable[[], list[str]]]] = {
    "nginx":         ("nginx.log",          gen_nginx),
    "apache":        ("apache.log",         gen_apache),
    "sshd":          ("sshd.log",           gen_sshd),
    "vpc_flow_logs": ("vpc_flow_logs.log",  gen_vpc_flow_logs),
    "cloudflare":    ("cloudflare.jsonl",   gen_cloudflare),
    "cloudtrail":    ("cloudtrail.jsonl",   gen_cloudtrail),
    "okta":          ("okta.jsonl",         gen_okta),
    "palo_alto":     ("palo_alto.log",      gen_palo_alto),
}


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    for name, (filename, fn) in GENERATORS.items():
        lines = fn()
        out_path = SAMPLES / filename
        out_path.write_text("\n".join(lines) + "\n")
        print(f"{name:<14} → {filename:<22} {len(lines):>4} lines")


if __name__ == "__main__":
    main()
