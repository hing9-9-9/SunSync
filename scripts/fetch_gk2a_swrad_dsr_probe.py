"""FR-01.4 Implementation Step C — download one GK2A L2 SWRAD-DSR NetCDF file for format inspection.

This is deliberately a manual probe, not a worker.  It neither imports the
nowcast package nor writes to SQLite.  Delete its local artifact after schema
inspection if the L2 product is not suitable for the production pipeline.
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

ENDPOINT = "https://apihub.kma.go.kr/api/typ01/url/sat_file_down2.php"
LIST_ENDPOINT = "https://apihub.kma.go.kr/api/typ01/url/sat_file_list.php"
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


def _netcdf_signature(payload: bytes) -> bool:
    # Classic NetCDF starts CDF; NetCDF4 is an HDF5 container.
    return payload.startswith((b"CDF", b"\x89HDF\r\n\x1a\n"))


def list_available_timestamps(*, api_key: str, date_utc: str) -> list[str]:
    """Return actual KO SWRAD-DSR filenames advertised for one UTC date."""

    query = urlencode(
        {
            "sat": "GK2A",
            "vars": "L2",
            "area": "KO",
            "fmt": "bin",
            "tm": date_utc,
            "size": "Y",
            "filter": "swrad-dsr",
            "authKey": api_key,
        }
    )
    request = Request(
        f"{LIST_ENDPOINT}?{query}", headers={"User-Agent": "SunSync-GK2A-SWRAD-probe/1.0"}
    )
    try:
        with urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            body = response.read().decode("utf-8", "replace")
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(type(error).__name__) from error
    return sorted(set(re.findall(r"gk2a_ami_le2_swrad-dsr_ko020lc_(20\d{10})\.nc", body, re.IGNORECASE)))


def download_one(
    *, api_key: str, timestamp: str, product: str, area: str, output: Path, error_output: Path
) -> int:
    """Download and minimally validate one KO 2 km SWRAD-DSR L2 binary file."""

    query = urlencode(
        {
            "typ": "bin",
            "lvl": "l2",
            "are": area,
            # API Hub's L2 download contract uses the product family code,
            # not the file-name component (which is ``swrad-dsr``).
            "dat": product,
            "tm": timestamp,
            "authKey": api_key,
        }
    )
    request = Request(
        f"{ENDPOINT}?{query}", headers={"User-Agent": "SunSync-GK2A-SWRAD-probe/1.0"}
    )
    try:
        with urlopen(request, timeout=60) as response:
            if response.status != 200:
                print(f"Download failed: HTTP {response.status}")
                return 1
            payload = response.read()
    except HTTPError as error:
        print(f"Download failed: HTTP {error.code}")
        return 1
    except (URLError, TimeoutError, OSError) as error:
        print(f"Download failed: {type(error).__name__}")
        return 1
    if not _netcdf_signature(payload):
        # Preserve a redacted diagnostic locally, never in SQLite or logs.
        error_output.parent.mkdir(parents=True, exist_ok=True)
        error_output.write_bytes(payload.replace(api_key.encode(), b"[REDACTED]"))
        print(
            "Download did not return a NetCDF/NetCDF4 signature; no NetCDF was written. "
            f"Redacted diagnostic: {error_output}"
        )
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    print(f"NetCDF probe saved: {output} ({len(payload)} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tm",
        default="202610170130",
        help="UTC observation timestamp, YYYYMMDDHHMM; default is the Seoul mock fixture time.",
    )
    parser.add_argument("--product", default="swrad", help="API Hub L2 dat product code")
    parser.add_argument("--area", default="ko", help="API Hub L2 area code")
    parser.add_argument(
        "--list-date",
        help="UTC date to list (YYYYMMDD); default derives it from --tm.",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Skip file-list discovery and request exactly --tm (diagnostic use only).",
    )
    parser.add_argument(
        "--error-output",
        type=Path,
        default=ROOT / "artifacts" / "gk2a_swrad_probe" / "last_download_error.txt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Local NetCDF destination; defaults to the selected timestamp under artifacts/.",
    )
    args = parser.parse_args()
    if len(args.tm) != 12 or not args.tm.isdigit():
        parser.error("--tm must be UTC YYYYMMDDHHMM")
    api_key = os.environ.get("KMA_API_KEY")
    if not api_key:
        print("BLOCKED: KMA_API_KEY is not set. No request was made.")
        return 2
    if args.direct:
        output = args.output or ROOT / "artifacts" / "gk2a_swrad_probe" / f"gk2a_swrad_dsr_ko_{args.tm}.nc"
        return download_one(
            api_key=api_key,
            timestamp=args.tm,
            product=args.product,
            area=args.area,
            output=output,
            error_output=args.error_output,
        )
    date_utc = args.list_date or args.tm[:8]
    if len(date_utc) != 8 or not date_utc.isdigit():
        parser.error("--list-date must be UTC YYYYMMDD")
    try:
        available = list_available_timestamps(api_key=api_key, date_utc=date_utc)
    except RuntimeError as error:
        print(f"File-list query failed: {error}")
        return 1
    if not available:
        print(f"No KO SWRAD-DSR NetCDF files were listed for UTC {date_utc}.")
        return 1
    selected = max((stamp for stamp in available if stamp <= args.tm), default=available[-1])
    print(f"Listed {len(available)} file(s); selected UTC {selected}.")
    output = args.output or ROOT / "artifacts" / "gk2a_swrad_probe" / f"gk2a_swrad_dsr_ko_{selected}.nc"
    return download_one(
        api_key=api_key,
        timestamp=selected,
        product=args.product,
        area=args.area,
        output=output,
        error_output=args.error_output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
