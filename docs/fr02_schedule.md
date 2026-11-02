# FR-02 Shading Plan (`ShadingPlan`)

FR-02 decides which film cells of a window are opaque and when. The result is one `ShadingPlan` per
Scene (one room). FR-04 smooths it and FR-05 applies it to the film, so all three read the same format.

- Model: `backend/app/schemas/shading_plan.py` (Pydantic, `plan_version` `0.1.0`, a draft until FR-04 and FR-05 use it)
- JSON Schema: `docs/fr02_shading_plan_schema.json` (generated from the model)
- Examples: `docs/examples/fr02/shading_plan_grade_a.json`, `docs/examples/fr02/shading_plan_infeasible_unknown.json`

Regenerate the schema after changing the model:

```bash
PYTHONPATH=backend python -m app.schemas.shading_plan
```

## Structure

```
ShadingPlan
├ plan_version      "0.1.0"
├ scene_id          the Scene (room) the plan was computed for
├ scene_revision    the revision of that Scene
├ start_time, end_time   the period the plan covers
└ windows[]
   ├ window_id, film_rows, film_cols
   └ cells[]            row, col
      └ segments[]       start_time, end_time, state
         └ reasons[]       region_id, grade
```

| Field | Meaning |
|---|---|
| `scene_id`, `scene_revision` | Which Scene and which revision of it the plan belongs to. A plan computed for another revision is out of date. |
| `start_time`, `end_time` | ISO 8601 timestamps with a time zone offset. |
| `window_id` | A window of the Scene. A room can have several windows. |
| `film_rows`, `film_cols` | Size of the film grid of that window. For a vertical window, row 0 is the top row and column 0 the left column when looking at the window from inside. |
| `row`, `col` | One film cell. |
| `state` | What the cell does during the segment (see below). |
| `reasons` | The protected areas (`region_id`) and their grades (`A` to `D`) that caused the state. |

## States

| `state` | Meaning |
|---|---|
| `opaque` | The cell is switched to opaque. |
| `transparent` | The cell stays transparent. Only needed to carry an explanation; reasons may be empty. |
| `infeasible` | The cell is needed, but the film cannot meet the grade's limit even when it is as dark as possible. |
| `unknown` | The intensity is unknown, so the cell cannot be decided. It is not treated as zero. |

`opaque`, `infeasible` and `unknown` need at least one reason.

## How to read a plan

- A window or cell that is not listed is **transparent for the whole plan**.
- Inside a listed cell, any time that no segment covers is transparent.
- A segment covers `[start_time, end_time)`: the start is included and the end is not.
- The segments of one cell are in time order and do not overlap. When the reasons change, the segment is split.
- The `reasons` of a segment list every protected area that affected the cell during that segment.
- The tile number (`sample_index`) of FR-01 is not part of the plan; FR-02 keeps it internally only.

## What the model rejects

Unknown fields (`extra="forbid"`), values of the wrong type or a time without an offset (`strict=True`), and:

1. a segment whose end is not after its start,
2. segments of one cell that are out of order or overlap,
3. a cell outside the window's film grid,
4. the same cell or the same window twice,
5. a segment outside the period of the plan,
6. an `opaque`, `infeasible` or `unknown` segment without a reason.

How the plan is built: [fr02_design.md](fr02_design.md). How to request it: [fr02_api.md](fr02_api.md).
