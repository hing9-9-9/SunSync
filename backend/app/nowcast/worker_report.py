"""Shared log line and exit code of the collection workers."""

from collections import Counter
from collections.abc import Mapping


def report_line(result: Mapping[str, int], failures: Counter[str]) -> str:
    """Counts plus, when something failed, how often each reason occurred.

    Reasons are fixed words (never a response body, a URL or a key).
    """
    line = " ".join(f"{key}={value}" for key, value in result.items())
    if failures:
        line += " reasons=" + ",".join(
            f"{reason}:{count}" for reason, count in sorted(failures.items())
        )
    return line


def exit_code(result: Mapping[str, int]) -> int:
    """Fail the run only when it tried and stored nothing.

    A run that stored some sites and failed on others (for example a site the provider does not
    cover) is reported in the log but does not mark the service as failed.
    """
    return 1 if result["failed"] > 0 and result["stored"] == 0 else 0
