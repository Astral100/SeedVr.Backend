# Push vs pull job distribution to GPU workers

Research for the wayfinder planning effort. All claims cite primary sources: official docs of the named systems, first-party GitHub wikis/repos, vendor announcements, and this repo's own verified research/ADRs. Researched 2026-09-05.

**Verdict: the decision-maker's instinct is correct — worker-pull is the industry standard for worker fleets you operate yourself. Every classic job queue, every workflow engine, and every CI runner surveyed here pulls, for good, stated reasons (NAT traversal, backpressure, no worker bookkeeping). But every GPU-serverless platform surveyed presents a *push* interface to the customer and keeps its pull loop (where it has one) internal — and the resolved design (ADR 0003) outsources exactly the fleet-operation layer where pull earns its keep. Vast.ai serverless offers no pull mode for customer payloads: `/route/` + direct POST to the worker is the only path. So at our layer, pull is not an available alternative under the current platform choice; it is a package deal with reopening ADR 0003 and running a DIY pool as primary. Within that DIY world pull would be a legitimate, standard shape — but on rented marketplace hosts it forfeits ADR 0005's zero-credential property: a pulling worker must hold a standing credential whose blast radius (read other users' queued payloads, claim and forge other jobs) is strictly worse than push's per-job containment. Details in §6–§8; even-handed table in §9.**

---

## 1. Two layers, two questions

"Push vs pull" is ambiguous until you fix the layer. In this system there are two:

- **Platform layer** — how a GPU process obtains work from whatever sits directly above it. This is where the industry-standard pull pattern lives (Celery workers consuming a broker, Temporal workers long-polling).
- **Our layer** — how the .NET control plane hands a job to the GPU-provisioning system. Under ADR 0003 that system is Vast.ai serverless: the backend calls `POST run.vast.ai/route/`, receives a ready worker URL, and POSTs the job to it — push ([ADR 0003](../adr/0003-serverless-primary-worker-orchestration.md); wire protocol verified from SDK source in [vastai-serverless.md](vastai-serverless.md) §1).

The survey below shows the pattern: systems that *operate* worker fleets use pull internally; systems that *sell* GPU execution expose push (an HTTPS request) to the customer and hide their internal scheduling. The real question for this repo is which side of that line we stand on — and ADR 0003 already answered it by outsourcing fleet operation.

## 2. Classic job-queue workers: pull, universally

- **Celery** — "To initiate a task the client adds a message to the queue, the broker then delivers that message to a worker"; "dedicated worker processes constantly monitor task queues for new work"; scale-out is "multiple workers and brokers, giving way to high availability and horizontal scaling." https://docs.celeryq.dev/en/stable/getting-started/introduction.html
- **Sidekiq** — "Sidekiq uses BRPOP to fetch a job from the queue in Redis" — a blocking pull; the same page documents pull's classic weakness: "if Sidekiq crashes while processing that job, it is lost forever," fixed by the paid `super_fetch` strategy (Redis `LMOVE`, job stays in Redis until complete). https://github.com/sidekiq/sidekiq/wiki/Reliability
- **BullMQ** — workers consume jobs from Redis queues; "BullMQ will place a lock on this job to protect the job from being modified by any other client or worker" and "the worker needs to periodically notify BullMQ that it is still working on the job," else the job is marked stalled and redelivered. https://docs.bullmq.io/guide/workers
- **Hangfire** (the .NET native) — "workers listen to queue and process jobs." https://docs.hangfire.io/en/latest/background-processing/processing-background-jobs.html

Why pull won in this class:

- The broker is the single coordination point — producers know nothing about consumers, and scale-out is "start another worker" (Celery quote above).
- Consumers take work only when free; nobody schedules onto a busy box.
- Crash recovery reduces to redelivery/lock-expiry (BullMQ stall handling; Sidekiq `super_fetch`) — though the §2 quotes show even this needs care: naive pull *loses* jobs, and the fix is lease machinery.
- Note the load-bearing assumption everywhere in this class: **the operator owns and trusts every worker box**, and every worker holds a standing broker credential. That assumption is what §6 tests against marketplace hardware.

## 3. GPU-serverless platforms: customer pushes; the platform's pull is hidden

For each platform, two facts: what the *customer's backend* does, and what the *worker* does.

- **RunPod serverless** — customer: "send a `POST` request to submit a job" to the endpoint (`/run`, `/runsync`). Worker: queue-based endpoints put the request in "a managed queue… The request is queued until a worker is available. A worker processes the request using your handler function" — platform-internal queue consumption; the alternative "load-balancing endpoints route incoming traffic directly to available workers" (platform-side push). Either way the customer only ever pushes to RunPod's HTTPS API. https://docs.runpod.io/serverless/overview
- **Replicate** — customer POSTs a prediction to the HTTP API; "async mode returns immediately with a prediction ID"; completion via polling ("making repeated API requests to fetch the prediction") or "the webhook URL provided will be called with the final prediction data." The scheduling between Replicate and its GPUs is not customer-visible at all. https://replicate.com/docs/topics/predictions/create-a-prediction
- **Modal** — customer invokes deployed functions via `.remote()` / `.spawn()` ("spawn a background execution and poll its status") or plain HTTPS ("Modal Web Functions can be invoked via HTTPS at a public URL"); every path is mediated by Modal's control plane — the docs expose no direct-to-container contact and no customer-consumable queue. https://modal.com/docs/guide/trigger-deployed-functions
- **Banana** — dead; sunset announced 2024-02-01, infrastructure off 2024-03-31, citing GPU-shortage unit economics and the cost of maintaining scale-to-zero infrastructure. Included as a maturity datum for the young-platform risk class, not as an architecture datum. https://www.banana.dev/blog/sunset
- **beam.cloud** — customer deploys a `task_queue` and submits work via a REST call; "because task queues run asynchronously, the API will return a Task ID," results fetched from a `/task` endpoint; failed tasks retried three times by the platform. Again: customer pushes to the platform; containers consume the platform's queue. https://docs.beam.cloud/v2/task-queue/running-tasks
- **Vast.ai serverless** — the odd one out: the platform does *not* even hide a queue behind a plain POST. The customer's backend calls `/route/` (which may queue the *routing request* until a worker is ready) and then POSTs the payload **directly to the worker's URL**; the worker-side PyWorker "monitors the inference server's readiness, proxies incoming requests, and coordinates with the autoscaler" — it reports metrics up (pull-shaped control traffic) but never fetches customer payloads from anywhere. Verified from SDK source and docs in [vastai-serverless.md](vastai-serverless.md) §1; https://docs.vast.ai/guides/serverless/architecture

So the industry pattern at *our* layer is unanimous: the customer backend pushes an HTTPS request at the platform and correlates completion by ID + webhook — exactly the shape ADR 0003 resolved.

## 4. Workflow engines and CI runners: pull, with stated reasons

- **Temporal** — "Workers poll for Tasks in Task Queues via synchronous RPC." Stated benefits: "a Worker Process polls for a message only when it has spare capacity, avoiding overloading itself" (backpressure); "Task Queues enable load balancing across many Worker Processes"; "Worker Processes do not need to advertise themselves through DNS or any other network discovery mechanism"; they "connect directly to the Temporal Service… without needing to open exposed ports." https://docs.temporal.io/task-queue
- **GitHub Actions self-hosted runners** — the runner uses "an HTTP(S) long poll that opens a connection to GitHub for 50 seconds" and retries; "only an outbound connection from the runner… is required, with no need for an inbound connection" — the whole design exists so runners can sit behind NAT/firewalls. https://docs.github.com/en/enterprise-cloud@latest/actions/concepts/runners/communicating-with-self-hosted-runners
- **GitLab Runner** — the runner initiates `POST /api/v4/jobs/request with runner_token` against the GitLab API to obtain jobs; GitLab never connects in. https://docs.gitlab.com/runner/
- **Buildkite** — "the agent works by polling Buildkite's agent API over HTTPS. There is no need to forward ports or provide incoming firewall access." https://buildkite.com/docs/agent/v3

Common thread: heterogeneous, customer-owned machines on networks the coordinator cannot reach into. Pull is the only workable topology there. On Vast, that constraint is already dissolved by the platform: every worker gets a publicly reachable proxied URL — inbound-to-worker is a solved problem we did not have to solve ([vastai-serverless.md](vastai-serverless.md) §1, §7 port mappings).

## 5. Generic trade-offs, grounded

**Pull's genuine advantages:**

- No inbound reachability to workers needed — the entire motivation of §4's CI/workflow designs. Moot here: Vast already gives every worker a publicly reachable proxied URL (§4).
- Natural backpressure: an idle worker takes work exactly when it has spare capacity (Temporal quote, §4). Vast approximates this by routing only to Ready workers, but the placement decision is the platform's, not a queue's.
- No worker registry/liveness bookkeeping in the control plane: retry-on-crash is broker redelivery — SQS: an undeleted message "becomes visible again in the queue and can be retrieved by the same or a different consumer." https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- Scale-out = start another consumer; producers need no topology knowledge (Celery, §2).

**Pull's real costs:**

- Every worker holds a standing credential to our broker/API (§6), and the queue endpoint must be network-reachable *from* every marketplace host.
- Long jobs need lease machinery. SQS's own best practice for exactly our job shape: "implement a heartbeat mechanism to periodically extend the visibility timeout" via `ChangeMessageVisibility`, under a hard 12-hour ceiling (same page — fine for 10–30+ min jobs, but the heartbeat loop is ours to write). BullMQ requires periodic lock renewal or the job is "marked as Stalled" and redelivered mid-run (§2). Sidekiq's stock BRPOP simply loses the job on worker crash; the durable variant is a paid feature (§2).
- Poison-message handling needs dead-letter plumbing: SQS redrive policy/DLQ (same page); NATS JetStream's DLQ is an advisory-capture DIY pattern ([message-brokers.md](message-brokers.md) §2–§3).
- A duplicate-suppression story: at-least-once delivery means a redelivered 20-minute upscale re-runs unless idempotency keys guard it (SQS: "no absolute guarantee that a message won't be delivered more than once," same page).

**Push's genuine advantages:**

- The dispatcher chooses *which* worker. Placement by machine-speed priors matters on a marketplace with a measured ~40% same-clip speed spread between hosts ([vastai-serverless.md](vastai-serverless.md) §7); a shared queue hands the job to whichever consumer asks first.
- Zero polling traffic; immediate dispatch when a worker is Ready (a `/route/` answer carries the worker URL at once — [vastai-serverless.md](vastai-serverless.md) §1).
- No standing credential of ours parked on the worker (§6) — the property ADR 0005 just resolved for storage extends to dispatch.
- Decisive at this layer: the platform already provides routing, benchmarking, and Ready-worker bookkeeping — push is one HTTPS call into machinery we do not maintain ([ADR 0003](../adr/0003-serverless-primary-worker-orchestration.md)).

**Push's real costs:**

- The control plane owns dead-worker retry and stall detection: the SDK contract re-routes on connection failure, and the "no update for T seconds" detector is ours to build with phase-aware timeouts ([vastai-serverless.md](vastai-serverless.md) §1; [serverless-live-progress.md](serverless-live-progress.md) §6).
- Dispatch/crash-recovery state lives in our Postgres job table rather than a broker's ack ledger — redelivery is our code, not a visibility timeout. (Mitigating fact: per [message-brokers.md](message-brokers.md) §1, `JobState` in Postgres stays the source of truth in *every* candidate design, so this table exists regardless.)
- Dispatch latency degrades to polling anyway when no worker is Ready: the WAITING branch of `/route/` is a jittered-backoff poll loop ([vastai-serverless.md](vastai-serverless.md) §1) — push's "immediate dispatch" holds only while the warm floor holds.

## 6. Security on untrusted hardware — the crux

ADR 0005's premise: Vast marketplace hosts have root on the Docker host and can read anything a container holds, so workers carry no storage credentials; per-job presigned URLs bound blast radius to one object, one operation, one time window ([ADR 0005](../adr/0005-workers-hold-no-storage-credentials.md)).

A pulling worker cannot be credential-free: to fetch work it must authenticate to our broker or API. Every §2/§4 system assumes this (broker password, GitLab `runner_token`, Buildkite agent token). An honest look at how narrow such a credential can be made:

- **Subject-scoped broker accounts.** NATS grants per-user publish/subscribe allow/deny lists on specific subjects ("a permission is a grant to publish to, or subscribe to, a set of subjects"; "deny beats allow") — https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization. Per-worker broker users are therefore practical. But a *work queue* is by nature a shared subject: any consumer permitted to consume `jobs.>` can receive **any** queued job — and our job payloads carry presigned input URLs of other users' videos plus presigned output PUT URLs ([ADR 0005](../adr/0005-workers-hold-no-storage-credentials.md)). Scoping delivery per worker (`jobs.worker-17.*`) requires the control plane to decide which worker gets which job *before* publishing — which is push wearing a broker costume.
- **Short-lived JWTs / mTLS / rotating per-worker API tokens** (the GitLab/Buildkite shape). These bound credential lifetime and identify the caller, but the authorization question is unchanged: a "give me work" endpoint must hand the caller *some* job it has not yet seen. A compromised host can run the loop honestly — harvest every payload it is dealt, claim jobs and post plausible forged results — and its requests are indistinguishable from a healthy worker's. Rate limits, attestation, and canary jobs are all buildable mitigations, and all machinery.
- **Push's blast radius, fairly stated.** The current design is push-to-worker *and* worker-pushes-back: workers already POST progress and a completion webhook to our backend, authenticated per job (HMAC-signable webhook, per-job `request_id` — [serverless-live-progress.md](serverless-live-progress.md) §4–§5). So "workers never reach into our infrastructure" is false in both designs, and inbound-to-backend auth must exist either way. The push design also trusts Vast's routing layer (`/route/` signatures cover only the worker URL, no expiry — [serverless-live-progress.md](serverless-live-progress.md) §1), a dependency pull-from-us would not have.
- **The asymmetry that decides it.** A compromised *pushed-to* worker knows the one job it was handed and can forge status for that job only. A compromised *pulling* worker holds a standing credential into the dispatch fabric: it can drain payloads for jobs it was never meant to see and forge results for any job it claims. On hardware where host-operator compromise is the ADR-acknowledged baseline, containment-per-job versus access-to-the-queue is a categorical difference, not a matter of degree.

## 7. What pull would concretely require here

**Shape (a): DIY pool as primary, workers pull from us.** Drop serverless dispatch; the `ManagedPoolDispatcher` fallback becomes the product: our control plane searches offers, creates instances from the template, and each worker image gains an agent that long-polls our API or consumes a broker queue. The bill:

- We take over everything the platform's autoscaler does today — recruitment, benchmarking-out slow hosts, warm-pool sizing, retirement, retry-on-dead-worker — the exact list ADR 0003 rejected rebuilding ([vastai-serverless.md](vastai-serverless.md) §5; [ADR 0003](../adr/0003-serverless-primary-worker-orchestration.md)). What we save: the young-platform risk and the `/route/` protocol client.
- A broker or job-request endpoint exposed to arbitrary marketplace IPs, where today's broker candidates were all evaluated for an internal-only topology — "the only consumer is the backend's own dispatcher — GPU workers speak HTTPS both ways and never touch the broker" ([message-brokers.md](message-brokers.md) §1, §12).
- Per-worker credential issuance/rotation/revocation, lease-heartbeat, idempotency, and poison-job machinery (§5, §6), and an amendment to ADR 0005's zero-credential principle.
- What pull genuinely buys in this shape: dispatch bookkeeping shrinks (redelivery and fair distribution come from the broker), and a mid-job control-plane restart is a non-event for workers mid-poll. Real, but small against the fleet-ops line above.

**Shape (b): keep Vast serverless, workers pull anyway.** Not available. The platform's only customer-payload path is `/route/` + direct POST to the worker URL; PyWorker registers HTTP handler routes and forwards POSTed payloads to the local model server — there is no mechanism by which a serverless worker consumes a customer-side queue, and no such feature in the docs, SDK, or PyWorker source ([vastai-serverless.md](vastai-serverless.md) §1; [serverless-live-progress.md](serverless-live-progress.md) §1; https://docs.vast.ai/guides/serverless/architecture). One could bolt a queue-consuming sidecar into our custom image and ignore the serverless routing — but then the autoscaler's load accounting sees no requests, scales the "idle" pool away mid-job, and the platform's benchmarking/routing value is discarded while its constraints remain. Shape (b) collapses into shape (a) with extra steps.

## 8. The hybrid reality

The resolved design is not purely push, and no surveyed system is purely either:

- Our dispatch is push (backend → `/route/` → worker POST), but progress and completion are worker-initiated pushes *back to us* (completion webhook + the planned `_update_progress` hook — [serverless-live-progress.md](serverless-live-progress.md) §4–§5).
- Below the waterline, Vast's own control traffic is pull-shaped: PyWorker reports readiness and metrics up to the autoscaler ([vastai-serverless.md](vastai-serverless.md) §1; https://docs.vast.ai/guides/serverless/architecture), and the SDK's `/route/` WAITING branch is effectively our backend long-polling the platform's queue for a worker slot.
- Conversely, every pull system in §2 has a push edge: *someone* enqueues, and the enqueue API is a push interface.

The industry's GPU platforms (§3) all draw the line in the same place: **the queue and its consumers live inside the trust boundary of whoever operates the fleet; customers push requests across the boundary and correlate by ID.** So the precise question was never "push or pull?" but "which side of the fleet boundary do we stand on?" — and ADR 0003 already put fleet operation on Vast's side. Our push follows from that placement, not from a belief that push beats pull in general.

## 9. Summary table — pull vs push for this system

| Criterion | Pull (workers consume our queue/API) | Push (resolved: `/route/` + POST) |
|---|---|---|
| Industry precedent at this layer | Standard for self-operated fleets (§2, §4) | Standard for customers of GPU platforms (§3) |
| Available under Vast serverless | No — no customer-payload pull mode (§7b) | Yes — the platform's only mode |
| Fleet ops (recruit/benchmark/scale) | Ours to build and run (§7a) | Platform's ([ADR 0003](../adr/0003-serverless-primary-worker-orchestration.md)) |
| Worker reachability | Not needed — but already provided by Vast proxy URLs, so no win here (§4) | Needed; platform-solved |
| Backpressure / placement | Free-when-idle consumption; no placement control | Platform routes to Ready workers; speed-prior placement possible (§5) |
| Crash retry / redelivery | Broker-native, plus lease-heartbeat + DLQ machinery for 10–30+ min jobs (§5) | Ours: re-route on failure + stall detector (already designed) |
| Credential on untrusted host | Required; standing; queue-wide read + forge blast radius even when scoped (§6) | None beyond per-job token/URLs; blast radius = own job (§6, ADR 0005) |
| Control-plane bookkeeping | Less dispatch state; broker becomes worker-facing infrastructure | Job table + dispatcher state; broker (if any) stays internal-only |
| Cost of switching now | Reopen ADR 0003 + amend ADR 0005 + build fleet ops (§10) | Zero — resolved and partially verified live |

## 10. Verdict

Pull is the industry standard **for worker fleets you operate yourself** — that instinct is right, and the survey confirms it without exception at that layer (§2, §4). But the resolved architecture outsources exactly that layer: at our layer, the industry standard is what every GPU-serverless customer does — push a request at the platform, correlate by ID, receive a webhook (§3).

The two strongest system-specific arguments land on the same side:

1. **Availability**: Vast serverless has no customer-payload pull mode (§7b), so choosing pull is not an architecture tweak — it is dropping the platform and re-buying fleet operations that ADR 0003 explicitly declined to build.
2. **Security**: on hardware whose host operator is the ADR-acknowledged adversary, pull's mandatory standing credential has a categorically wider blast radius (any queued job's presigned URLs, forgeable results for claimed jobs) than push's per-job containment — it would amend the just-resolved ADR 0005 in the wrong direction (§6).

Meanwhile pull's classic advantages are, in this specific system: already solved by the platform (worker reachability), already outsourced (discovery, scaling, backpressure-ish routing), or offset by lease/heartbeat/idempotency machinery at 10–30+ min job durations (§5). **Keep push-dispatch.**

The honest caveats the decision-maker should hold onto:

- If the young platform disappoints and the `ManagedPoolDispatcher` fallback is promoted to primary, re-run this question: inside a self-operated pool, worker-pull is the conventional shape, and the choice becomes genuinely close — with the §6 credential problem (which marketplace hardware never stops posing) as the main reason a DIY pool here might still deviate from convention and push over Vast's proxy URLs.
- Push's operational costs are real and already on our plate: the stall detector, dead-worker re-route, and Postgres-based crash recovery are our code (§5). Choosing push is choosing to own those instead of a broker's redelivery semantics — at this system's scale (≤~30–40 concurrent jobs, [message-brokers.md](message-brokers.md) §1), that is a modest, bounded build.

## 11. What would need reopening if pull is chosen

- **[ADR 0003](../adr/0003-serverless-primary-worker-orchestration.md)** — superseded: serverless cannot be primary under pull (§7b); the DIY pool becomes the build, including recruitment/benchmarking/warm-floor scaling it explicitly rejected rebuilding.
- **[ADR 0005](../adr/0005-workers-hold-no-storage-credentials.md)** — amended: workers hold a dispatch credential; its scoping, rotation, and blast-radius story must be designed and documented (§6).
- **[message-brokers.md](message-brokers.md)** — re-scoped: the entire evaluation assumed an internal-only, single-consumer topology (§1, §12); a worker-facing broker changes the security bar, the hosting exposure, and possibly the shortlist ranking.
- **[serverless-live-progress.md](serverless-live-progress.md)** — largely moot: with direct network access to self-managed instances, the wrapper's own routes are reachable and the PyWorker fork machinery is unnecessary.
- **Dispatcher-seam tickets** — the `IUpscaleDispatcher` seam survives in name, but the "identical job contract in both worlds" premise weakens: a pull design moves dispatch out of the interface's request/response shape into queue semantics.
- **Vast account/scaling policy decisions in ADR 0003** (warm floor, On-Demand-only, auto-top-up) — re-decided for a self-managed pool.

## 12. Addendum (2026-09-05): verification against the challenged claims

Three claims were challenged: "the only customer-payload path is `/route/` + POST" (§3, §7b), "every GPU-serverless platform draws the same line" (§8), and the standing-credential asymmetry (§6). Verified against docs.vast.ai (serverless section, fetched 2026-09-05), the `vast-ai/vast-sdk` source (main @ `1c39c42`, `vastai/serverless/server/`), `vast-ai/pyworker` (main @ `2207a3f`), and docs.runpod.io.

### 12.1 What the engine manages, and what feeds it (claim 1a–b)

- The Serverless Engine decides "when to recruit new workers, activate inactive workers, release or destroy workers", and workers "report operational and performance metrics back to the Serverless Engine, which uses this data to make ongoing scaling decisions"; routing "returns a suitable worker address based on current load and capacity". https://docs.vast.ai/guides/serverless/architecture
- The metrics payload, verified from source: PyWorker POSTs a `WorkerStatusData` — `id, mtoken, version, loadtime, cur_load, rej_load, new_load, error_msg, max_perf, cur_perf, num_requests_working, num_requests_recieved, working_request_idxs, url, …` — to `{REPORT_ADDR}/worker_status/` every 1–10 s (`Metrics.__send_metrics_and_reset` / `_send_metrics_loop`, `server/lib/metrics.py`; `WorkerStatusData` in `server/lib/data_types.py`, docstring "Data that is reported to autoscaler's on_status endpoint"; auth = `MASTER_TOKEN` env var, `server/lib/backend.py`).
- **Where load is incremented — the crux**: `cur_load` is `sum(request.workload for request in requests_working)` (`ModelMetrics.cur_load`, `data_types.py`), and entries reach `requests_working` only via `Metrics._request_start`, called from exactly two places: `Backend.__handle_request` (a registered handler route, *after* the `auth_data` signature check passes) and `session_create_handler`. `workload` comes from `payload.count_workload()` — the `workload_calculator`, documented as "the key input to autoscaling" (https://docs.vast.ai/guides/serverless/creating-new-pyworkers). **A worker executing work that never arrived through a handler route reports `cur_load = 0` — the platform sees it as idle.** This confirms §7b's premise for a stock PyWorker.
- What survives without routed traffic: recruitment, readiness detection (log-tail + `model_is_loaded`), and benchmarking (`max_perf` from the startup benchmark, `Backend.__run_benchmark`) are all independent of customer requests. What goes blind: utilization-driven scale-up/down and routing — both keyed to reported per-request load.

### 12.2 Synthetic load: constructible in our fork, supported nowhere (claim 1c, 1e)

The challenger's premise is correct as far as it goes: **all load accounting is self-reported by customer-run code** — the platform structurally trusts the fork. A fork could inflate `cur_load` for a pulled job (insert a synthetic `RequestMetrics`; or POST its own `/session/create`, which — notably — never calls `__check_signature` and takes `workload` from the client-supplied `auth_data.cost`, `backend.py`). Sessions are in fact the one *documented* "hold me busy without an active request" primitive (a session "reports the session's cost as in-flight load", [serverless-live-progress.md](serverless-live-progress.md) §1; https://docs.vast.ai/guides/serverless/sessions) — but they are created by a routed client, not by the worker. Against that:

- The engine keeps a per-request ledger: `request_idx` values are issued by `/route/`, echoed back in `working_request_idxs`, and retired via `{REPORT_ADDR}/delete_requests/` (`metrics.py` `__send_delete_requests_and_reset`). Fabricated load carries idxs the autoscaler never issued; how the server treats them is undocumented and unverified.
- `/pyworker/update` is not a load-reporting surface: it only writes a `/.force_update` marker file to trigger a worker self-update, authenticated by the same `MASTER_TOKEN` (`Backend.pyworker_update_handler`, `backend.py`). No other worker-initiated "I am busy" API exists in server, SDK, or docs.
- Even if faked load were accepted server-side, routing is "based on current load and capacity" (architecture doc) — a worker holding its `cur_load` high to survive scale-down would thereby also repel routed requests, i.e. the fake works only by neutralizing the routing layer being paid for.
- A deliberate sweep of the serverless docs (architecture, serverless-parameters, managing-scale, worker-states, sessions, creating-new-pyworkers, plus web search) finds **no mention** of workers fetching their own work, external queues, batch/job-queue mode, or custom scaling signals — pull is neither recommended against nor supported; it is simply never brought up (answering 1e).

**Correction to §7b**: "there is no mechanism by which a serverless worker consumes a customer-side queue" overstates — the mechanism is constructible in our fork, since load reporting is our code. The accurate statement is *unsupported, undocumented, and dependent on faking a ledger-tracked metrics contract*. §7b's conclusion (shape (b) collapses into shape (a) with extra steps) stands: a load-faking pull worker suppresses the routing/placement value that is the platform's point, and rests mid-job survival on unverified server behavior.

### 12.3 Scale-down selection (claim 1d)

No selection rule is documented anywhere: the engine "release[s] or destroy[s] workers" from reported metrics (architecture doc); `inactivity_timeout` governs endpoint-level scale-to-zero ("how long the endpoint must be idle before scaling down is permitted", https://docs.vast.ai/guides/serverless/managing-scale); `target_util` sizes maintained capacity against "measured load" (https://docs.vast.ai/guides/serverless/serverless-parameters); worker-states documents states, not triggers (https://docs.vast.ai/guides/serverless/worker-states). The only per-worker busy signal the engine receives is self-reported `cur_load` (§12.1) — so a pulling worker mid-job is indistinguishable from an idle one, and §7b's "scales the 'idle' pool away mid-job" is the correct default expectation, though it is an inference from the signal set, not a documented guarantee.

### 12.4 Claim 2 — RunPod: never brought up, not recommended against

RunPod's serverless docs describe the managed-queue handler path ("The request is queued until a worker is available. A worker processes the request using your handler function") and load-balancing endpoints; a worker fetching work from a customer-side queue is neither prohibited, discouraged, nor mentioned — the handler/queue model is simply the only documented path (https://docs.runpod.io/serverless/overview). **Correction to §3/§8's framing**: "every GPU-serverless platform draws the same line" is accurate as a description of documented interfaces, but should not be read as vendor guidance *against* pull — since handlers are customer code there too, the platforms are silent on it, exactly as Vast is (§12.2).

### 12.5 Claim 3 — the standing credential, stated precisely

- **(a) Serverless**: workers are created by the autoscaler from the workergroup's template; env vars live at template level and are identical across workers, with no customer per-worker injection hook at creation (https://docs.vast.ai/guides/serverless/workergroup-parameters, https://docs.vast.ai/guides/templates/creating-templates — which also warns "Never put sensitive information (passwords, API keys) in template environment variables if you plan to make the template public"). `PROVISIONING_SCRIPT` runs on the instance at creation and *could* fetch per-worker credentials from our backend at boot — but whatever authenticates that bootstrap call is itself baked into the fleet-shared image/template and host-readable, and Vast documents no attestation primitive to break the regress. Per-worker credentials on serverless therefore reduce to a fleet-shared bootstrap secret plus issuance-side heuristics (rate limits, instance-list cross-checks) — machinery, not containment.
- **(b) DIY pool**: per-instance env vars are confirmed — create-instance's `env` is "merged with template `env` … conflicting keys use the request value" (https://docs.vast.ai/api-reference/instances/create-instance) — so unique, individually revocable per-worker tokens exist in that world; each is still readable by that one machine's host operator.
- **(c) Blast radius, fairly**: §6's "a compromised pushed-to worker knows the one job it was handed" understates — over its lifetime a pushed-to worker receives every job the dispatcher assigns it (at ~40 workers, on the order of 1/N of traffic), other users' presigned URLs included. The real asymmetry is **rate and selection** — a puller actively claims jobs as fast as the queue serves them and can claim selectively; a pushed-to worker gets only what the dispatcher chooses, at the dispatcher's pace, and placement-by-prior can quarantine a suspect host — and **credential lifetime/scope**: pull requires a secret that exists before any job does and (on serverless, per (a)) is fleet-shared, so one compromised host means fleet-wide re-key; push parks nothing standing, so containment is per-job even across a long-lived compromise.

### Net effect on the §10 verdict

The verdict stands, with argument 1 restated rather than removed. **Availability** softens from "no pull mode exists" to: pull-on-serverless is technically constructible (load reporting is our code) but unsupported and undocumented — it either lets undocumented scale-down reap idle-looking workers mid-job (§12.3) or fakes a ledger-tracked metrics contract with unverified server behavior (§12.2), while discarding the routing/benchmark placement value that justifies the platform. That is still not a real alternative; it is shape (a) wearing shape (b)'s billing. **Security** (claim 3) survives the fairness correction: the categorical difference is standing-fleet-shared-secret vs nothing-standing, plus claim-at-will vs dispatcher-paced assignment — narrower wording than §6's, same direction. Keep push-dispatch.
