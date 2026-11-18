# FR-03 & FR-06 Vision AI 3D Furniture Estimation & Scene Registration

This document provides the mathematical derivations, architectural design, API specifications, and experiment guidelines for **FR-03 (Vision AI Automatic Furniture Classification)** and **FR-06 (3D Bounding Box Estimation & Scene Registration)** in SunSync.

---

## 1. Architectural Overview

Monocular RGB images inherently suffer from depth scale ambiguity: an object in a 2D bounding box can be small and close or large and far away. Standard monocular depth networks (such as *Depth Anything V2* or *MiDaS*) output relative or affine-invariant inverse depth $d_{\text{rel}}$, but lack metric scale (metres). Furthermore, single-view 3D estimation can degrade for partial occlusions, far objects, or fallback contour detections.

SunSync pairs monocular Vision AI with a **2-Stage Coarse-to-Fine Hybrid Pipeline** anchored on the smart film window's $6 \times 6$ ArUco marker (DICT_4X4_50, side length $s = 0.6\,\text{m}$):
1. **Metric Anchor**: The window ArUco marker has confirmed metric dimensions and confirmed ENU transform $T_{\text{ENU}, \text{marker}}$ on the window plane.
2. **Stage 1 — Default Neural Detection & Depth with Safe Fallback**: `VisionRegistrationPipeline` enables `prefer_yolo=True` and `prefer_monocular=True` by default via `CompositeFurnitureDetector` (`YoloV8DetectorAdapter` with expanded indoor classes and `_merge_book_clusters` → fallback `GeometricSyntheticDetector`) and `CompositeDepthEstimator` (`MonocularDepthAdapter` → fallback `GeometricSyntheticDepthEstimator`).
3. **Camera Pose & Metric Depth Calibration**: OpenCV's `cv2.solvePnP` with `SOLVEPNP_IPPE_SQUARE` computes $T_{\text{ENU}, \text{camera}}$ and exact metric depths $Z_{\text{true}}$ of the marker corners, calibrating $Z_{\text{metric}}(u, v) = \alpha \cdot d_{\text{rel}}(u, v) + \beta$ (falling back to proportional scaling $\beta = 0$ when anchor depth spread is narrow).
4. **Floor-Standing 3D Cuboid Unprojection & Per-Item Uncertainty Scoring**: 2D bounding boxes are unprojected into 3D, constrained to the floor ($z_{\text{base}} = 0$), clamped via `clamp_cuboid_to_room`, assigned optical `ProtectedRegion`s, and scored via `_assess_estimation_quality` (`estimation_quality`, `needs_aruco_refinement`, `uncertainty_reason_ko`, `aruco_capture_guidance_ko`, `estimated_corners_enu_m`).
5. **Stage 2 — Guided ArUco Corner Capture (Human-in-the-Loop Refinement)**: In `SpatialCapture.tsx` (*"사진 1장으로 가구 먼저 찾기"*), high-uncertainty or user-selected estimates launch *"이 가구 위치로 가서 ArUco 정밀 촬영"*, guiding the user with an on-screen ENU spatial prompt to walk to the estimated furniture location and capture the window ArUco marker at corners `P1~P4` (or confirm directly as an AI draft via *"AI 추정 치수로 바로 도면에 배치"*).

```mermaid
flowchart TD
    Img[Single Indoor RGB Photo] --> Stage1[Stage 1: CompositeFurnitureDetector YOLOv8 + CompositeDepthEstimator]
    Aruco[6x6 ArUco Window Marker] -->|solvePnP IPPE_SQUARE| Pose[Camera Pose T_ENU_cam & Z_true Corners]
    Stage1 --> Calib[Depth Calibration: Linear or Proportional Scale]
    Pose --> Calib
    Calib --> Backproj[3D Cuboid Back-Projection & clamp_cuboid_to_room]
    Backproj --> Quality[_assess_estimation_quality & ENU Corners]
    Quality -->|Direct Draft| SceneDraft[Apply AI Estimate to Scene]
    Quality -->|needs_aruco_refinement / User Choice| GuidedAruco[Stage 2: Guided P1~P4 ArUco Corner Capture in SpatialCapture.tsx]
    GuidedAruco --> SceneFinal[High-Precision Registered Occluder & ProtectedRegion]
```

---

## 2. Mathematical Framework

### Step 1 — Window Marker Pose & Corner Depths

Let the window ArUco marker have side length $s$ ($0.6\,\text{m}$). In the marker local frame (centered at $(0, 0, 0)$ with $+x$ right, $+y$ up, $+z$ normal):
$$P_{m, 0} = \left(-\frac{s}{2}, \frac{s}{2}, 0\right)^T, \quad P_{m, 1} = \left(\frac{s}{2}, \frac{s}{2}, 0\right)^T$$
$$P_{m, 2} = \left(\frac{s}{2}, -\frac{s}{2}, 0\right)^T, \quad P_{m, 3} = \left(-\frac{s}{2}, -\frac{s}{2}, 0\right)^T$$

Given camera intrinsics matrix $K = \begin{pmatrix} f_x & 0 & c_x \\ 0 & f_y & c_y \\ 0 & 0 & 1 \end{pmatrix}$ and observed corner pixels $p_i = (u_i, v_i)$, `cv2.solvePnP` with `SOLVEPNP_IPPE_SQUARE` computes:
$$P_c = R_{c\_m} P_m + t_{c\_m}$$

The rigid transformation from marker frame to ENU frame is $T_{\text{ENU}, m} = \begin{pmatrix} R_{\text{ENU}, m} & t_{\text{ENU}, m} \\ 0 & 1 \end{pmatrix}$. The camera pose in ENU coordinates is then derived as:
$$R_{\text{ENU}, c} = R_{\text{ENU}, m} R_{c\_m}^T$$
$$t_{\text{ENU}, c} = t_{\text{ENU}, m} - R_{\text{ENU}, c} t_{c\_m}$$
$$T_{\text{ENU}, c} = \begin{pmatrix} R_{\text{ENU}, c} & t_{\text{ENU}, c} \\ 0 & 1 \end{pmatrix}$$

The true metric depths of the 4 marker corners in the camera optical frame ($Z_c$) are:
$$Z_{\text{true}, i} = \left[R_{c\_m} P_{m, i} + t_{c\_m}\right]_z \quad (i = 0, 1, 2, 3)$$

### Step 2 — Linear Depth Calibration

Given relative depth values $d_{\text{rel}, i}$ at anchor points and their true metric depths $Z_{\text{true}, i}$, we formulate the least-squares linear regression:
$$\min_{\alpha, \beta} \sum_{i=1}^N \left(\alpha \cdot d_{\text{rel}, i} + \beta - Z_{\text{true}, i}\right)^2$$

When $N \ge 2$ and relative depth values have sufficient spread ($\text{ptp}(d_{\text{rel}}) > 0.15$ and $\text{ptp}(Z_{\text{true}}) > 0.25\,\text{m}$):
$$\begin{pmatrix} \alpha \\ \beta \end{pmatrix} = \left(A^T A\right)^{-1} A^T Z_{\text{true}}, \quad A = \begin{pmatrix} d_{\text{rel}, 1} & 1 \\ \vdots & \vdots \\ d_{\text{rel}, N} & 1 \end{pmatrix}$$

For narrow/nearly-flat marker depth ranges or single-point anchors, proportional scale-only calibration is used ($\beta = 0$, $\alpha = \bar{Z}_{\text{true}} / \max(10^{-4}, \bar{d}_{\text{rel}})$) to avoid ill-conditioned regression slopes.
All calibrated depths are clamped to physical boundaries:
$$Z_{\text{metric}} = \text{clip}\left(\alpha \cdot d_{\text{rel}} + \beta, 0.3\,\text{m}, 25.0\,\text{m}\right)$$

### Step 3 — 3D Cuboid Back-Projection & Floor Refinement

For each 2D bounding box $(x_1, y_1, x_2, y_2)$ with center $(u_c, v_c)$ and calibrated metric distance $Z_{\text{obj}}$:
1. **Ray Unprojection**:
   $$P_{\text{left}, c} = \left(\frac{x_1 - c_x}{f_x} Z_{\text{obj}}, \frac{v_c - c_y}{f_y} Z_{\text{obj}}, Z_{\text{obj}}\right)^T$$
   $$P_{\text{right}, c} = \left(\frac{x_2 - c_x}{f_x} Z_{\text{obj}}, \frac{v_c - c_y}{f_y} Z_{\text{obj}}, Z_{\text{obj}}\right)^T$$
   $$P_{\text{top}, c} = \left(\frac{u_c - c_x}{f_x} Z_{\text{obj}}, \frac{y_1 - c_y}{f_y} Z_{\text{obj}}, Z_{\text{obj}}\right)^T$$
   $$P_{\text{bot}, c} = \left(\frac{u_c - c_x}{f_x} Z_{\text{obj}}, \frac{y_2 - c_y}{f_y} Z_{\text{obj}}, Z_{\text{obj}}\right)^T$$
2. **ENU Transformation**: $P_{\text{ENU}} = R_{\text{ENU}, c} P_c + t_{\text{ENU}, c}$.
3. **Floor-Standing Constraint**: $z_{\text{base}} = 0.0$ for `bookshelf`, `desk`, and `chair`.
4. **Dimensions & Yaw**:
   - Width $w = \|P_{\text{right}, \text{ENU}}[0:2] - P_{\text{left}, \text{ENU}}[0:2]\|_2$.
   - Lateral yaw $\theta = \text{atan2}(\Delta y, \Delta x) \pmod{360}$.
   - Depth thickness $d$ is derived from class priors ($0.40\,\text{m}$ for bookshelf, $0.70\,\text{m}$ for desk, $0.55\,\text{m}$ for chair) or depth span.
   - The cuboid center is positioned at $P_{\text{front}} + \frac{d}{2} \hat{v}_{\text{inward}}$, where $\hat{v}_{\text{inward}}$ points away from the camera.
5. **Room Polygon Clamping**: `clamp_cuboid_to_room` guarantees the cuboid footprint is strictly inside `room.floor_vertices_m` and height $\le \text{room.height\_m}$.

### Step 4 — Default ProtectedRegion Generation

- **Bookshelf**: Generates vertical front surface facing into the room with **Grade B** (protects books against direct solar UV degradation).
- **Desk / Table**: Generates horizontal top tabletop surface ($z = h$) with **Grade C** (maintains glare-free reading/work lighting).

### Step 5 — Quality & Uncertainty Scoring and Guided ArUco Fallback

`VisionRegistrationPipeline._assess_estimation_quality(...)` evaluates each estimated item across four signals (`detection_source`, object `confidence`, calibrated distance `z_metric`, and `was_clamped`) to classify single-photo reliability (`"high"`, `"medium"`, or `"low_needs_aruco"`) and emit Korean spatial guidance (`aruco_capture_guidance_ko`, `recommended_capture_shape`, `estimated_corners_enu_m`). When reliability is `"low_needs_aruco"` or `"medium"` (or whenever the user wants higher accuracy), `SpatialCapture.tsx` hands off the item metadata directly into `P1~P4` corner ArUco capture via *"이 가구 위치로 가서 ArUco 정밀 촬영"*.

---

## 3. REST API Specification

### Endpoints:
- `POST /api/scenes/{scene_id}/vision-furniture-estimate` (JSON payload with optional `image_base64` / `detections`)
- `POST /api/scenes/{scene_id}/vision-furniture-photo` (`multipart/form-data` photo upload delegating into the same pipeline)

#### Request Payload (`VisionFurnitureEstimateRequest`)
```json
{
  "camera": {
    "width": 1280,
    "height": 720,
    "fx": 900.0,
    "fy": 900.0,
    "cx": 640.0,
    "cy": 360.0
  },
  "marker_anchor": {
    "code_id": 0,
    "side_length_m": 0.6,
    "corners_px": [
      [572.5, 292.5],
      [707.5, 292.5],
      [707.5, 427.5],
      [572.5, 427.5]
    ],
    "anchor_depth_rel": 4.0
  },
  "detections": [
    {
      "box_xyxy": [450.0, 320.0, 720.0, 580.0],
      "label": "desk",
      "confidence": 0.95,
      "observed_depth_rel": 2.5,
      "name": "Oak Study Desk",
      "protection_grade": "C"
    }
  ],
  "apply_to_scene": true,
  "auto_create_protected_regions": true
}
```

#### Response Payload (`VisionFurnitureEstimateResponse`)
```json
{
  "scene_id": "reading_room_01",
  "scene_revision": 2,
  "applied_to_scene": true,
  "camera_position_enu_m": [3.0, 0.8, 1.4],
  "depth_calibration_alpha": 1.0,
  "depth_calibration_beta": 0.0,
  "estimated_items": [
    {
      "occluder": {
        "occluder_id": "occluder_desk_1",
        "room_id": "room_reading",
        "name": "Oak Study Desk",
        "kind": "cuboid",
        "type": "desk",
        "base_center_m": [3.0, 3.0, 0.0],
        "dimensions_m": [1.4, 0.7, 0.75],
        "yaw_deg_ccw_from_east": 0.0,
        "material": "opaque",
        "quality": {
          "observation_ids": ["obs_vision_a1b2c3d4"],
          "confirmed": false
        }
      },
      "protected_region": {
        "region_id": "region_occluder_desk_1",
        "room_id": "room_reading",
        "name": "Oak Study Desk Surface",
        "host_occluder_id": "occluder_desk_1",
        "vertices_m": [
          [2.3, 2.65, 0.75],
          [3.7, 2.65, 0.75],
          [3.7, 3.35, 0.75],
          [2.3, 3.35, 0.75]
        ],
        "receiving_normal": [0.0, 0.0, 1.0],
        "grade": "C",
        "policy_id": "policy_reading_room",
        "quality": {
          "observation_ids": ["obs_vision_a1b2c3d4"],
          "confirmed": false
        }
      },
      "confidence": 0.95,
      "calibrated_distance_m": 2.5,
      "clamped_to_room": false,
      "detection_source": "yolov8",
      "estimation_quality": "high",
      "needs_aruco_refinement": false,
      "uncertainty_reason_ko": "단일 사진 기준 추정 신뢰도가 양호하지만 정밀 배치가 필요하면 ArUco 촬영을 권장합니다.",
      "aruco_capture_guidance_ko": "창문에서 약 2.5m 거리(동쪽 3.0m, 북쪽 3.0m)로 추정된 '책상'(1.40×0.70×0.75m) 위치로 이동해 주세요. 해당 가구 네 모서리(P1~P4)에서 창문 ArUco 마커를 촬영하면 정확한 위치와 치수로 등록됩니다.",
      "recommended_capture_shape": "rectangle",
      "estimated_corners_enu_m": [[2.3, 2.65], [3.7, 2.65], [3.7, 3.35], [2.3, 3.35]]
    }
  ]
}
```

---

## 4. Experiment Guidelines for Teammates

### Running the Synthetic Benchmark
Execute the automated 3D benchmark tool:
```bash
.venv/bin/python tools/ai_experiments/run_fr03_vision_3d_experiment.py --noise-pct 2.0 --json-out /tmp/fr03_benchmark.json
```
Output:
- Exact camera pose recovery error (typically $0.0\,\text{cm}$)
- Metric depth calibration equation ($Z_{\text{metric}} = \alpha \cdot d_{\text{rel}} + \beta$)
- Per-object 3D center error, dimension error, and yaw orientation error.

### Running with Real-World Photographs
1. **Camera Calibration**: Note your camera's resolution and intrinsics ($f_x, f_y, c_x, c_y$).
2. **Window Marker**: Use the one `6×6` ArUco `DICT_4X4_50` marker ($s = 0.6\,\text{m}$) that the server shows on the selected film during a capture session (see [fr03_single_marker.md](fr03_single_marker.md)); the request's `marker_anchor` must be that marker.
3. **Capture Image**: Take an indoor photo where both the window marker and indoor furniture are visible.
4. **Detect Furniture (Default Composite + Safe Fallback)**:
   ```python
   from engine.vision_ai.protocols import create_default_detector
   detector = create_default_detector(prefer_yolo=True)  # YOLOv8 -> GeometricSyntheticDetector fallback
   detections = detector.detect(image_rgb)
   ```
   Direct `YoloV8DetectorAdapter("yolov8m.pt", conf_thresh=0.25)` also merges nearby `"book"` detections into unified `"bookshelf"` boxes via `_merge_book_clusters`.
5. **Estimate Relative Depth (Default Composite + Safe Fallback)**:
   ```python
   from engine.vision_ai.protocols import create_default_depth_estimator
   depth_estimator = create_default_depth_estimator(prefer_monocular=True)  # Depth Anything V2 -> Geometric fallback
   depth_map = depth_estimator.estimate_relative_depth(image_rgb, width=1280, height=720)
   ```
6. **Execute Pipeline or Mobile Hybrid Workflow**: Pass the photo/detections to `VisionRegistrationPipeline.run()` or `POST /api/scenes/{scene_id}/vision-furniture-photo`. In the web/mobile UI (`SpatialCapture.tsx`), inspect each candidate's `estimation_quality` and tap *"이 가구 위치로 가서 ArUco 정밀 촬영"* whenever single-photo geometry needs corner verification.

## Where the anchor comes from on the server (checked 2026-11-11)

`POST /api/scenes/{scene_id}/vision-furniture-estimate` takes the marker transform in this order:

1. the stored Scene marker whose `marker_id` equals `marker_anchor.marker_id`, if one is given, then the one whose `code_id` equals `marker_anchor.code_id`;
2. otherwise the first window marker of the Scene;
3. otherwise, if the Scene has windows: the centre of the first window, but only for a Scene with exactly one 1 m x 1 m window. Any other window layout (for example several film modules) answers 422 `Select and pair a film module before vision measurement.`, because the window centre is not the centre of the selected film;
4. otherwise, if the Scene has no windows and the request has `T_ENU_camera`, an identity marker transform is used;
5. otherwise 422 `Scene has no window or ArUco marker to anchor camera pose.`

Pair the selected film first (`POST /api/scenes/{scene_id}/marker-pairing` or a capture session).
