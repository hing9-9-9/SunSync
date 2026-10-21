# Decisions
| Date | Decision | Why | Trade-off |
|---|---|---|---|
| 2026-10-14 | Backend FastAPI, DB SQLite + SQLModel, Frontend React + Vite | Calc libs are Python; zero-install DB | SQLite weak for concurrent writes; move to PostgreSQL if deployed |
