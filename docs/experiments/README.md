# Experiments

Results written by the tools in `tools/ai_experiments`. Each `.md` file is a report and the `.json` file next to it holds the numbers. Every file is overwritten when its tool runs again, so do not edit them by hand.

| Report | Tool | What it compares |
|---|---|---|
| [fr04_adaptive_control_experiment_benchmark.md](fr04_adaptive_control_experiment_benchmark.md) | `run_fr04_adaptive_control_experiment.py` | The FR-02 rule, FR-04 smoothing and FR-04 replace on synthetic irradiance |
| [fr04_real_weather_replay.md](fr04_real_weather_replay.md) | `run_fr04_real_weather_replay.py` | FR-02 and FR-04 on real KMA days (10-minute data) |
| [fr04_minute_replay.md](fr04_minute_replay.md) | `run_fr04_minute_replay.py` | FR-02 and FR-04 on measured 1-minute irradiance |

The submitted course reports are in [`docs/reports/`](../reports/). How FR-04 works and what these results mean: [FR-04 document](../fr04_adaptive_control_lg_sgf.md).
