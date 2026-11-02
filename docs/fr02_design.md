# FR-02 Design: How the Plan Is Built

Result format: [fr02_schedule.md](fr02_schedule.md). Request: [fr02_api.md](fr02_api.md).

## Terms

| Term | Meaning |
|---|---|
| tile | A 5 cm piece of a protected surface that FR-01 traces a sun ray to. |
| cell | One square of the window's film grid. It is switched as a whole. |
| need | One cell must be in one state for a stretch of time because of one protected area. |
| piece | A part of the time line of one cell where the active needs do not change. |
| segment | A piece as written into the plan (`ShadingPlan`). |
| step | One time step of FR-01 (`interval_seconds`). |

## How the decisions are built

FR-01 looks at fixed time steps. When a cell is needed at step `t`, it is shaded from `t - interval` to
`t + interval`, cut at the period of the plan, because the exposure may have started or ended anywhere
between the neighbouring steps. Steps that follow each other become one segment.

- **Grade A:** every cell that a direct-sun path reaches is shaded, whatever the intensity. If the film
  cannot block fully (its minimum transmittance is above zero) the need cannot be met and is `infeasible`.
- **Grades B and C:** for one cell, one area and one step, the strongest tile intensity is compared with the
  cap of the grade. At or below the cap nothing is needed. Above it, the cell is `opaque` when the darkest
  film brings the intensity to the cap or below, and `infeasible` when it cannot. When an intensity is
  unknown the need is `unknown` (never zero), unless a known tile is already above the cap.
- **Several areas in one cell:** the most severe state wins (`infeasible`, then `opaque`, then `unknown`),
  because a darker cell never hurts another area's need. Every active area is listed as a reason, strictest
  grade first. The segment is split where the state or the reasons change.

## Known limits

- An exposure that starts and ends between two steps is not seen. A smaller interval reduces this.
- A tile that FR-01 cannot decide (the crossing lies exactly on a window edge or a wall corner, reason `boundary_ambiguous`) has no crossing point and is skipped.
- The intensity is compared with the cap as FR-01 gives it, without glass or film. The glass and film
  transmittances in a Scene are assumed values, not measurements.
- The default time step is 300 seconds, as in FR-01. The `max_time_step_s` of the Scene policy (10 seconds in the example) is not enforced.
- The film is assumed to have two states, opaque and transparent. Dimming would add an `opacity` field
  (0 to 1) to the segments and raise `plan_version`.

## Open points

- How FR-04 and FR-05 treat `infeasible` and `unknown` cells is still to be agreed with them.
