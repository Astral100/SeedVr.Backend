# K3s + CloudNativePG is the runtime stack on the Hetzner box

The self-hosted core services box (ADR 0008) runs K3s with Helm 4 charts, and PostgreSQL under the CloudNativePG operator using the Barman Cloud Plugin from day one — without Flux initially (Helm is driven from GitHub Actions; Flux can be added later if the full GitOps shape is wanted, at ~0.5–1 GB extra RAM). The decisive facts: CNPG packages the hard Postgres care — WAL archiving to S3-compatible storage, base backups, PITR restore, rolling primary updates — as declarative configuration, where every non-Kubernetes option assembles pgBackRest by hand; the whole system state lives as manifests/charts in git, which is the property that makes the setup a reusable template for future projects and the most legible possible surface for agent-driven operations; and rolling app deploys plus a one-command second-node growth path come native. Platform RAM cost ~1 GB (K3s ~750 MB measured with add-ons + CNPG operator ~100–200 MB) on the 8 GB box.

Eyes-open caveats accepted with the choice (survey: `docs/research/single-box-runtime-stack.md`):

- **CNPG archives WAL to exactly one destination** ("only one plugin at a time can be responsible for WAL archiving"), so ADR 0008's second offsite copy is bucket-level sync — a scheduled rclone R2→Backblaze B2 job with its own failure alerting, plus B2 versioning so a propagated deletion cannot destroy history. This is the one DIY backup component option A retains; pgBackRest's native multi-repo fan-out was the main argument for the losing side.
- **CNPG officially supports/tests only vanilla Kubernetes**; K3s runs it as CNCF-conformant but unsupported. Pin the K3s channel to a K8s minor CNPG lists as supported.
- **CNPG's in-tree backup path is deprecated** mid-transition to the Barman Cloud Plugin — start on the plugin, accept that the packaged machinery is itself in motion.
- **No primary source confirms R2 compatibility** for barman-cloud (or pgBackRest); it rests on generic S3-endpoint support. A live WAL-archive-to-R2 probe is added to the R2 live-probe ticket before implementation lock-in.
- Helm 3 security fixes end 2026-11-11 — start on Helm 4.

Consequence: the operator learns Kubernetes (deliberately — the skill and the template transfer to any cluster anywhere), the single-instance Postgres still blips on its own version updates (CNPG restarts the primary; harmless per the pipeline design), and K3s joins the pinned-versions upgrade-sprint list. SQLite is K3s's single-node default datastore; a later multi-server step needs embedded etcd or a datastore migration.

## Considered Options

- K3s + Helm 4 + CloudNativePG, Barman Cloud Plugin, no Flux (chosen) — packaged Postgres care, git-owned declarative state, best agent surface, rolling deploys, growth path; ~1 GB platform RAM.
- Docker Compose v5 + systemd + pgBackRest — fewest concepts and the survey's best PITR tool (native R2+B2 multi-repo, per-repo retention/encryption); loses on no rolling deploys, no reconciliation, no growth path, copy-and-adapt reuse.
- Kamal 2 + pgBackRest — the runner-up: MIT, 37signals-proven, zero-downtime app deploys, git-owned config; but Postgres care stays fully DIY, the accessory model restarts the DB with downtime, and reuse/skill transfer trail Kubernetes.
- Eliminated with cause: RKE2 (4 GB minimum), Talos (no SSH escape hatch, 3-control-plane Hetzner flow), MicroK8s (snapd, single-vendor), Nomad (BUSL license under IBM, anti-single-node posture, no PG operator), Podman+Quadlet (no zero-downtime, no growth path), Docker Swarm (momentum frozen, Mirantis→IREN uncertainty), panels/Coolify/Dokploy (dump-only backups, UI-held state), k0s (credible but trails K3s's ecosystem and currency), WAL-G as component (failover-only multi-storage).
