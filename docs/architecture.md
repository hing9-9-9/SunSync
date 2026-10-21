# Architecture (draft)
measure / manual input -> room JSON -> engine (FR-01) -> backend API -> frontend

FR06 now supplies a shared Pydantic `Scene` contract at
`backend/app/schemas/scene.py`. Both manual input and ArUco floor observations
produce final metric ENU geometry with observation provenance. FastAPI validates
that aggregate before storing it in a separate SQLModel `SceneRecord` table.
The existing draft `Room` table is retained without migration or data deletion.

`POST /api/scenes` imports a complete scene, `GET` reads/lists scenes, and `PUT`
replaces a scene using an atomic revision check. JSON Schema and a validation-only
endpoint are available for the UI/measurement owners. See [FR06](fr06.md).

FR01 can consume `Scene` directly in the Python backend without HTTP. No existing
engine coordinate implementation is present; ENU is the integration contract to
confirm with the FR01 owner. FR01 sunlight, FR02 control, ArUco image extraction
and the frontend setup screens remain separate modules.
