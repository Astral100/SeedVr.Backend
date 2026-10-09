# Monorepo layout and .NET solution structure

Decided on the code-layout ticket (#20, 2026-10-02), on the conventions research in [`docs/research/monorepo-layout-conventions.md`](../research/monorepo-layout-conventions.md).

**Top-level directories** (lowercase structural folders; PascalCase only on .NET project folders — the near-unanimous convention): `backend/`, `frontend/` (future), `gpu-worker/`, `shared/`, `infra/`, `docs/`, `probes/` (stays until revisited toward project completion), plus `.github/` for CI. No `src/` umbrella: polyglot single-product repos (immich the closest analogue) use per-app root folders, and no surveyed repo mixes a .NET `src/` with non-.NET sibling apps.

**All operational/deployment files centralize in `infra/`** — `tofu/`, `helm/` (cluster plumbing *and* the backend's chart), `vast/`, `telemetry/` — the attested pattern (airflow, dagster, immich). A per-area hybrid was agreed attractive but dropped: unattested in the survey, and convention is documentation we don't have to write. The worker image recipe lives in `gpu-worker/` beside the agent source (build recipe of the deliverable, not infrastructure).

**One solution at the repo root** (`PixUp.sln`) spanning backend and gpu-worker, with `shared/PixUp.Contracts` referenced by both entry points so protocol drift breaks the compile, not production. Deployment never uses the solution: each deliverable publishes from its entry project and the reference graph is the packing list.

**Projects** (project-per-service; the deployable stays one process per #8):

- `PixUp.Api` — thin web host: endpoints, startup wiring; hosts but does not contain the background services.
- `PixUp.AppCore` — records and rules: entities, EF migrations, state machine, timeout/retry/expiry policy, operations every door shares (rules used by several triggers live here; a loop's own scheduling lives with the loop).
- `PixUp.Dispatcher` — the dispatch seam (ADR 0003) and its pool implementation.
- `PixUp.Provisioner` — worker-pool lifecycle (ADR 0016), the only Vast API client.
- `PixUp.Maintenance` — recovery checks and the periodic deletion pass.
- `PixUp.Contracts` — backend↔agent message shapes (in `shared/`).
- `PixUp.GpuWorker` — the worker agent (in `gpu-worker/`).

**DI composition**: each project exposes one registration method (`AddAppCore()`, …) owning its services and options binding; `PixUp.Api`'s composition root is a few lines calling them. Swapping the dispatcher implementation is swapping one registration.

**Tests**: unit and integration tests never share a project and integration projects say so in the name. Shared cross-project suite at `backend/PixUp.IntegrationTests` (boots the deployable against Testcontainers Postgres per #14); per-project `PixUp.<Project>.Tests` (unit) and `PixUp.<Project>.IntegrationTests` (single-project integration), each created when its first test arrives. The shared suite is integration, not end-to-end: the world outside our code is faked; true end-to-end stays the manual live smoke (#14).

## Considered Options

- `src/` umbrella — rejected: the repo is polyglot once the frontend lands; the umbrella is a pure-.NET convention.
- Per-area infra files (`backend/infra/`, …) — rejected as unconventional despite its atomic-commit appeal; see above.
- Classic Domain/Application/Infrastructure/Api layering — rejected: compiler-enforced ceremony the 10–100-user launch doesn't need; every feature would touch four projects.
- Test project per production project — rejected: the main integration suite spans everything and would have no home; most twins would start empty.
