# Control-plane hosting price re-verification (September 2026)

Follow-up to [control-plane-hosting.md](control-plane-hosting.md). Prices re-verified 2026-09-01 against primary sources (vendor pricing pages/docs; Azure via the official Retail Prices API, https://prices.azure.com/api/retail/prices, because azure.com pricing pages render "$-" without JS). Adds vendors and SKUs the original survey did not cover: Hostinger, Azure VMs, App Service Premium tiers, and post-repricing Render/Fly numbers.

**Region note**: Azure figures here are **West Europe**; the original survey used East US. Some meters differ by region (ACA requests $0.56/M vs $0.40/M; Log Analytics $2.99/GB vs $2.30/GB).

**Reference workload**: one always-on app container (API + dispatcher merged, ~1 vCPU / 2 GB), PostgreSQL with ~32 GB storage, telemetry sink. All prices monthly, pay-as-you-go retail, excl. VAT. Hetzner in EUR; everything else USD.

---

## Summary — the managed↔unmanaged ladder

### At ~2–4 GB app tier (+ managed PG B1ms + 32 GB ≈ $19 where applicable)

| Rung | Option | App compute/mo | Total/mo |
|---|---|---|---|
| Unmanaged, budget vendor | Hetzner CX33 (all on one box) | €8.99 | **~€11–13** (incl. offsite backup storage) |
| Unmanaged, hyperscaler | Azure VM B1ms (1 vCPU/2 GiB) + 64 GB disk + IP | $25.97 | **~$45** |
| | Azure VM B2als_v2 (2 vCPU/4 GiB) + disk + IP | $39.99 | **~$59** |
| Managed runtime, deploy blip | App Service Basic B1 (1.75 GB, no slots) | $13.14 | **~$32** |
| Managed + zero-downtime | Render Standard (1 CPU/2 GB) | $25 | **~$53** (Hobby) / ~$78 (Pro workspace) |
| | Fly.io shared-2x / 2 GB | $11.83 | ~$59 |
| | App Service P0v3 (1 vCPU/4 GB, 20 slots) | $64.97 | **~$84** |
| Serverless-style | ACA 1 vCPU/2 GiB | $29 idle – $103 active | **~$48–122** |

### At ~8 GB app tier

| Option | Total/mo (with PG) |
|---|---|
| Hetzner CX33 (already 8 GB) | ~€11–13 |
| App Service B3 (4 core/7 GB, no slots) | ~$71 |
| Azure VM B2as_v2 (2 vCPU/8 GiB, AMD) | ~$91 |
| Fly.io shared-4x/8 GB (estimated from ~$5/GB RAM rate) | ~$90 |
| App Service P1v3 (2 vCPU/8 GiB, slots) | ~$149 |
| Render 2c-8g | ~$163 |
| ACA (1:2 vCPU:mem ratio forces 4 vCPU/8 GiB) | **~$143–453** |

---

## Hetzner Cloud

Current shared-vCPU lineup (DE/FI, EUR/mo excl. VAT, excl. IPv4; IPv4 +€0.50/mo):

| Plan | vCPU | RAM | NVMe | Traffic | €/mo |
|---|---|---|---|---|---|
| CX23 | 2 | 4 GB | 40 GB | 20 TB | 5.49 |
| CX33 | 4 | 8 GB | 80 GB | 20 TB | 8.49 |
| CX43 | 8 | 16 GB | 160 GB | 20 TB | 15.99 |
| CAX11 (Arm) | 2 | 4 GB | 40 GB | 20 TB | 5.99 |
| CAX21 (Arm) | 4 | 8 GB | 80 GB | 20 TB | 10.49 |

CPX (AMD) line is no longer price-competitive after June 2026 (CPX22 = €19.49). US locations use CPX-only in USD at much higher prices with 1–5 TB traffic.

**2026 price-hike timeline** (the "Hetzner hiked 150%" claim from an Aug 2026 video is *partially* true, misdated):

- **Apr 1** (existing customers too): cloud +30–37% DE/FI, storage +30%, dedicated +3–21%.
- **Jun 15** (new orders/rescales only): CPX +144–175%, CCX +113–173%, **CX/CAX only +30–38%**.
- Cumulative CX23 since Jan 2026: €2.99 → €5.49 (+84%). Hetzner repriced more aggressively in 2026 than any managed candidate.

Sources: https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/, https://www.hetzner.com/pressroom/statement-price-adjustment/, https://docs.hetzner.com/cloud/servers/overview/

## Hostinger

KVM VPS (USD, 24-month intro term): KVM 2 = 2 vCPU/8 GB/100 GB, **$8.99 intro / $14.99 renewal**; KVM 4 = 4 vCPU/16 GB, $12.99/$28.99. Dedicated IPv4 included. Free backups are **weekly only** (daily is a paid upgrade; max 4 backups retained); one manual snapshot that expires after 1 day. 99.9% uptime via blog-level guarantee. Verdict: cheapest sticker, weakest production story; hcloud's IaC/API ecosystem is far stronger — Hetzner dominates it for this use. Source: https://www.hostinger.com/vps-hosting

## Azure Container Apps (Consumption, West Europe)

Active: $0.000034/vCPU-s + $0.000004/GiB-s. Idle: $0.000004/vCPU-s + $0.000004/GiB-s. Free grant 180k vCPU-s + 360k GiB-s + 2M requests/mo. Always-on replica (730 h), net of grant:

| Size | Idle-billed | Active-billed |
|---|---|---|
| 0.5 vCPU/1 GiB | ~$15 | $47.63 |
| 1 vCPU/2 GiB | ~$29 | $102.82 |
| 2 vCPU/4 GiB | ~$61 | $213.19 |
| 4 vCPU/8 GiB | ~$124 | ~$434 |

The **idle-vs-active classification decides a 3–5× swing** and can't be computed from the pricing page: idle requires minReplicas≥1, no requests in flight, and near-zero CPU — a dispatcher loop plus steady worker progress calls flips an unknown fraction of seconds to active. **Recommendation: deploy the PoC on ACA for a week and read the actual meter before treating any ACA estimate as real.** Consumption enforces ~1:2 vCPU:memory pairs, so 8 GiB forces 4 vCPU. Dedicated plan minimum profile (D4) lands ~$387/mo — never cheaper here. Source: https://azure.microsoft.com/en-us/pricing/details/container-apps/

## Azure Database for PostgreSQL Flexible Server (West Europe)

B1ms (1 vCore/2 GiB) $14.53 + 32 GiB × $0.1369 = **$18.91/mo**. B2s (2 vCore/4 GiB) → $62.49/mo. PITR included (7-day default, max 35); backup storage free up to 100% of provisioned storage. Source: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-backup-restore

## Azure VMs (Linux, West Europe)

| Size | vCPU | RAM | $/mo |
|---|---|---|---|
| B1ms | 1 | 2 GiB | 17.52 |
| B2als_v2 | 2 | 4 GiB | 31.54 |
| B2s | 2 | 4 GiB | 35.04 |
| B2as_v2 (AMD) | 2 | 8 GiB | 63.07 |
| B2ms / B2s_v2 | 2 | 8 GiB | 70.08 |

Add 64 GiB Standard SSD (E6) $4.80/mo + standard static IPv4 $3.65/mo. Classic B-series was repriced 2025-10-01 to match v2 equivalents.

## Azure App Service (Linux, West Europe)

| Tier | Specs | $/mo | Deployment slots |
|---|---|---|---|
| B1 | 1 core/1.75 GB | 13.14 | none |
| B3 | 4 core/7 GB | 51.83 | none |
| S1 | 1 core/1.75 GB | 69.35 | 5 |
| P0v3 | 1 vCPU/4 GB | 64.97 | 20 |
| P1v3 | 2 vCPU/8 GB | 129.94 | 20 |

Zero-downtime deploys need slot swap ⇒ Basic tier deploys take a restart blip. S1 is dominated by P0v3. A pricier Premium v4 line exists. Source: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits

## Azure Monitor / Log Analytics (West Europe)

Analytics ingestion $2.99/GB after 5 GB/mo free; Basic Logs $0.65/GB. Source: https://azure.microsoft.com/en-us/pricing/details/monitor/

## Render

Post-April-2026 restructure: flat-fee workspaces (Hobby $0; **Pro $25/mo flat, unlimited seats — gates autoscaling**, dedicated egress IPs, 7-day PITR window; Hobby gets 3-day PITR). Compute: 0.5c/512 MB $7; **1c/2 GB $25**; 2c/4 GB $85; 2c/8 GB $135. Managed PG: 0.5c/1 GB $19; 1c/2 GB $40; storage $0.30/GB-mo beyond 1 GB; **PITR included on paid instances**. Realistic launch total ~$53 (Hobby) / ~$78 (Pro) — the original survey's ~$21–25 predates the repricing. Sources: https://render.com/pricing, https://render.com/docs/new-workspace-plans

## Fly.io

Machines: shared-cpu-1x/2 GB $11.11, shared-cpu-2x/2 GB $11.83 (US regions; region markups apply); extra RAM ~$5/GB-mo. Managed Postgres: Basic (shared-2x/1 GB) $38 + $0.28/GB storage; backups/HA/pooling included but **alerting, security patching, version upgrades still "in development"** — too immature for a paid-jobs system of record. Launch total ~$59. Sources: https://fly.io/docs/about/pricing/, https://fly.io/docs/mpg/

---

## Findings

1. **Corrections to the original survey**: ACA "~$16–70 (typ. $30–50)" understates always-active billing ($103/mo at 1 vCPU/2 GiB; only ~$29 if idle-billed) — the idle/active mix must be measured empirically. Render "~$21–25" predates April 2026 repricing; realistic is $53–78.
2. **The managed premium decomposes into two purchases**: vendor premium (Hetzner box → same-spec Azure VM: ~4×, ~€13 vs ~$45) and runtime-management premium (Azure VM → App Service/ACA: another 1.5–3×). They can be bought independently.
3. **Hybrid rung worth noting**: Azure VM B1ms + Flexible Server ≈ **$45/mo** = managed PITR Postgres (the part that matters most) + flat-billed SSH-able compute.
4. **RAM economics explain the VPS/PaaS gap at 8 GB**: on a VPS, RAM is nearly free and is shared with co-located Postgres (page cache); PaaS meters RAM as the expensive axis and sells DB RAM separately. The app tier itself needs only 1–2 GB (async ASP.NET Core control plane, no video bytes in-process).
5. **Deploy-blip pricing interaction**: zero-downtime tiers (Render, App Service Premium) largely charge for surviving deploys. Durable job dispatch (jobs table with `FOR UPDATE SKIP LOCKED`, worker retry-with-backoff) makes brief deploy blips harmless, which unlocks the cheaper rungs (plain VM, App Service Basic, Hetzner) without violating the missed-webhook constraint.
6. **Cost gap in context**: unmanaged ~$12–15/mo vs managed ~$45–90/mo ⇒ ~$500–1,000/yr, against $5.4–10.8k/yr warm GPU spend — clearly secondary, but no longer pure noise as the original ($9–50/mo spread) framing suggested.
