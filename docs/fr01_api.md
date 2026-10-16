# FR-01 Sunpath API

저장된 FR-06 `Scene`의 `ProtectedRegion.sample_points_m`에 대해 기하학적 직사광선 경로를 예측한다. 이 API는 결과를 저장하지 않으며, DNI·날씨·광학 투과율·FR-02 제어는 적용하지 않는다.

```bash
curl -sS -X POST http://localhost:8000/api/scenes/scene_demo_001/sunpath-predictions \
  -H 'Content-Type: application/json' \
  --data '{
    "start_time": "2026-10-14T12:00:00+09:00",
    "duration_minutes": 120,
    "interval_seconds": 300,
    "protected_region_ids": ["region_A"],
    "expected_scene_revision": 2
  }'
```

`start_time`은 timezone offset을 포함해야 한다. `protected_region_ids`를 생략하면 모든 보호 영역을 예측한다. 응답에는 UTC timestamp별 태양 위치, sample별 상태·차폐물·창 교점, region별 timeline과 최초 sampled exposure timestamp가 포함된다.

오류 코드는 Scene 미존재 시 404, `expected_scene_revision` 불일치 시 409, 잘못된 요청·알 수 없는 region·계산 불가능한 geometry·자원 제한 초과 시 422이다.
