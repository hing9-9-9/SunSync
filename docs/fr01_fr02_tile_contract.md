# FR-01 5 cm Protected-Region Tile Output Contract

## Scope

This document fixes FR-01 tile generation and the FR-01 → FR-02 handoff.
FR-01 creates the tiles itself and returns one sample-level result per tile
centre. It does not define FR-02's internal output or film-cell mapping
structure.

The Scene supplies a rectangular protected region, not a list of evaluation
points. FR-01 divides the region into 5 cm x 5 cm tiles. For example, a 60 cm
x 60 cm protected region has 12 x 12 = 144 tiles and 144 centre samples.

## Scene requirements

- `ProtectedRegion.vertices_m` contains exactly four ENU points in the order
  `p0`, `p1`, `p2`, `p3`.
- `p0 → p1` is the local U edge, `p0 → p3` is the local V edge, and
  `p2 = p1 + p3 - p0`.
- The U and V edges are non-zero and perpendicular; the winding agrees with
  `receiving_normal`.
- `sample_points_m` is not a Scene input.
- The prediction request and response retain the immutable `scene_id` and
  `scene_revision` that identify this geometry.

## Tile generation

FR-01 divides each local U and V edge into consecutive intervals of at most
0.05 m. If the final interval is shorter than 0.05 m, it remains one residual
tile; it is not discarded or expanded. The centre of every U/V interval pair
is one evaluation point.

For a 62 cm x 60 cm region, this produces 13 x 12 tiles. The final U tile is
2 cm wide and its centre is 61 cm from the U origin.

Sample ordering is deterministic: V rows increase first, and U columns
increase within each row. The zero-based position in that order is
`sample_index`.

## FR-01 output

FR-01 continues to return its normal prediction response. Each generated tile
centre appears as one `regions[].timeline[].samples[]` entry.

```json
{
  "scene_id": "scene_demo_001",
  "scene_revision": 2,
  "coordinate_frame": "ENU",
  "units": "m",
  "regions": [
    {
      "region_id": "region_A",
      "timeline": [
        {
          "timestamp": "2026-10-31T01:00:00Z",
          "samples": [
            {
              "sample_index": 17,
              "point_m": [0.35, 1.25, 0.80],
              "status": "exposed",
              "reason": "clear_path",
              "candidate_path": {
                "window_id": "south_window",
                "window_hit_m": [0.42, 0.00, 1.63]
              },
              "direct_irradiance_W_m2": 480.0
            }
          ]
        }
      ]
    }
  ]
}
```

`window_hit_m` is an ENU point in metres and is present only for a valid
`clear_path`. A non-exposed result, including `boundary_ambiguous`, has
`candidate_path: null`.

`sample_index`, `region_id`, and `timestamp` identify the generated source
tile. `direct_irradiance_W_m2` remains part of the normal FR-01 response.

## DNI and irradiance boundary

FR-01.4 first selects one final DNI independently for each timestamp: valid
`Scene.weather`, then a durable Open-Meteo snapshot, then pvlib clear-sky
fallback, otherwise `unknown`. This is the source-selection responsibility.

FR-01 converts that selected DNI into each exposed tile's
`direct_irradiance_W_m2` using the tile receiving normal and the sun direction.
FR-02 must use this surface value rather than raw DNI when evaluating an
intensity cap. A geometrically non-exposed tile is `0`; an exposed tile whose
DNI is unknown is `null`.

## FR-02 handoff input

FR-02 receives the normal FR-01 prediction response for the same immutable
`scene_id` and `scene_revision`. It creates a shading candidate only from a
sample entry with all of the following values:

- `status == "exposed"`
- `reason == "clear_path"`
- non-null `candidate_path.window_id`
- non-null `candidate_path.window_hit_m`

The FR-01 → FR-02 handoff record is therefore:

```json
{
  "scene_id": "scene_demo_001",
  "scene_revision": 2,
  "timestamp": "2026-10-31T01:00:00Z",
  "region_id": "region_A",
  "sample_index": 17,
  "point_m": [0.35, 1.25, 0.80],
  "window_id": "south_window",
  "window_hit_m": [0.42, 0.00, 1.63],
  "direct_irradiance_W_m2": 480.0
}
```

All coordinates are ENU metres. `point_m` identifies the generated
protected-region tile centre; `window_hit_m` is its valid window crossing.
`direct_irradiance_W_m2` is retained for downstream grade policy. A
non-exposed sample produces no handoff record.

## Out of scope

This contract does not cover window-local coordinates, point expansion,
film-cell selection or activation, protection grades, or shading schedules.
