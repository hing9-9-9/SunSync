"""FR-01.4 Implementation Step B — live, key-safe verification for KMA's GK2A DSR point API.

Manual integration diagnostic only; it is not an API-path component or a scheduled worker.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ENDPOINT = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt"
ERROR = re.compile(
    r"error|fail|denied|invalid|unauthori|option\s+required|"
    r"\[\uc635\uc158\s*\ud544\uc694\]|\uc778\uc99d|\uc624\ub958|\uc790\ub8cc\s*\uc5c6",
    re.IGNORECASE,
)

# Local convenience only; an already supplied process environment takes priority.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: str
    received_at: datetime


@dataclass(frozen=True)
class DsrRow:
    observed_at: datetime
    dsr: str


def redact(value: str, key: str) -> str:
    return value.replace(key, "[REDACTED]")


def call_api(key: str, params: dict[str, str], latitude: str, longitude: str) -> ApiResponse:
    query = urlencode(
        {"varn": "DSR", "lat": latitude, "lon": longitude, **params, "authKey": key}
    )
    request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "SunSync-GK2A-DSR-verifier/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            return ApiResponse(response.status, redact(response.read().decode("utf-8", "replace"), key), datetime.now(UTC))
    except HTTPError as error:
        return ApiResponse(error.code, redact(error.read().decode("utf-8", "replace"), key), datetime.now(UTC))


def tokens(line: str) -> list[str]:
    return [item for item in re.split(r"[\s,|]+", line.strip().lstrip("#").strip()) if item]


def parse_dsr_rows(body: str) -> tuple[list[DsrRow], list[str]]:
    """Parse only data rows that match the API's TM/TIME + DSR table columns."""

    if ERROR.search(body):
        return [], []
    columns = None
    for line in body.splitlines():
        candidate = [item.upper() for item in tokens(line)]
        if "DSR" in candidate and ("TM" in candidate or "TIME" in candidate):
            columns = candidate
            break
    if columns is None:
        return [], ["No TM/TIME and DSR table header found; response was not counted as data."]
    time_index = columns.index("TM") if "TM" in columns else columns.index("TIME")
    dsr_index = columns.index("DSR")
    rows: list[DsrRow] = []
    ignored: list[str] = []
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = tokens(line)
        if len(fields) <= max(time_index, dsr_index) or not re.fullmatch(r"20\d{10}", fields[time_index]):
            continue
        try:
            stamp = datetime.strptime(fields[time_index], "%Y%m%d%H%M").replace(tzinfo=UTC)
        except ValueError:
            ignored.append(line.strip())
            continue
        rows.append(DsrRow(stamp, fields[dsr_index]))
    return rows, ignored


def intervals(values: list[datetime]) -> list[float]:
    return [round((values[i + 1] - values[i]).total_seconds() / 60, 3) for i in range(len(values) - 1)]


def report(label: str, response: ApiResponse, key: str) -> list[DsrRow]:
    rows, ignored = parse_dsr_rows(response.body)
    times = sorted({row.observed_at for row in rows})
    hint = next((line for line in response.body.splitlines() if ERROR.search(line)), None)
    print(f"\n[{label}]\nHTTP status: {response.status}\nParsed DSR data rows: {len(rows)}")
    print(f"Error hint: {redact(hint, key) if hint else 'none'}")
    print("Unit contract: DSR is surface downward shortwave radiation in W/m^2.")
    print(f"Observed timestamps (UTC): {', '.join(item.isoformat() for item in times) or 'none parsed'}")
    if times:
        print(f"Timestamp intervals (minutes): {intervals(times) or 'single observation'}")
    print("Parsed observations (UTC timestamp, DSR W/m^2; zero is retained as zero):")
    for row in rows[:20]:
        print(f"  {row.observed_at.isoformat()}  DSR={row.dsr}")
    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} additional rows omitted")
    for message in ignored[:3]:
        print(f"Ignored non-data row: {redact(message, key)}")
    return rows


def report_latest_delay(rows: list[DsrRow], received_at: datetime) -> None:
    if not rows:
        print("Latest availability delay: unavailable (no parseable UTC observation timestamp).")
        return
    latest = max(row.observed_at for row in rows)
    print(f"API response received (UTC): {received_at.isoformat()}")
    print(f"Latest available observation (UTC): {latest.isoformat()}")
    print(f"Observed-to-receipt delay: {(received_at - latest).total_seconds() / 60:.1f} minutes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tm1", default="202610260300", help="UTC start, yyyymmddHHMM")
    parser.add_argument("--tm2", default="202610260400", help="UTC end, yyyymmddHHMM")
    parser.add_argument("--lat", default="37.493762", help="site latitude in degrees")
    parser.add_argument("--lon", default="126.916751", help="site longitude in degrees")
    parser.add_argument(
        "--body-output",
        type=Path,
        help="Write complete API response bodies locally after auth-key redaction; never use for DB storage.",
    )
    args = parser.parse_args()
    key = os.environ.get("KMA_API_KEY")
    if not key:
        print("BLOCKED: KMA_API_KEY is not set in this process. No request was made.")
        return 2

    results: dict[str, list[DsrRow]] = {}
    bodies: list[tuple[str, str]] = []
    for interval in ("10", "2"):
        label = f"historical int={interval}"
        try:
            response = call_api(key, {"tm1": args.tm1, "tm2": args.tm2, "int": interval}, args.lat, args.lon)
            bodies.append((label, response.body))
            results[label] = report(label, response, key)
        except (URLError, TimeoutError, OSError) as error:
            print(f"\n[{label}]\nTransport failure: {type(error).__name__}: {error}")

    # The endpoint caps timestamp columns at 24; request a valid 3h50m window.
    latest_end = datetime.now(UTC)
    latest_start = latest_end - timedelta(hours=3, minutes=50)
    try:
        latest_response = call_api(key, {"tm1": latest_start.strftime("%Y%m%d%H%M"), "tm2": latest_end.strftime("%Y%m%d%H%M"), "int": "10"}, args.lat, args.lon)
        bodies.append(("latest int=10 (last 3h50m)", latest_response.body))
        latest_rows = report("latest int=10 (last 3h50m)", latest_response, key)
        report_latest_delay(latest_rows, latest_response.received_at)
    except (URLError, TimeoutError, OSError) as error:
        print(f"\n[latest int=10]\nTransport failure: {type(error).__name__}: {error}")

    int2_times = sorted({row.observed_at for row in results.get("historical int=2", [])})
    if len(int2_times) >= 2:
        result = "2-minute production observations present" if 2.0 in intervals(int2_times) else "no 2-minute production interval observed"
        print(f"\nint=2 conclusion: {result}.")
    else:
        print("\nint=2 conclusion: inconclusive (fewer than two parseable observations).")
    if args.body_output:
        rendered = "\n\n".join(f"[{label}]\n{body}" for label, body in bodies) + "\n"
        args.body_output.parent.mkdir(parents=True, exist_ok=True)
        args.body_output.write_text(rendered, encoding="utf-8")
        print(f"Redacted complete response bodies written to: {args.body_output}")
    print("Advection conclusion: this point endpoint returns nearest-grid values only; use a 2-D grid/NetCDF source for optical-flow advection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
