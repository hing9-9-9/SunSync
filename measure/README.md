# measure
ArUco marker-based room measurement (research). Output must follow the room JSON format in docs/.

## Furniture registration service

[`registration-core/`](registration-core/README.md) provides a platform-neutral Python
measurement library and a standalone HTTP API for guided furniture registration.
It includes shape-based capture plans, multi-marker board pose estimation, standard-model
placement data, ENU conversion, synthetic examples and tests.

Install and run it from `measure/registration-core/` as described in its README.
The API is separate from the existing backend and requires mobile camera adapters,
service-level authentication and real-device accuracy validation before deployment.
Its FR-06 output is an extended measurement fragment, not a complete Scene accepted
directly by `/api/scenes`; see the integration contract before connecting the receiver.
