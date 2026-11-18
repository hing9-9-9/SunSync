"""FR-04 through the application: a short break in the sun is smoothed away, a long one is not.

The sky is controlled (600 W/m2 with one break to 0) and goes through the whole chain: Scene
weather, the FR-01 prediction, the FR-02 plan and the smoothed FR-04 plan, all computed by the
application's own API. The unit tests of FR-04 work on ready-made opacity series; this test checks
that the rules act on the plans the application really builds, and at which steps they can.

FR-02 shades a needed step from one step before to one step after, so a break of one step is
bridged by FR-02 itself. A break of five 1-minute steps leaves a gap of four steps (4 minutes) in
the FR-02 plan, which is shorter than the 10-minute limit of FR-04.1 and is filled.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.ai_experiments import run_fr04_real_weather_replay as replay

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"
SITE = replay.Site(37.5714, 126.9658, "Asia/Seoul")
# 15:00 to 16:00 Korean time: the afternoon sun reaches the protected regions of the example Scene.
START = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
MINUTES = 60
BREAK_FROM = 25  # minute of the window at which the sky breaks


def sky(break_minutes: int) -> list[dict]:
    records = []
    for index in range(MINUTES + 1):
        moment = START + timedelta(minutes=index)
        dark = BREAK_FROM <= index < BREAK_FROM + break_minutes
        records.append(
            {
                "valid_time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval_start": None,
                "interval_end": None,
                "dni_W_m2": 0.0 if dark else 600.0,
            }
        )
    return records


@pytest.fixture(scope="module")
def run():
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    with replay._client() as client:
        counter = {"n": 0}

        def go(break_minutes: int, step_seconds: int) -> dict:
            counter["n"] += 1
            return replay.plan_cases(
                client,
                template,
                f"flicker_{counter['n']}",
                SITE,
                sky(break_minutes),
                START,
                START + timedelta(minutes=MINUTES),
                [step_seconds],
            )[0]

        yield go


def test_a_steady_sky_needs_no_smoothing(run):
    case = run(0, 60)
    assert case["fr02_switches"] > 0
    assert case["fr04_switches"] == case["fr02_switches"]


def test_a_break_shorter_than_the_gap_limit_is_smoothed_back_to_the_steady_plan(run):
    steady = run(0, 60)
    short = run(5, 60)
    assert short["fr02_switches"] > steady["fr02_switches"]  # the break splits many exposures
    assert short["fr04_switches"] == steady["fr04_switches"]  # FR-04 closes every one of them
    assert short["never_brighter_than_fr02"]
    assert short["fr04_above_cap_q50"] == short["fr04_above_cap_q90"] == 0


def test_a_break_longer_than_the_gap_limit_is_not_closed(run):
    steady = run(0, 60)
    # 14 steps remain as a gap after FR-02 widens each step: more than the 10-minute limit.
    long_break = run(15, 60)
    assert long_break["fr04_switches"] > steady["fr04_switches"]
    assert long_break["fr04_switches"] <= long_break["fr02_switches"]
    assert long_break["never_brighter_than_fr02"]


def test_at_the_screens_step_a_short_break_is_invisible_and_there_is_nothing_to_smooth(run):
    # The break lies between two 30-minute steps, so the plan never sees it; rules of 10 minutes
    # are shorter than one step and cannot act at this step in any case.
    steady = run(0, 1800)
    short = run(5, 1800)
    assert short["fr02_switches"] == steady["fr02_switches"]
    assert short["fr04_switches"] == short["fr02_switches"]
