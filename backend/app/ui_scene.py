"""UI projection and its conversion into the unchanged FR06 Scene contract."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, Text
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from app.schemas.scene import Scene

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


def canonical_id(kind: str, identifier: str) -> str:
    return f"ui_{kind}_{sha256(identifier.encode()).hexdigest()}"


def region_surface(space: UISpace, region: UIRegion) -> tuple[list[list[float]], list[int]]:
    """Screen y grows down; ENU y grows north. Wind every face along its normal."""
    west, east = region.x, region.x + region.width
    south, north = space.depth - region.y - region.depth, space.depth - region.y
    low, high = region.bottom, region.top
    if region.face == "남쪽 전면":
        return [[west, south, low], [east, south, low], [east, south, high], [west, south, high]], [
            0,
            -1,
            0,
        ]
    if region.face == "북쪽 후면":
        return [[east, north, low], [west, north, low], [west, north, high], [east, north, high]], [
            0,
            1,
            0,
        ]
    if region.face == "동쪽 측면":
        return [[east, south, low], [east, north, low], [east, north, high], [east, south, high]], [
            1,
            0,
            0,
        ]
    if region.face == "서쪽 측면":
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


def to_scene(space: UISpace, revision: int, created_at: str | None = None) -> Scene:
    timestamp = created_at or datetime.now(UTC).isoformat()
    scene_id = canonical_id("scene", space.id)
    room_id = canonical_id("room", space.id)
    observation_id = canonical_id("observation", space.id)
    policy_id = canonical_id("policy", space.id)
    quality = {"observation_ids": [observation_id], "confirmed": True}
    protected = []
    for region in space.regions:
        vertices, normal = region_surface(space, region)
        center = [sum(point[axis] for point in vertices) / 4 for axis in range(3)]
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
                "sample_points_m": [*vertices, center],
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
                    "max_direct_irradiance_W_m2": 20,
                },
                "C": {
                    "rank": 3,
                    "constraint": "soft",
                    "rule": "intensity_cap",
                    "max_direct_irradiance_W_m2": 80,
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
                "quality": quality,
            },
            "occluders": [
                {
                    "occluder_id": canonical_id("furniture", f.id),
                    "room_id": room_id,
                    "name": f.name,
                    "kind": "cuboid",
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
            "windows": [],
            "markers": [],
            "optical_profiles": [],
        }
    )
