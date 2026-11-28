# Documents

Documents are grouped by topic. Each feature folder (`fr01` to `fr06`) holds the design, the API and the data contract of that feature.

| Folder | Content |
| --- | --- |
| [`overview/`](overview/) | [Functional requirements](overview/features.md), [decisions](overview/decisions.md), [architecture](overview/architecture.md), [scenario precomputation](overview/scenario-precomputation.md), [design specification (2026-10-23, record)](overview/design-spec-2026-10-23.md) |
| [`ops/`](ops/) | [Deployment on one server](ops/deploy.md), [Google login](ops/auth.md), [database](ops/db.md), [phone and PC measurement session](ops/device-handoff.md), [film hub sync architecture](ops/hub-sync-architecture.md) |
| [`ui/`](ui/) | [UI flow](ui/ui-flow.md), [UI data integration](ui/ui-data-integration.md), [UI and FR-06 integration](ui/ui-fr06-integration.md), and two records: [award example](ui/award-integration.md), [hero image](ui/hero-asset.md) |
| [`fr01/`](fr01/) | Sunlight prediction: [pvlib design](fr01/pvlib_design.md), [API](fr01/api.md), [tile contract to FR-02](fr01/tile_contract.md), [KMA AI overview](fr01/kma_ai_overview.md), [Open-Meteo DNI](fr01/open_meteo_dni_design.md), [prediction errors](fr01/prediction_error_implementation.md), [large spaces](fr01/large_space_implementation.md), [mock test plan](fr01/mock_test_plan.md) |
| [`fr02/`](fr02/) | Shading plan: [design](fr02/design.md), [API](fr02/api.md), [plan format](fr02/schedule.md), [JSON schema](fr02/shading_plan_schema.json) |
| [`fr03/`](fr03/) | Measurement: [workflow](fr03/measurement_workflow.md), [single marker](fr03/single_marker.md), [robust estimation](fr03/robust_measurement.md), [selected film](fr03/selected_film.md), [Vision AI 3D estimation](fr03/vision_ai_3d_estimation.md), [YOLO and Depth Anything experiment](fr03/ai_experiment_YOLO_DA.md) |
| [`fr04/`](fr04/) | [Adaptive control and LG SGF](fr04/adaptive_control_lg_sgf.md) |
| [`fr05/`](fr05/) | [Virtual film protocol](fr05/protocol.md) (remote hub mode: [ops/hub-sync-architecture.md](ops/hub-sync-architecture.md)) |
| [`fr06/`](fr06/) | [Space data design](fr06/design.md), [JSON schema](fr06/scene_schema.json) |
| [`experiments/`](experiments/) | Results of the experiment scripts in `server/tools/ai_experiments/`, including the [FR-03 depthtest & Colab GPU benchmark](experiments/fr03_depthtest_experiment_report.md) |
| [`examples/`](examples/) | Sample Scene, plan and measurement files that the tests and tools read |
| [`reports/`](reports/) | Submitted course reports (PDF and LaTeX source) |
| [`ai_applications/`](ai_applications/) | [AI & Applications final blog](ai_applications/README.md), [IEEE PDF](ai_applications/SunSync_AI_Applications_Blog.pdf), and [final video](https://youtu.be/Fp4ip1Duu2s) |
| `images/` | Images used by the README |

Where to put a new document: a feature document goes into the folder of its FR number; deployment, login and database notes go into `ops/`; screen documents go into `ui/`. Do not put files directly into `docs/`.
