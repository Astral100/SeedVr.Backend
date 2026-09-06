# The product lives in one monorepo

This repository is the monorepo for the whole SeedVR2 product. The backend (this planning effort's subject), the bundled worker-side service that ships in the GPU worker image, and the frontend website when it is built all live here under top-level directories. Decided by the user during charting (2026-09-06), ahead of the code-layout grilling (#20), so every structural decision downstream inherits it; #20 decides the top-level directory scheme and where the .NET solution sits.

What it buys: one source of truth for the product. Cross-cutting changes — an API contract change with its frontend client, a worker protocol change with its dispatcher counterpart — land as one atomic commit; a single `CONTEXT.md`, ADR set and docs tree governs all parts; agent sessions operate over one tree instead of coordinating across repositories.

Consequences carried into open tickets: CI/CD (#13) must path-filter pipelines per top-level area rather than build repo-wide, and the repository name `SeedVr.Backend` stops being accurate once non-backend code lives here — the rename, and what moves in when, are settled in #20.

The POC repo (`Astral100/SeedVr`) stays separate: its code is copied in when implementation starts, and changes to it remain out of scope.

## Considered Options

- Separate repositories per part (backend, frontend, worker image). Rejected — it splits the product's source of truth, and every cross-cutting change needs coordinated commits and version alignment across repositories.
