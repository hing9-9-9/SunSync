"""UI projection and its conversion into the unchanged FR06 Scene contract."""

import math
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Column, Text
from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, select

from app.schemas.scene import Occluder, Scene

Number = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[Number, Field(gt=0)]
Nonnegative = Annotated[Number, Field(ge=0)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Face = Literal["남쪽 전면", "북쪽 후면", "동쪽 측면", "서쪽 측면", "상단"]
FurnitureType = Literal["bookcase", "desk", "table", "cabinet", "display", "other"]
Wall = Literal["south", "north", "east", "west"]


class UIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, str_strip_whitespace=True
    )


class UISite(UIModel):
    latitude: Annotated[Number, Field(ge=-90, le=90)]
    longitude: Annotated[Number, Field(ge=-180, le=180)]
    timezone: Name = "Asia/Seoul"
    northConfirmed: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("유효한 시간대를 입력해 주세요.") from error
        return value


class UIFurniture(UIModel):
    id: Identifier
    name: Name
    zone: Name
    type: FurnitureType | None = None
    x: Nonnegative
    y: Nonnegative
    width: Positive
    depth: Positive
    height: Positive


class UIRegion(UIModel):
    id: Identifier
    name: Name
    zone: Name
    furnitureId: Identifier | None
    x: Nonnegative
    y: Nonnegative
    width: Positive
    depth: Positive
    face: Face
    bottom: Nonnegative
    top: Positive
    grade: Literal["A", "B", "C", "D"]


class UIWindowMarker(UIModel):
    """QR marker shown on the film; centre is relative to the window's lower-left corner."""

    size: Positive
    x: Nonnegative
    y: Nonnegative


class UIWindowDimensions(UIModel):
    width: Positive
    height: Positive
    wall: Wall | None = None
    offset: Nonnegative | None = None
    sill: Nonnegative | None = None
    marker: UIWindowMarker | None = None

    @model_validator(mode="after")
    def marker_inside(self) -> Self:
        m = self.marker
        if m and (
            m.x - m.size / 2 < -1e-6
            or m.y - m.size / 2 < -1e-6
            or m.x + m.size / 2 > self.width + 1e-6
            or m.y + m.size / 2 > self.height + 1e-6
        ):
            raise ValueError("QR 마커는 창문 안에 있어야 합니다.")
        return self


class UIShadingSchedule(UIModel):
    enabled: bool
    start: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
    end: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]

    @model_validator(mode="after")
    def check_interval(self) -> Self:
        if self.enabled and self.start == self.end:
            raise ValueError("차단 시작과 종료 시간을 다르게 설정해 주세요.")
        return self


class UISpace(UIModel):
    shadingSchedule: UIShadingSchedule | None = None
    id: Identifier
    building: Name
    floor: Name
    name: Name
    width: Positive
    depth: Positive
    height: Positive
    zone: Name
    windowDimensions: UIWindowDimensions | None = None
    zones: Annotated[list[Name], Field(min_length=1)]
    furniture: list[UIFurniture]
    regions: list[UIRegion]
    source: Literal["sample", "manual"]
    revision: Annotated[int, Field(ge=1)]
    site: UISite
    savedAt: str | None = None
    predictedRevision: int | None = None
    serverRevision: Annotated[int, Field(ge=1)] | None = None
    serverSceneId: str | None = None

    @model_validator(mode="after")
    def check_ui_references(self) -> Self:
        if len(self.zones) != len(set(self.zones)) or self.zone not in self.zones:
            raise ValueError("구역 이름과 선택한 구역을 확인해 주세요.")
        hosts = {f.id: f for f in self.furniture}
        if len(hosts) != len(self.furniture):
            raise ValueError("가구 ID가 중복됩니다.")
        if len({r.id for r in self.regions}) != len(self.regions):
            raise ValueError("보호영역 ID가 중복됩니다.")
        window = self.windowDimensions
        # Spaces saved before window position existed have none of these fields; keep them readable.
        if window and (window.wall, window.offset, window.sill) != (None, None, None):
            wall = window.wall or "south"
            length = self.width if wall in ("south", "north") else self.depth
            offset = window.offset if window.offset is not None else 0
            if offset + window.width > length + 1e-6:
                raise ValueError("창문이 벽 길이를 벗어났습니다.")
            if (window.sill or 0) + window.height > self.height + 1e-6:
                raise ValueError("창문이 천장 높이를 벗어났습니다.")
        for furniture in self.furniture:
            if furniture.zone not in self.zones:
                raise ValueError("가구의 구역 연결을 확인해 주세요.")
        for region in self.regions:
            host = hosts.get(region.furnitureId) if region.furnitureId else None
            if region.zone not in self.zones or (
                region.furnitureId and (host is None or host.zone != region.zone)
            ):
                raise ValueError("보호면의 가구와 구역 연결을 확인해 주세요.")
            if region.top <= region.bottom:
                raise ValueError("보호 끝 높이는 시작 높이보다 커야 합니다.")
            if host and region.face == "상단" and abs(region.top - host.height) > 1e-6:
                raise ValueError("상단 보호면의 높이는 가구 높이와 같아야 합니다.")
        return self


class UISpaceRecord(SQLModel, table=True):
    """Separate projection; no fields are added to the shared FR06 schema."""

    __tablename__ = "ui_spaces"
    space_id: str = SQLField(primary_key=True)
    scene_id: str = SQLField(index=True, unique=True)
    scene_revision: int
    payload_json: str = SQLField(sa_column=Column(Text, nullable=False))


class DeletedSpaceRecord(SQLModel, table=True):
    """Tombstone: a deleted space id, so copies left on other devices are dropped, not re-uploaded."""

    __tablename__ = "ui_deleted_spaces"
    space_id: str = SQLField(primary_key=True)
    deleted_at: str
    # Account that deleted it (auth_users.id); empty for ids deleted before accounts existed.
    owner_id: str | None = SQLField(default=None, index=True)


def canonical_id(kind: str, identifier: str) -> str:
    return f"ui_{kind}_{sha256(identifier.encode()).hexdigest()}"


# The protected side of a piece of furniture is the side that looks at the window (2026-11-06);
# people never pick it. A top face (e.g. a reading table) stays the top.
WINDOW_FACE = {"south": "남쪽 전면", "north": "북쪽 후면", "east": "동쪽 측면", "west": "서쪽 측면"}


def protected_face(space: UISpace, region: UIRegion) -> str:
    wall = space.windowDimensions.wall if space.windowDimensions else None
    if region.furnitureId and region.face != "상단" and wall:
        return WINDOW_FACE[wall]
    return region.face


def region_surface(space: UISpace, region: UIRegion) -> tuple[list[list[float]], list[int]]:
    """Screen y grows down; ENU y grows north. Wind every face along its normal."""
    west, east = region.x, region.x + region.width
    south, north = space.depth - region.y - region.depth, space.depth - region.y
    low, high = region.bottom, region.top
    face = protected_face(space, region)
    if face == "남쪽 전면":
        return [[west, south, low], [east, south, low], [east, south, high], [west, south, high]], [
            0,
            -1,
            0,
        ]
    if face == "북쪽 후면":
        return [[east, north, low], [west, north, low], [west, north, high], [east, north, high]], [
            0,
            1,
            0,
        ]
    if face == "동쪽 측면":
        return [[east, south, low], [east, north, low], [east, north, high], [east, south, high]], [
            1,
            0,
            0,
        ]
    if face == "서쪽 측면":
        return [[west, north, low], [west, south, low], [west, south, high], [west, north, high]], [
            -1,
            0,
            0,
        ]
    return [[west, south, high], [east, south, high], [east, north, high], [west, north, high]], [
        0,
        0,
        1,
    ]


FURNITURE_OVERLAP_EPS = 1e-6


def furniture_overlaps_3d(a: UIFurniture, b: UIFurniture) -> bool:
    """Return True when two floor-standing UI furniture cuboids in the same zone share a positive
    3D interior volume (> 1e-6 m along X, Y, and Z). Touching boundaries are allowed."""
    if a.zone != b.zone:
        return False
    overlap_x = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    overlap_y = min(a.y + a.depth, b.y + b.depth) - max(a.y, b.y)
    overlap_z = min(a.height, b.height)
    return (
        overlap_x > FURNITURE_OVERLAP_EPS
        and overlap_y > FURNITURE_OVERLAP_EPS
        and overlap_z > FURNITURE_OVERLAP_EPS
    )


def find_furniture_overlap(space: UISpace) -> tuple[UIFurniture, UIFurniture] | None:
    """Return the first pair of 3D-overlapping furniture items in the space, or None."""
    for i, first in enumerate(space.furniture):
        for second in space.furniture[i + 1 :]:
            if furniture_overlaps_3d(first, second):
                return (first, second)
    return None


def furniture_overlap_message(space: UISpace) -> str | None:
    """Return a Korean validation message naming the two overlapping furniture items, or None."""
    pair = find_furniture_overlap(space)
    if pair is None:
        return None
    first, second = pair
    return (
        f"'{first.name}'과 '{second.name}' 가구가 서로 겹칩니다. 가구 위치나 크기를 조정해 주세요."
    )


def to_scene(space: UISpace, revision: int, created_at: str | None = None) -> Scene:
    overlap = furniture_overlap_message(space)
    if overlap is not None:
        raise ValueError(overlap)
    timestamp = created_at or datetime.now(UTC).isoformat()
    scene_id = canonical_id("scene", space.id)
    room_id = canonical_id("room", space.id)
    observation_id = canonical_id("observation", space.id)
    policy_id = canonical_id("policy", space.id)
    quality = {"observation_ids": [observation_id], "confirmed": True}
    protected = []
    for region in space.regions:
        vertices, normal = region_surface(space, region)
        protected.append(
            {
                "region_id": canonical_id("region", region.id),
                "room_id": room_id,
                "name": region.name,
                "host_occluder_id": canonical_id("furniture", region.furnitureId)
                if region.furnitureId
                else None,
                "vertices_m": vertices,
                "receiving_normal": normal,
                "grade": region.grade,
                "policy_id": policy_id,
                "quality": quality,
            }
        )
    policy = (
        None
        if not protected
        else {
            "policy_id": policy_id,
            "version": "0.2.0",
            "status": "proposal_demo_only",
            "metric": "direct_solar_irradiance_W_m2",
            "aggregation": "max_over_sample_points",
            "threshold_status": "synthetic_demo_not_agreed",
            "max_time_step_s": 10,
            "brightening_margin_W_m2": 5,
            "brightening_hold_s": 60,
            "grades": {
                "A": {
                    "rank": 1,
                    "constraint": "hard",
                    "rule": "block_geometric_direct_paths",
                    "max_direct_irradiance_W_m2": 0,
                },
                "B": {
                    "rank": 2,
                    "constraint": "soft",
                    "rule": "intensity_cap",
                    "max_direct_irradiance_W_m2": 30,
                },
                "C": {
                    "rank": 3,
                    "constraint": "soft",
                    "rule": "intensity_cap",
                    "max_direct_irradiance_W_m2": 70,
                },
                "D": {
                    "rank": 4,
                    "constraint": "none",
                    "rule": "daylight_priority",
                    "max_direct_irradiance_W_m2": None,
                },
            },
        }
    )
    windows = []
    optical_profiles = []
    w = space.windowDimensions
    # Only explicitly positioned windows become calculation geometry. Legacy decorative
    # windows must be reviewed and saved with their wall/offset/sill first.
    if w and w.wall is not None and w.offset is not None and w.sill is not None:
        o, z, width, height = w.offset, w.sill, w.width, w.height
        frames = {
            "south": ([o + width, 0, z], [-1, 0, 0], [0, 1, 0], 180),
            "north": ([o, space.depth, z], [1, 0, 0], [0, -1, 0], 0),
            "east": ([space.width, space.depth - o, z], [0, -1, 0], [-1, 0, 0], 90),
            "west": ([0, space.depth - o - width, z], [0, 1, 0], [1, 0, 0], 270),
        }
        origin, u, normal, azimuth = frames[w.wall]
        vertices = [
            [origin[i] + u[i] * du + (dz if i == 2 else 0) for i in range(3)]
            for du, dz in [(0, 0), (width, 0), (width, height), (0, height)]
        ]
        optics_id = canonical_id("optics", space.id)
        windows.append(
            {
                "window_id": canonical_id("window", space.id),
                "room_id": room_id,
                "vertices_m": vertices,
                "inward_normal": normal,
                "u_axis": u,
                "v_axis": [0, 0, 1],
                "azimuth_deg_true": azimuth,
                "optical_profile_id": optics_id,
                "initial_film_direct_transmittance": 1,
                "film_rows": max(1, math.floor(height / 0.1 + 0.5)),
                "film_cols": max(1, math.floor(width / 0.1 + 0.5)),
                "quality": quality,
            }
        )
        optical_profiles.append(
            {
                "optical_profile_id": optics_id,
                "status": "assumed",
                "model": "direct_scalar_transmittance",
                "glazing_direct_transmittance": 1,
                "film_min_direct_transmittance": 0,
                "film_max_direct_transmittance": 1,
                "actual_sgf_properties": None,
            }
        )
    floor_match = re.fullmatch(r"(-?\d+)\s*층?", space.floor)
    floor_level = int(floor_match[1]) if floor_match else None
    if floor_level is not None and not -100 <= floor_level <= 1000:
        floor_level = None
    return Scene.model_validate(
        {
            "schema_version": "0.2.0",
            "scene_id": scene_id,
            "revision": revision,
            "data_kind": "synthetic_fixture" if space.source == "sample" else "measured_draft",
            "created_at": timestamp,
            "units": "m",
            "coordinate_frame": "ENU",
            "site": {
                "latitude_deg": space.site.latitude,
                "longitude_deg": space.site.longitude,
                "timezone": space.site.timezone,
                "source": "user_entered_ui",
            },
            "north_alignment": {
                "source": "manual" if space.site.northConfirmed else "assumed",
                "accuracy_deg": None,
                "confirmed": space.site.northConfirmed,
                "note": "UI plan up is north; confirmation is recorded separately.",
            },
            "room": {
                "room_id": room_id,
                "name": space.name,
                "floor_vertices_m": [
                    [0, 0, 0],
                    [space.width, 0, 0],
                    [space.width, space.depth, 0],
                    [0, space.depth, 0],
                ],
                "height_m": space.height,
                "floor_level": floor_level,
                "quality": quality,
            },
            "occluders": [
                {
                    "occluder_id": canonical_id("furniture", f.id),
                    "room_id": room_id,
                    "name": f.name,
                    "kind": "cuboid",
                    "type": {"bookcase": "bookshelf", "desk": "desk", "table": "desk"}.get(
                        f.type or "other", "other"
                    ),
                    "base_center_m": [f.x + f.width / 2, space.depth - f.y - f.depth / 2, 0],
                    "dimensions_m": [f.width, f.depth, f.height],
                    "yaw_deg_ccw_from_east": 0,
                    "material": "opaque",
                    "quality": quality,
                }
                for f in space.furniture
            ],
            "protected_regions": protected,
            "observations": [
                {
                    "observation_id": observation_id,
                    "method": "manual",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "uncertainty_m": None,
                    "confirmed": True,
                    "note": "User-reviewed UI geometry. QR sizes are synthetic demo values, not actual ArUco observations."
                    if space.source == "sample"
                    else "User-entered geometry; no measurement accuracy certification.",
                }
            ],
            "policy": policy,
            # The UI's decorative window has no measured geometry; never invent a window.
            "windows": windows,
            "markers": [],
            "optical_profiles": optical_profiles,
        }
    )


# Fields other modules may change without touching what the UI owns.
SERVER_OWNED = {
    "revision",
    "scenario",
    "markers",
    "registration_calibration",
    "observations",
    "occluders",
}
UI_TYPES: dict[str, FurnitureType] = {"bookshelf": "bookcase", "desk": "desk"}


UI_LABELS = {"bookcase": "책장", "desk": "책상"}


def ui_furniture(occluder: Occluder, space: UISpace, number: int = 1) -> UIFurniture:
    """A measured cuboid seen from above: the plan has no rotation, so a turned piece shows as its
    axis-aligned footprint, moved inside the room."""
    width, depth, height = occluder.dimensions_m
    yaw = math.radians(occluder.yaw_deg_ccw_from_east)
    w = min(abs(width * math.cos(yaw)) + abs(depth * math.sin(yaw)), space.width)
    d = min(abs(width * math.sin(yaw)) + abs(depth * math.cos(yaw)), space.depth)
    cx, cy = occluder.base_center_m[0], occluder.base_center_m[1]
    kind = UI_TYPES.get(occluder.type, "other")
    # Engine names are machine text ("desk standard-model bounding box"); users read Korean labels.
    korean = re.search(r"[가-힣]", occluder.name) is not None
    return UIFurniture(
        id=occluder.occluder_id,
        name=occluder.name if korean else f"측정 {UI_LABELS.get(kind, '가구')} {number}",
        zone=space.zone,
        type=kind,
        x=round(min(max(cx - w / 2, 0), space.width - w), 3),
        y=round(min(max(space.depth - cy - d / 2, 0), space.depth - d), 3),
        width=round(w, 3),
        depth=round(d, 3),
        height=round(min(height, space.height), 3),
    )


def sync_ui_record(session: Session, previous: Scene, scene: Scene) -> None:
    """Keep a UI-created space editable after a server-only change (calibration, registration).

    Only additions the UI can show are accepted: room, site, windows and regions stay as they
    were and existing furniture is untouched. Anything else leaves the space marked as changed
    by another module, as before.
    """
    record = session.exec(
        select(UISpaceRecord).where(UISpaceRecord.scene_id == scene.scene_id)
    ).first()
    if record is None or record.scene_revision != previous.revision:
        return
    before, after = previous.model_dump(mode="json"), scene.model_dump(mode="json")
    if any(before[key] != after[key] for key in before if key not in SERVER_OWNED):
        return
    old = {o.occluder_id: o for o in previous.occluders}
    new = {o.occluder_id: o for o in scene.occluders}
    if any(new.get(key) != value for key, value in old.items()):
        return
    space = UISpace.model_validate_json(record.payload_json)
    fresh = [o for key, o in new.items() if key not in old]
    added = [ui_furniture(o, space, len(space.furniture) + i + 1) for i, o in enumerate(fresh)]
    try:
        synced = UISpace.model_validate(
            {
                **space.model_dump(mode="json"),
                "furniture": [
                    *space.model_dump(mode="json")["furniture"],
                    *(f.model_dump() for f in added),
                ],
                "serverRevision": scene.revision,
            }
        )
    except ValidationError:
        return
    record.scene_revision = scene.revision
    record.payload_json = synced.model_dump_json()
    session.add(record)
