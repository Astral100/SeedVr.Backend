# Monorepo code-layout conventions for a single-product .NET + IaC + Helm repo

Date: 2026-10-01. Scope: top-level folder schemes, Helm-chart/IaC placement, and folder-name casing in well-regarded .NET-centric and mixed-stack product monorepos, checked against the actual default-branch trees on GitHub as of today, plus the owning docs (Helm, Argo CD, Flux, GitLab, dotnet repo guidelines). Our shape: one product — .NET 9 backend + .NET worker agent + OpenTofu + Helm + future non-.NET frontend — one small team, K3s, trunk CD via GitHub Actions path filters.

**TL;DR.** Every .NET repo surveyed — framework and product alike — uses a lowercase `src/` umbrella with lowercase structural siblings (`test(s)`, `docs`, `eng`/`build`/`tools`) and PascalCase .NET *project* folders inside `src/` (bitwarden/server, OrchardCore, eShop, aspire, runtime, aspnetcore; jellyfin is mid-migration toward it; duplicati is the lone PascalCase-root outlier). Mixed-stack products (immich, grafana, airflow, dagster) instead put per-app/per-area lowercase folders at the root with no `src/` umbrella — immich (`server/`, `web/`, `mobile/`, `docs/`, `docker/`, `deployment/`) is the closest match to our shape and keeps its OpenTofu in-repo under `deployment/`. On charts: Helm itself mandates nothing about placement (only DNS-1123 lowercase chart names); Argo CD recommends a separate config repo but for multi-team GitOps reasons (access control, CI loops) that don't bind a single-product trunk-CD repo; Flux's "repo per app" pattern explicitly blesses source + deployment manifests in one app repo; GitLab's agent docs expect manifests in the same project. Real products split by audience: charts *distributed to third-party installers* live in separate repos (bitwarden/helm-charts, grafana/helm-charts, immich-charts); charts *the repo deploys itself with* live in-repo (airflow `chart/`, dagster `helm/`).

## 1. Top-level folder schemes observed (default branches, 2026-10-01)

### Framework/library repos (.NET)

- **dotnet/runtime**: `docs/`, `eng/`, `src/` only (plus dot-folders) ([tree](https://github.com/dotnet/runtime)). Everything, tests included, lives under `src/` areas (`src/libraries`, `src/coreclr`, `src/mono`, ...).
- **dotnet/aspnetcore**: same Arcade scheme — `docs/`, `eng/`, `src/` ([tree](https://github.com/dotnet/aspnetcore)); `src/` is subdivided into areas (`Http`, `Mvc`, `Components`, ...).
- **dotnet/aspire**: `benchmarks/`, `docs/`, `eng/`, `extension/`, `playground/`, `src/`, `tests/`, `tools/` ([tree](https://github.com/dotnet/aspire)) — `src/` umbrella + lowercase structural siblings.

### Product/service repos (.NET)

- **bitwarden/server** (production SaaS + self-host): `src/` (Api, Admin, Core, Identity, Billing, ...), `test/`, `util/` (Migrator, Nginx, Setup, Seeder — ops/aux projects), `dev/` (local dev environment), `perf/`, `bitwarden_license/`, `AppHost/` ([tree](https://github.com/bitwarden/server)). No deployment manifests in-repo (see §2).
- **OrchardCMS/OrchardCore** (CMS product+framework): `src/`, `test/`, `tools/`, `plans/`, `.scripts/` ([tree](https://github.com/OrchardCMS/OrchardCore)); quirk: docs live at `src/docs`, not root.
- **dotnet/eShop** (Microsoft's reference product app): `src/`, `tests/`, `e2e/`, `build/`, `img/` ([tree](https://github.com/dotnet/eShop)) — `src/` holds all services (`Basket.API`, `Ordering.API`, `WebApp`, `eShop.AppHost`, ...); build assets in lowercase `build/`.
- **jellyfin/jellyfin** (media-server product): transitional — 13 legacy PascalCase project folders still at root (`Jellyfin.Api`, `Emby.Naming`, `MediaBrowser.Controller`, ...) *plus* a newer `src/` (with `Jellyfin.Database`, `Jellyfin.Networking`, ...), `tests/`, `fuzz/`, `deployment/` ([tree](https://github.com/jellyfin/jellyfin)). The migration direction is root-projects → `src/`.
- **duplicati/duplicati**: the outlier — PascalCase root folders (`Duplicati/`, `BuildTools/`, `Executables/`, `Tools/`, `Assets/`) and no `src/` ([tree](https://github.com/duplicati/duplicati)); an older VS-era layout no current Microsoft repo follows.

### Product repos, mixed-stack (closest to our shape)

- **immich-app/immich** (TS server + web + Flutter mobile + Python ML): per-app lowercase root folders — `server/`, `web/`, `mobile/`, `machine-learning/`, `e2e/`, `docs/`, `docker/` (compose), `deployment/` (IaC), `open-api/`, `design/`, `i18n/` ([tree](https://github.com/immich-app/immich)). No `src/` umbrella; each app folder has its own internal `src/`.
- **grafana/grafana** (Go + TS): lowercase area folders at root — `pkg/` (Go), `public/` + `packages/` (frontend), `apps/`, `conf/`, `devenv/`, `docs/`, `scripts/`, `packaging/` (deb/rpm/docker/msi) ([tree](https://github.com/grafana/grafana)).
- **getsentry/sentry** (Python + TS): `src/`, `tests/`, `static/`, `api-docs/`, `devenv/`, `devservices/`, `scripts/`, `tools/`, `self-hosted/` ([tree](https://github.com/getsentry/sentry)).
- **apache/airflow**, **dagster-io/dagster** (Python products that also ship a chart): lowercase per-area root folders (`airflow-core/`, `providers/`, `chart/`, `dev/`, `docs/`, `scripts/` — [airflow](https://github.com/apache/airflow); `python_modules/`, `js_modules/`, `helm/`, `docs/`, `examples/` — [dagster](https://github.com/dagster-io/dagster)).

### Where docs / build tooling / deployment sit

- Docs: root `docs/` dominates (runtime, aspnetcore, aspire, grafana, immich, airflow, dagster); OrchardCore's `src/docs` is the exception; eShop and bitwarden/server keep docs elsewhere entirely.
- Build/eng tooling: `eng/` in Arcade-built Microsoft repos (runtime, aspnetcore, aspire); product repos use `build/` (eShop), `scripts/` (grafana, sentry, dagster), `.scripts/`+`tools/` (OrchardCore), `dev/` for local-environment tooling (bitwarden, airflow).
- Deployment files, when in-repo: lowercase root folder — immich `docker/` + `deployment/`, grafana `packaging/`, jellyfin `deployment/`, airflow `chart/`, dagster `helm/`. No surveyed repo nests deployment under `src/`.

**Pattern split**: framework repos (a) are pure `docs/eng/src`; .NET product repos (b) keep the `src/` umbrella and add lowercase operational siblings (`util`, `dev`, `perf`, `e2e`, `deployment`); *polyglot* product repos (b) drop the `src/` umbrella in favour of per-app root folders, because a single `src/` can't meaningfully span .NET + web + mobile + IaC toolchains.

## 2. Where Helm charts and IaC live

### What the owning docs say

- **Helm**: a chart is "a collection of files inside of a directory" whose "directory name is the name of the chart" ([Charts topic](https://helm.sh/docs/topics/charts/)); chart names must be DNS-1123 — lowercase letters/digits/dashes only ([best practices: conventions](https://helm.sh/docs/chart_best_practices/conventions/)), so the chart *folder* is lowercase by construction. Helm's docs say nothing about where that directory sits relative to application code — placement is explicitly out of Helm's scope.
- **Argo CD** best practices recommend "separating config vs. source code repositories", for five reasons: manifest-only changes without triggering a CI build, cleaner config-only audit history, apps assembled from multiple source repos, *separation of access* (devs vs. prod-pushers), and avoiding CI↔commit infinite loops ([best practices](https://argo-cd.readthedocs.io/en/stable/user-guide/best_practices/)). Note the reasons: access separation and multi-repo assembly are multi-team GitOps-fleet concerns; the CI-loop risk applies only when CI pushes manifest commits back to the watched repo.
- **Flux** documents four patterns — monorepo (apps/infrastructure/clusters dirs), repo-per-environment, repo-per-team, and **repo-per-app**, where "applications maintain their own repositories containing both source code and deployment manifests" ([repository structure guide](https://fluxcd.io/flux/guides/repository-structure/)). Repo-per-app is the one that matches a single-product repo: code and its manifests change atomically in one place.
- **GitLab** (agent for Kubernetes, install docs): "If you have an agent configuration file, it must be in this project. Your cluster manifest files should also be in this project" ([install docs](https://docs.gitlab.com/user/clusters/agent/install/)) — co-locating manifests with the project is the documented default; separate agent projects are positioned for multi-project scale ([enterprise considerations](https://docs.gitlab.com/user/clusters/agent/enterprise_considerations/)).

### What real products do

- **Separate chart repo** — chosen when the chart is a *product for third-party installers* with its own release cadence: [bitwarden/helm-charts](https://github.com/bitwarden/helm-charts) (`charts/self-host`, `charts/sm-operator` + `examples/`, while bitwarden/server has no manifests), [grafana/helm-charts](https://github.com/grafana/helm-charts), [immich-app/immich-charts](https://github.com/immich-app/immich-charts). Sentry goes further: deployment artifacts live in a dedicated [getsentry/self-hosted](https://github.com/getsentry/self-hosted) repo, and its Helm charts are community-maintained outside the org entirely.
- **In-repo chart folder** — chosen when chart and code are developed together: airflow's official chart at [`chart/`](https://github.com/apache/airflow/tree/main/chart) (with its own README/RELEASE_NOTES inside the monorepo), dagster's at [`helm/dagster`](https://github.com/dagster-io/dagster/tree/master/helm/dagster).
- **In-repo IaC**: immich keeps OpenTofu + Terragrunt in [`deployment/`](https://github.com/immich-app/immich/tree/main/deployment) at the repo root (its `mise.toml` pins `opentofu = "1.12.6"`, `terragrunt = "1.1.6"`), separate from `docker/` (compose for end-users). None of the surveyed .NET product repos carry IaC in-repo — bitwarden/grafana/sentry keep it out of the main repo along with their charts.
- **No surveyed repo** puts a chart next to the individual service's code inside `src/` ("per-app chart beside the csproj"); in-repo charts always sit in a dedicated root folder.

### For a single-product monorepo with trunk CD specifically

No primary source forbids in-repo charts; the pro-separation arguments (Argo CD's) are explicitly about access control, multi-repo products, and CI-trigger loops — none of which apply to a one-team, one-product repo where GitHub Actions path filters already scope CI per area and the chart versions in lock-step with the app. The two patterns that *are* documented for our shape both co-locate: Flux's repo-per-app (code + manifests in one repo) and GitLab's manifests-in-the-project default. The observed trigger for moving charts out is third-party distribution of the chart (bitwarden, grafana, immich), not team size.

## 3. Folder-name casing

Observed per repo (structural/area folders vs. .NET project folders):

- **dotnet/runtime**: lowercase structural (`src`, `docs`, `eng`; areas `libraries`, `coreclr`, `mono`); PascalCase project folders inside (`src/libraries/Microsoft.CSharp/`, `System.*`) with lowercase leaves `src|ref|tests|gen` per project.
- **dotnet/aspnetcore**: lowercase root (`src`, `docs`, `eng`); PascalCase area and project folders inside `src/` (`src/Http/Http.Abstractions/`).
- **dotnet/eShop**: lowercase root (`src`, `tests`, `e2e`, `build`, `img`); PascalCase projects (`src/Basket.API/`, `src/eShop.AppHost/`).
- **dotnet/aspire**: lowercase root (`src`, `tests`, `docs`, `eng`, `tools`, `playground`); PascalCase projects (`src/Aspire.Dashboard/`).
- **bitwarden/server**: lowercase root (`src`, `test`, `util`, `dev`, `perf`; exceptions `AppHost/`, `bitwarden_license/`); PascalCase projects (`src/Api/`, `src/Core/`, `util/Migrator/`).
- **OrchardCMS/OrchardCore**: lowercase root (`src`, `test`, `tools`, `plans`); PascalCase projects (`src/OrchardCore.Modules/`).
- **jellyfin/jellyfin**: lowercase structural (`src`, `tests`, `deployment`, `fuzz`); PascalCase projects both at root (legacy) and under `src/` (`src/Jellyfin.Networking/`).
- **duplicati/duplicati**: counterexample — PascalCase structural folders (`BuildTools/`, `Tools/`, `Assets/`).
- Non-.NET comparisons (grafana, immich, sentry, airflow, dagster): all-lowercase throughout, since nothing there carries assembly names.

**Majority pattern, plainly**: lowercase for structural/area folders, PascalCase only for folders that *are* a .NET project/assembly name — 7 of the 8 .NET repos surveyed follow it; duplicati alone does not.

Official/reference guidance:

- **dotnet/runtime** `docs/coding-guidelines/project-guidelines.md` ("Directory layout"): `src\<Library Name>\gen|ref|src|tests` — project folders named after the (PascalCase) library, structural leaves lowercase ([doc](https://github.com/dotnet/runtime/blob/main/docs/coding-guidelines/project-guidelines.md)). The archived dotnet/corefx predecessor prescribes the same `src\<Library Name>\src|ref|pkg|tests` ([archived doc](https://github.com/dotnet/corefx/blob/master/Documentation/coding-guidelines/project-guidelines.md)). Neither doc covers whole-repo layout beyond this; there is no current official Microsoft "repo layout" spec.
- **David Fowler's gist** (community reference, not primary; ~1.5k stars, comments active through 2026): lowercase `src/`, `tests/`, `docs/`, `build/`, `artifacts/`, `samples/` at root ([gist](https://gist.github.com/davidfowl/ed7564297c61fe9ab814)) — consistent with everything Microsoft ships today.
- **Helm** forces lowercase chart directories via DNS-1123 chart naming ([conventions](https://helm.sh/docs/chart_best_practices/conventions/)).

## What this means for us

- **Root scheme**: the evidence splits by stack mix, not repo fame. Pure-.NET products converge on `src/` + lowercase siblings (bitwarden/server is the strongest single-product example); polyglot single-product repos — which is what we become the day the non-.NET frontend lands — converge on plain lowercase area folders at the root (immich being the closest analogue: `server/`, `web/`, `docs/`, `docker/`, `deployment/`). Both are well-attested; no surveyed repo mixes a .NET `src/` umbrella with sibling non-.NET app folders, though nothing forbids it.
- **Charts/IaC placement**: for a single product with trunk CD and no third-party chart consumers, the documented patterns that fit (Flux repo-per-app, GitLab manifests-in-project) and the in-repo precedents (airflow `chart/`, dagster `helm/`, immich `deployment/` for OpenTofu) favour a dedicated lowercase root folder in the monorepo; Argo CD's separate-repo advice is real but motivated by multi-team/fleet concerns we don't have. The observed reason to split later is distributing the chart to outside installers — a reversible, well-trodden move (bitwarden, grafana, immich all did it as separate repos).
- **Casing**: lowercase structural folders, PascalCase only for .NET project folders, is the near-unanimous convention (7/8 .NET repos + the runtime guidelines + the Fowler gist); Helm independently forces the chart folder lowercase.
