# Core services are self-hosted on Hetzner

The core services — the always-on deployable and its PostgreSQL — run on a self-operated Hetzner cloud/dedicated box (~€11–13/mo) rather than a managed platform. The decisive facts: at like-for-like machine size the managed premium is 5–12× (an 8 GB app tier plus managed Postgres runs ~$71–163/mo against ~€13, i.e. ~$700–1,800/yr — no longer noise even beside GPU spend); the setup work largely converges anyway (IaC, CI/CD, TLS, secrets, monitoring, backup config and a restore drill happen under either rung, making managed setup ~60–70% of self-hosted, not 10%); and self-hosted RAM is nearly free, so the comfort of an 8 GB box with Postgres sharing the page cache costs nothing extra where a PaaS meters it as the expensive axis. The residual managed advantages — no on-call, no security-patch stream, and a vendor guaranteeing that point-in-time recovery actually works — were judged not worth the premium, given agent-assisted operations and infrastructure ownership valued as a deliberate skill investment.

The choice is deliberately low-stakes to reverse: everything in the design is portable (containers, plain Postgres, S3-compatible storage), so migrating to the managed fallback — Azure App Service B1 + PostgreSQL Flexible Server, ~$32/mo, PITR included — is days of work, not a re-platforming. The fallback is recorded here so a future capacity or burnout decision doesn't need to re-survey.

Binding conditions, design requirements rather than suggestions:

- **WAL-based point-in-time recovery** to R2, plus a second offsite copy at a different provider, with archive-failure monitoring and a restore rehearsed before launch. Dump-only backups (the Coolify/Dokploy built-in) are not an acceptable posture for the system of record; PITR is what caps a bad failure at minutes of loss instead of a day, and covers the bad-write class (botched migration, unscoped UPDATE) that snapshot dumps copy faithfully.
- **Automated OS security patching** on every internet-facing host (unattended-upgrades); pinned versions and infrequent upgrade sprints are fine for everything else.
- **Alerting that pages the operator.**

Consequences: the operator is on call (failures cost labor rather than the managed rung's helpless waiting — hyperscalers fail for hours too); the region is Hetzner's DE/FI datacenters, fine for EU-based first users; Supabase has no role as a Postgres host (its PITR is a $100/mo add-on; the auth ticket remains free to pick any identity provider); deploy-blip tolerance is moot since candidate stacks include rolling updates. The runtime stack (K3s + Helm + CloudNativePG vs plain Docker Compose + pgBackRest), edge posture, and machine/datacenter choice are decided in their own ticket.

## Considered Options

- Self-ops Hetzner box (chosen) — cheapest by 5–12× like-for-like, free RAM headroom, portable and inspectable; you are the SRE, subject to the binding conditions above.
- Azure App Service Basic B1 + Flexible Server (~$32/mo) — the managed fallback: cheapest fully-managed rung, PITR-included Postgres, no on-call; deploys blip (harmless per the pipeline design); loses the like-for-like comparison the moment RAM headroom is wanted.
- Azure VM + Flexible Server hybrid (~$45/mo) — price-dominated by App Service B1; only wins if raw SSH compute and vendor-guaranteed Postgres are both wanted.
- Render (~$53–78/mo post-April-2026 repricing), ACA (~$48–122/mo with a 3–5× idle/active metering unknown), Fly.io (~$59, immature managed PG), Heroku (~$100 production shape), ECS Fargate + RDS (~$50–65) — all dominated for this workload; Railway (no PITR) and AWS App Runner (closed to new customers) disqualified outright. Full survey: `docs/research/core-services-hosting.md`, prices re-verified in `docs/research/core-services-hosting-prices-2026-09.md`.
