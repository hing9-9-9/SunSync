"""FR-01.4 Implementation Steps C/E — compare GK2A L2 SWRAD file-list responses without downloading data.

Manual API-contract diagnostic only; it is not an API-path component or a scheduled worker.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://apihub.kma.go.kr/api/typ01/url/sat_file_list.php"
PRODUCT_PATTERN = re.compile(r"gk2a_ami_le2_swrad(?:-[a-z]+)?_[a-z0-9]+_\d{12}\.nc", re.IGNORECASE)
load_dotenv(ROOT / ".env", override=False)


def query(*, api_key: str, date_utc: str, area: str, product: str, name_filter: str) -> tuple[int, str]:
    params = {
        "sat": "GK2A",
        "vars": product,
        "area": area,
        "fmt": "bin",
        "tm": date_utc,
        "size": "N",
        "authKey": api_key,
    }
    if name_filter:
        params["filter"] = name_filter
    request = Request(
        f"{ENDPOINT}?{urlencode(params)}", headers={"User-Agent": "SunSync-GK2A-file-list-probe/1.0"}
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (URLError, TimeoutError, OSError) as error:
        return 0, type(error).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", action="append", default=[], help="UTC YYYYMMDDHH; repeatable")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "gk2a_swrad_probe" / "file_list_matrix.txt",
    )
    args = parser.parse_args()
    dates = args.date or ["2026101701", "2026102802"]
    if any(len(date) != 10 or not date.isdigit() for date in dates):
        parser.error("--date must be UTC YYYYMMDDHH")
    api_key = os.environ.get("KMA_API_KEY")
    if not api_key:
        print("BLOCKED: KMA_API_KEY is not set. No request was made.")
        return 2

    captures: list[str] = []
    for date in dates:
        for area in ("KO", "ko", "EA", "ea", "FD", "fd"):
            for product in ("L2", "l2", "SWRAD", "swrad", "SWRAD-DSR", "swrad-dsr", "swrad_dsr"):
                # The empty filter distinguishes a nonexistent product directory
                # from a filter-string mismatch.  It is still one hour only.
                name_filter = "" if product.lower() in {"swrad-dsr", "swrad_dsr"} else "swrad-dsr"
                status, body = query(
                    api_key=api_key,
                    date_utc=date,
                    area=area,
                    product=product,
                    name_filter=name_filter,
                )
                names = sorted(set(PRODUCT_PATTERN.findall(body)))
                print(
                    f"date={date} area={area} vars={product} filter={name_filter} "
                    f"status={status} swrad_files={len(names)}"
                )
                captures.append(
                    f"[date={date} area={area} vars={product} filter={name_filter} status={status}]\n"
                    + body.replace(api_key, "[REDACTED]")
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n\n".join(captures) + "\n", encoding="utf-8")
    print(f"Redacted file-list responses written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
