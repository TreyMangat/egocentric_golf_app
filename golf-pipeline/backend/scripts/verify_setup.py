"""Preflight credential check for the backend.

Operator tool — not part of the test suite. Run before plugging in real
cloud credentials, then again after, to confirm each layer of the V1
pipeline is reachable.

Each check is independent: a failure in one does not abort the others.
Exit code is 0 only if every check passed.

Usage
-----
  cd backend
  python scripts/verify_setup.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from dotenv import load_dotenv
from rich.console import Console

console = Console()

REQUIRED_ENV_VARS = [
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "MONGO_URI",
    "TEMPORAL_TARGET",
]

# Substrings that mark an env var as sensitive (mask in the report).
_SECRET_SUBSTRINGS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "URI")


# ─── result type ──────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    skipped: bool = False  # skipped checks count as passing for exit code


def _run(name: str, fn: Callable[[], tuple[bool, str]]) -> CheckResult:
    """Wrap a check so a thrown exception is reported, not propagated."""
    try:
        passed, detail = fn()
    except Exception as e:  # noqa: BLE001 — every check should be independent
        tb = traceback.format_exc(limit=1).strip().splitlines()[-1]
        return CheckResult(name, False, f"{type(e).__name__}: {e} ({tb})")
    return CheckResult(name, passed, detail)


def _mask(name: str, value: str) -> str:
    if any(s in name.upper() for s in _SECRET_SUBSTRINGS):
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}...{value[-2:]}"
    return value


# ─── checks ───────────────────────────────────────────────────────────────────


def _check_env_var(name: str) -> Callable[[], tuple[bool, str]]:
    def fn() -> tuple[bool, str]:
        val = os.getenv(name, "").strip()
        if not val:
            return False, "missing or empty"
        return True, _mask(name, val)
    return fn


def _check_ffmpeg() -> tuple[bool, str]:
    if shutil.which("ffmpeg") is None:
        return False, "not found on PATH"
    proc = subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
        timeout=10,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[:1]
        return False, f"ffmpeg -version returned {proc.returncode}: {err}"
    first_line = proc.stdout.decode("utf-8", errors="replace").splitlines()[0]
    return True, first_line


def _check_aws_s3() -> tuple[bool, str]:
    bucket = os.getenv("S3_BUCKET", "").strip()
    if not bucket:
        return False, "S3_BUCKET unset; cannot test"
    region = os.getenv("AWS_REGION", "us-east-1")
    import boto3  # imported here so a missing dep doesn't take out the whole script

    s3 = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
    )
    resp = s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
    n = resp.get("KeyCount", 0)
    return True, f"bucket={bucket} reachable (KeyCount@HEAD={n})"


def _check_mongo() -> tuple[bool, str]:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        return False, "MONGO_URI unset; cannot test"
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError  # noqa: F401  (kept for the catch path)

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        dbs = client.list_database_names()
    finally:
        client.close()
    summary = ",".join(dbs[:5]) + ("..." if len(dbs) > 5 else "")
    return True, f"ping ok; databases=[{summary}]"


def _check_temporal() -> tuple[bool, str]:
    target = os.getenv("TEMPORAL_TARGET", "").strip()
    if not target:
        return False, "TEMPORAL_TARGET unset; cannot test"
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")

    async def aio() -> tuple[bool, str]:
        from temporalio.client import Client

        client = await asyncio.wait_for(
            Client.connect(target, namespace=namespace), timeout=5
        )
        # Connect succeeds locally even if the server is wrong because gRPC
        # is lazy — force a real RPC by iterating list_workflows once.
        async for _ in client.list_workflows("", page_size=1):
            break
        return True, f"connected to {target}, namespace={namespace}"

    return asyncio.run(aio())


def _check_modal() -> tuple[bool, str]:
    raw = os.getenv("LOCAL_DEV", "1").strip().lower()
    is_local = raw not in {"0", "false", "no", "off", ""}
    if is_local:
        return True, "skipped (LOCAL_DEV=true)"

    # Production-mode path: confirm modal has a usable token. Modal looks at
    # both env vars and ~/.modal.toml, so import-time config is the source
    # of truth, not just os.getenv.
    import modal  # noqa: F401  — side effect: parses ~/.modal.toml + env

    tok_id = (
        os.getenv("MODAL_TOKEN_ID")
        or getattr(getattr(modal, "config", None), "config", {}).get("token_id")
    )
    tok_secret = (
        os.getenv("MODAL_TOKEN_SECRET")
        or getattr(getattr(modal, "config", None), "config", {}).get("token_secret")
    )
    if not tok_id or not tok_secret:
        return False, (
            "no modal token (run `modal token new` or set "
            "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in .env)"
        )
    return True, f"token configured ({_mask('TOKEN', tok_id)})"


# ─── runner ───────────────────────────────────────────────────────────────────


def _print_result(r: CheckResult) -> None:
    if r.skipped:
        marker = "[yellow]\\[SKIP][/yellow]"
    elif r.passed:
        marker = "[green]\\[ OK ][/green]"
    else:
        marker = "[red]\\[FAIL][/red]"
    console.print(f"  {marker}  [bold]{r.name:<22}[/bold]  {r.detail}")


def main() -> None:
    load_dotenv()

    console.print("[bold]golf-pipeline backend setup verification[/bold]\n")

    results: list[CheckResult] = []

    console.print("[dim]Required env vars[/dim]")
    for var in REQUIRED_ENV_VARS:
        r = _run(var, _check_env_var(var))
        _print_result(r)
        results.append(r)

    console.print("\n[dim]External services[/dim]")
    for name, fn in [
        ("ffmpeg", _check_ffmpeg),
        ("AWS S3", _check_aws_s3),
        ("Mongo", _check_mongo),
        ("Temporal", _check_temporal),
        ("Modal", _check_modal),
    ]:
        r = _run(name, fn)
        # tag the modal "skipped" result so its marker prints as SKIP, not OK
        if name == "Modal" and "skipped" in r.detail:
            r.skipped = True
        _print_result(r)
        results.append(r)

    failed = [r for r in results if not r.passed]
    passed_or_skipped = len(results) - len(failed)
    console.print(
        f"\n[bold]Summary:[/bold] {passed_or_skipped}/{len(results)} ok"
        f"{f' ({len(failed)} failed)' if failed else ''}"
    )
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
