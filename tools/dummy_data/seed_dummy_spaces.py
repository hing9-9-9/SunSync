"""Seed dummy spaces for checking the 3D view, FR-01 and FR-02 (draft).

Uses only the standard library. Scenario data lives in scenarios.json next to this file.

  python seed_dummy_spaces.py --dry-run
  python seed_dummy_spaces.py --base-url http://127.0.0.1:8000 --suffix v1
  python seed_dummy_spaces.py --suffix v1 --check            # also run FR-01 and compare with expectations
  python seed_dummy_spaces.py --suffix v1 --only S1,S3 --check
  python seed_dummy_spaces.py --suffix v1 --delete           # remove (the server remembers deleted IDs)

Deployed server: put the Caddy basic-auth login in the environment, never in this file or the repository.

  SUNSYNC_USER=... SUNSYNC_PASSWORD=... python seed_dummy_spaces.py --base-url https://HOST --allow-remote --suffix v1

After the Google login ships (#105, #106) every request needs a signed-in account; only
auth_headers() and the owner handling below have to change, not scenarios.json.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BUILDING = "[더미]"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def auth_headers() -> dict[str, str]:
    """Basic auth for the deployed server (Caddy). Replace when the account login exists."""
    user, password = os.environ.get("SUNSYNC_USER"), os.environ.get("SUNSYNC_PASSWORD")
    if not user or not password:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def call(base: str, method: str, path: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **auth_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None), len(raw)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw), len(raw)
        except ValueError:
            return error.code, raw.decode("utf-8", "replace")[:200], len(raw)


def load(only: set[str] | None):
    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    return [s for s in scenarios if not only or s["key"] in only]


def make_space(scenario: dict, suffix: str) -> dict:
    space = json.loads(json.dumps(scenario["space"]))
    space["id"] = f"{space['id']}-{suffix}"
    space["building"] = BUILDING
    return space


def utc_offset(timezone: str, date: str) -> str:
    local = datetime.fromisoformat(date + "T12:00:00").replace(tzinfo=ZoneInfo(timezone))
    minutes = int(local.utcoffset().total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def check(base: str, space: dict, scene_id: str, revision: int, date: str, expect: str) -> str:
    """Same request as the web app: a whole local day in 30 minute steps."""
    body = {
        "start_time": f"{date}T00:00:00{utc_offset(space['site']['timezone'], date)}",
        "duration_minutes": 1440,
        "interval_seconds": 1800,
        "expected_scene_revision": revision,
    }
    status, result, size = call(base, "POST", f"/api/scenes/{scene_id}/sunpath-predictions", body)
    if status != 200:
        actual = f"error_{status}"
    else:
        exposed = any(r["first_predicted_exposure_time"] for r in result["regions"])
        actual = "exposure" if exposed else "none"
    mark = "OK  " if actual == expect else "DIFF"
    extra = f"{size / 1e6:.1f} MB" if status == 200 else str(result)[:80]
    return f"    {mark} {date}  expected {expect:<10} actual {actual:<10} {extra}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--suffix", default="v1", help="appended to every space id (deleted ids cannot be reused)")
    parser.add_argument("--only", help="comma separated scenario keys, e.g. S1,S3")
    parser.add_argument("--check", action="store_true", help="run FR-01 for each scenario and compare with expectations")
    parser.add_argument("--delete", action="store_true", help="delete the spaces instead of creating them")
    parser.add_argument("--dry-run", action="store_true", help="validate the files and list what would be sent")
    parser.add_argument("--allow-remote", action="store_true", help="required for any address that is not this machine")
    parser.add_argument(
        "--skip-heavy",
        action="store_true",
        help="skip the compute-heavy S6a large-space scenario",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    host = urllib.parse.urlparse(base).hostname or ""
    if host not in LOCAL_HOSTS and not args.allow_remote:
        print(f"Refusing {base}: not a local address. Pass --allow-remote to write to a shared server.")
        return 2
    only = {k.strip() for k in args.only.split(",")} if args.only else None
    scenarios = [s for s in load(only) if not (args.skip_heavy and s["key"] == "S6a")]
    if not scenarios:
        print("No scenario selected.")
        return 2

    differences = failures = 0
    for scenario in scenarios:
        space = make_space(scenario, args.suffix)
        label = f"{scenario['key']:<4} {space['id']:<40} {scenario['description']}"
        if args.dry_run:
            print("DRY ", label)
            continue
        if args.delete:
            status, _, _ = call(base, "DELETE", f"/api/ui/spaces/{space['id']}")
            print(f"{'DEL ' if status == 204 else 'FAIL'} {status} {label}")
            failures += status != 204
            continue
        status, saved, _ = call(base, "POST", "/api/ui/spaces", space)
        if status == 201:
            print(f"NEW  201 {label}")
        elif status == 409:
            print(f"SKIP 409 {label}  (exists or was deleted: use another --suffix)")
            continue
        else:
            print(f"FAIL {status} {label}\n     {str(saved)[:200]}")
            failures += 1
            continue
        if args.check:
            scene_id, revision = saved["serverSceneId"], saved["serverRevision"]
            for item in scenario["checks"]:
                line = check(base, space, scene_id, revision, item["date"], item["expect"])
                print(line)
                differences += "DIFF" in line
    if args.check:
        print(f"\n{differences} difference(s) from the expectations in scenarios.json")
    return 1 if failures or differences else 0


if __name__ == "__main__":
    sys.exit(main())
