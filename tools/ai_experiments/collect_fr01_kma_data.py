"""Backfill KMA GK2A DSR/cloud data into inspectable raw TXT and normalized CSV files."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.config import load_local_environment
from app.nowcast.kma_adapter import KmaGk2aAdapter
from app.nowcast.kma_collection_queue import (
    finalize_queue,
    prepare_queue,
    queue_summary,
    run_queue,
)

from engine.fr01.models import Site


def parse_timestamp(value: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp and normalize it to UTC."""

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset or Z")
    return parsed.astimezone(UTC)


def main() -> int:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--start", type=parse_timestamp, required=True)
    parser.add_argument("--end", type=parse_timestamp, required=True)
    parser.add_argument("--interval-minutes", type=int, choices=[10], default=10)
    parser.add_argument("--variables", nargs="+", choices=["DSR", "CA"], default=["DSR", "CA"])
    parser.add_argument("--output-dir", type=Path, default=WORKSPACE_ROOT / "data" / "fr01")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-concurrency", type=int, default=3)
    parser.add_argument(
        "--resume", action="store_true", help="resume an existing durable queue"
    )
    parser.add_argument(
        "--retry-failed", action="store_true", help="move failed queue tasks back to pending"
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="create or inspect the queue without making KMA requests",
    )
    args = parser.parse_args()

    if set(args.variables) != {"DSR", "CA"}:
        parser.error("this collector requires both DSR and CA")
    site = Site(args.latitude, args.longitude, "UTC")
    queue_path = prepare_queue(
        site=site,
        start_time=args.start,
        end_time=args.end,
        output_root=args.output_dir,
        resume=args.resume,
    )
    if args.prepare_only:
        print(f"queue={queue_path} summary={queue_summary(queue_path)}")
        return 0

    api_key = os.getenv("KMA_API_KEY", "")
    if not api_key.strip():
        parser.error("KMA_API_KEY is missing; set it in the repository .env")

    adapter = KmaGk2aAdapter(
        site,
        api_key,
        timeout_seconds=args.timeout_seconds,
        max_attempts=args.max_attempts,
    )
    summary = run_queue(
        queue_path=queue_path,
        output_root=args.output_dir,
        adapter=adapter,
        max_concurrency=args.max_concurrency,
        retry_failed=args.retry_failed,
    )
    print(f"queue={queue_path} summary={summary}")
    if summary["succeeded"] != summary["total"]:
        print("queue incomplete; rerun with --resume --retry-failed after reviewing failures")
        return 2

    paths = finalize_queue(queue_path=queue_path, output_root=args.output_dir, site=site)
    print(
        " ".join(
            [
                f"raw_dsr_files={len(paths.dsr_raw)}",
                f"raw_ca_files={len(paths.cloud_raw)}",
                f"raw_dir={paths.dsr_raw[0].parent}",
                f"csv={paths.normalized_csv}",
                f"manifest={paths.manifest}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
