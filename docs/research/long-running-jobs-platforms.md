# Long-running GPU jobs on serverless platforms — RunPod vs Vast.ai

Research for the live architecture question: our jobs are single video upscales, ~10–30+ minutes each, one job per GPU; Vast.ai serverless was observed to be "not very advanced" for long jobs. Does RunPod serverless account for them materially better? Primary sources: docs.runpod.io, runpod.io pricing pages, github.com/runpod-workers, modal.com/docs, replicate.com/docs, plus the repo's own Vast findings ([vastai-serverless.md](vastai-serverless.md), [serverless-live-progress.md](serverless-live-progress.md), [push-vs-pull-workers.md](push-vs-pull-workers.md) §12). Researched 2026-09-05.

**Verdict: yes, RunPod's job model is materially more long-job-aware than Vast's — a platform-held queue with fire-and-forget submission, a 7-day execution-timeout ceiling, platform-side liveness (a silent worker times the job out instead of reporting busy forever), same-ID retry, a built-in progress channel, and webhook retries. But we have already designed around every one of those gaps (webhook + S3 completion, wrapper-fork progress push, backend-held queue, stall detection), so RunPod would mostly buy us deletion of machinery, not new capability — and at roughly 3× the GPU rate of Vast marketplace on-demand, with a fixed GPU catalog and an image-centric ComfyUI worker we'd fork anyway. Recommendation: keep ADR 0003; treat RunPod as a named alternative dispatcher behind the existing seam, not a replacement. Details in §9–10.**

---

## 1. RunPod's job model: a real platform-side queue

RunPod serverless has two endpoint types; the relevant one is **queue-based**: requests "process ... sequentially through a managed queue" with "guaranteed execution and automatic retries", using handler functions and the `/run`//`/runsync` operations. Flow: "The request is queued until a worker is available. A worker processes the request using your handler function"; if no worker exists, "Runpod automatically starts one (cold start)". Jobs progress through states "IN_QUEUE, RUNNING, COMPLETED". Source: https://docs.runpod.io/serverless/overview

- **`/run` (async)** — returns a job id immediately; "The job processes in the background; retrieve results via `/status`". Results retained **30 minutes after completion**. Recommended for "long-running tasks and batch processing". **`/runsync`** blocks; results retained 1 minute (5 with `?wait=t`). Source: https://docs.runpod.io/serverless/endpoints/operations
- **Job states** (documented enum): IN_QUEUE, IN_PROGRESS/RUNNING, COMPLETED, FAILED, CANCELLED, TIMED_OUT — where TIMED_OUT means "the job either expired before it was picked up by a worker **or the worker failed to report back** before reaching the timeout threshold". Source: https://docs.runpod.io/serverless/references/job-states
- **`/retry`** — "Requeue a failed or timed-out job with the same job ID and input" (POST `/v2/{endpoint}/retry/{job_id}`; only FAILED/TIMED_OUT jobs; impossible once the result-retention window has lapsed). Source: https://docs.runpod.io/serverless/endpoints/operation-reference
- Also: `/cancel/{id}`, `/purge-queue`, `/health` (worker/job statistics, including a retried-jobs metric), `/stream` ("Receive incremental results as they become available"). Sources: https://docs.runpod.io/serverless/endpoints/operation-reference , https://docs.runpod.io/serverless/endpoints/operations

## 2. Duration ceilings: long jobs are first-class

- **Execution timeout**: default **600 s** ("Keep enabled to prevent runaway jobs" — cannot be disabled), configurable **5 s to 7 days**, both per endpoint and per job (`policy.executionTimeout` in the request "overrides the default endpoint setting for that specific job only"). Source: https://docs.runpod.io/serverless/endpoints/endpoint-configurations , https://docs.runpod.io/serverless/endpoints/send-requests
- **Job TTL**: total lifespan from *submission* (queue time counts), default 24 h, max 7 days; "TTL is a hard limit. If TTL expires while a job is running, the job is immediately removed." Per-job `policy.ttl`. Source: https://docs.runpod.io/serverless/endpoints/send-requests
- So a 30-minute upscale needs one line of per-job policy (`executionTimeout: 2_400_000` or similar) and is otherwise unremarkable to the platform. Contrast Vast: there is no platform-side execution timeout at all — the 600 s figure there is merely the Python SDK's default client-side HTTP timeout on the held-open worker POST ([vastai-serverless.md](vastai-serverless.md) §1, §6 caveat 4).

## 3. Where the job lives; fire-and-forget

On RunPod the queue is the platform's: the backend POSTs `/run`, stores the job id, and disconnects — placement, queueing, cold-starting a worker, and timeout enforcement are all platform-side. `/status/{id}` answers throughout (and for 30 min after completion); a top-level `webhook` URL is called on completion, and "If the call fails, Runpod retries up to 2 more times with a 10-second delay". Payload caps exist (community worker docs cite ~10 MB `/run` / 20 MB `/runsync` — https://github.com/runpod-workers/worker-comfyui), so bytes still travel via S3-compatible storage exactly as in our design. Sources: https://docs.runpod.io/serverless/endpoints/operations , https://docs.runpod.io/serverless/endpoints/send-requests

On Vast there is **no platform queue**: an unplaceable job stays in the customer backend, which re-polls `/route/` with a `request_idx`; the job is carried by a held-open HTTP POST to the worker, and completion is durable only because our wrapper does webhook + S3 ([vastai-serverless.md](vastai-serverless.md) §1; [push-vs-pull-workers.md](push-vs-pull-workers.md) §12.1).

## 4. Progress and mid-job health

- **Progress**: `runpod.serverless.progress_update(job, message)` from inside the handler; "Progress updates will be available when the job status is polled" — i.e. a built-in, platform-hosted progress field on `/status`. No cadence limits documented. Streaming handlers (`yield` + `/stream`) exist for incremental results. Source: https://docs.runpod.io/serverless/workers/handlers/handler-additional-controls , https://docs.runpod.io/serverless/workers/handlers/overview
- **Liveness**: the TIMED_OUT definition ("the worker failed to report back before reaching the timeout threshold", §1) means a crashed or wedged worker surfaces as a platform-visible job failure, retryable with the same id. On Vast, busy is self-reported in-flight load; a hung worker reports busy forever and nothing platform-side ever notices ([push-vs-pull-workers.md](push-vs-pull-workers.md) §12.1, §12.3).
- Caveat: the *automatic* retry semantics behind "guaranteed execution and automatic retries" (count, triggers) are not precisely documented — the troubleshooting page documents unhealthy-worker scale-down but not requeue rules (https://docs.runpod.io/serverless/troubleshooting); only the manual `/retry` contract is exact. Treat at-least-once as directional, not a verified guarantee, and note our jobs are not idempotent-free (re-running an upscale is safe but costs a GPU-hour).

## 5. Worker contract and scaling with 10–30 min jobs

The handler receives `{"id", "input": {...}}` from RunPod's internal queue (worker-pull at the platform layer — the customer never routes to a worker URL and no client connection is held open to the worker). Uncaught exceptions mark the job FAILED with details in the result; `refresh_worker: True` in the return wipes worker state after a job — a clean fix for VRAM-latch-style poisoning that Vast's PyWorker has no equivalent for ([vastai-serverless.md](vastai-serverless.md) §7). Source: https://docs.runpod.io/serverless/workers/handlers/overview , https://docs.runpod.io/serverless/workers/handlers/handler-additional-controls

Scaling: **queue-delay** strategy (add workers when requests wait > ~4 s) or **request-count** (`ceil((inQueue+inProgress)/scalerValue)`); max workers sized ~"20% higher than expected max concurrency"; up to three GPU types in priority order per endpoint; scale-to-zero with **FlashBoot** ("reduces cold starts by retaining worker state after spin-down"); idle timeout default 5 s, billed. With 30-minute serial jobs both strategies degenerate to "one worker per queued job", which is fine; the failure mode to configure against is over-eager scale-up on a burst of long jobs. Billing is per-second "from worker start to full stop" — start (model load) time is billed, as on Vast. Sources: https://docs.runpod.io/serverless/endpoints/endpoint-configurations , https://docs.runpod.io/serverless/pricing , https://www.runpod.io/serverless-gpu

Cold starts with multi-GB SeedVR2 models remain *our* problem on both platforms: FlashBoot helps only re-warmed workers, and RunPod's answer for big models is baking into the image or datacenter-scoped network volumes ($0.07/GB/mo) — the same engineering as Vast's Inactive-worker/custom-image story. Source: https://docs.runpod.io/serverless/pricing , https://github.com/runpod-workers/worker-comfyui (Customization Guide)

## 6. ComfyUI on RunPod

`runpod-workers/worker-comfyui` is first-party (runpod-workers org), popular (~739 stars / 703 forks), supports custom models/nodes (network volumes or Docker builds) and S3 output upload via env vars — but its documented output contract is **images** (`output.images`, base64 or `s3_url`); video workflows are not mentioned anywhere in its README. Source: https://github.com/runpod-workers/worker-comfyui . For SeedVR2 video output we would either fork it or (cheaper) write a thin RunPod handler that drives our existing ai-dock wrapper/ComfyUI internals — the container internals port unchanged; only the PyWorker/`auth_data` layer is replaced by a `handler(job)` function.

## 7. Pricing: the serverless markup Vast doesn't have

RunPod serverless is per-second at flat catalog rates (active/always-on worker discounts are "available through sales inquiry", not listed): **RTX 4090 24 GB $1.10/hr, A6000/A40 48 GB $1.22/hr, L40/L40S/6000 Ada 48 GB $1.75/hr, A100 80 GB $2.72/hr, H100 $4.79/hr**. RunPod's own pods run far cheaper (4090: $0.34 community / $0.74 secure), i.e. the serverless tier carries a ~1.5–3× markup over its own on-demand compute. Sources: https://www.runpod.io/serverless-gpu , https://www.runpod.io/pricing , https://docs.runpod.io/serverless/pricing

Vast serverless charges **plain marketplace on-demand rates with no serverless markup** ([vastai-serverless.md](vastai-serverless.md) §4); current 4090 marketplace listings run ~$0.23–0.59/hr with verified mid-range ~$0.35 (https://vast.ai/pricing/gpu/RTX-4090). So per GPU-hour on 4090-class hardware, RunPod serverless ≈ **2–4× Vast**. At our scale — 300 jobs/mo × 20 min = **100 GPU-hours/mo of execution** — that is roughly **$110/mo (RunPod 4090 flex) vs ~$35/mo (Vast @ $0.35)**: a real ratio but small absolute money. The dominant cost in ADR 0003's policy is the **warm floor** (2 Ready workers 24/7 ≈ 1,460 h/mo ≈ **$510/mo** at Vast rates; ≈ **$1,600/mo** at RunPod's $1.10 flex rate, less with a negotiated active discount). RunPod's queue + FlashBoot could *justify shrinking* the warm floor (queued jobs survive placement waits without backend involvement), but multi-GB model loads mean genuinely cold workers still cost minutes — the floor-vs-cold-start tradeoff does not disappear, it just gets a queue in front of it.

## 8. Comparators: where the spectrum ends

- **Modal**: functions have a default timeout of 300 s and a documented hard ceiling — "users may specify timeout durations between 1 second and 24 hours" (https://modal.com/docs/guide/timeouts). Long jobs are first-class (spawn/`.remote` with polling), but it's a premium serverless runtime with its own packaging model; 24 h is far above our need, and pricing sits above even RunPod.
- **Replicate**: "Predictions time out after running for 30 minutes" — a hard ceiling at exactly our upper bound, relaxable only by contacting support (https://replicate.com/docs/topics/predictions/lifecycle). Deployments give queueing/webhooks, but a 30-min ceiling with 30+-min jobs disqualifies it outright.
- Spectrum: Replicate (30 min hard) < Modal (24 h) < RunPod (7 days, per-job) < Vast (no platform-side ceiling because there is no platform-side job at all — the "limit" is whatever HTTP timeout the client holds).

## 9. The honest comparison for our case

**What RunPod's model would actually buy us:**

1. **Delete the backend-held pending queue** — unplaced jobs live in RunPod's queue (TTL up to 7 days), not in our dispatcher's re-poll loop of `/route/`. The backend job table survives regardless (user-facing state must outlive the platform's 30-minute result retention), so this deletes the re-poll/placement loop, not the job store.
2. **Platform liveness + same-ID retry** — a hung/crashed worker becomes TIMED_OUT instead of busy-forever; `/retry` requeues with the same id; `refresh_worker` cleans poisoned workers (the VRAM-latch scenario from [vastai-serverless.md](vastai-serverless.md) §7). This replaces our planned stall detector's *recovery* half; we'd still want the progress-silence detector for early warning, since a 7-day ceiling won't catch a wedged 20-minute job quickly — per-job `executionTimeout` at ~2× expected duration would.
3. **Built-in progress store** — `progress_update` → `/status` replaces the ai-dock wrapper fork + push-webhook + heartbeat design ([serverless-live-progress.md](serverless-live-progress.md) §5 option 2) with a supported one-liner, at the price of backend polling instead of push. (The handler would still hook `_update_progress` internally to feed it — the wrapper-side work doesn't fully vanish, it just gets a supported sink.)
4. **Webhook with retries** (2 retries/10 s) vs the wrapper's fire-once webhook — though neither is durable enough to drop the S3-object-as-truth fallback.

**What it costs:**

- **~3× GPU rate**: ≈ +$75/mo at launch volume on execution alone; ≈ +$1,100/mo if the 2-worker warm floor were replicated at flex rates — the floor policy would need rethinking, not copying (§7).
- **A fixed GPU catalog**: max 3 GPU types per endpoint, no marketplace breadth, no host price competition (https://docs.runpod.io/serverless/endpoints/endpoint-configurations).
- **A worker rewrite at the dispatch layer only**: a RunPod `handler(job)` replaces PyWorker/`route`/`auth_data`; container internals — ComfyUI, SeedVR2, the ai-dock wrapper, the S3 flow — port unchanged, so this is days not weeks, and the `IUpscaleDispatcher` seam from ADR 0003 is exactly where it plugs in.
- **An image-centric first-party ComfyUI worker** we'd bypass or fork for video output (§6).

**Which pains are Vast-specific vs inherent to long GPU jobs anywhere:**

- **Vast-specific** (RunPod fixes all of these): no platform queue; job-as-held-open-POST; no platform execution timeout; hung worker reports busy forever; no status/progress store; no crash retry; fire-once webhook.
- **Inherent** (RunPod fixes none of these): multi-GB model cold starts vs warm-capacity cost; bytes via object storage in both directions; progress silence during long un-stepped ComfyUI nodes; one serial job per ComfyUI worker; and, on any marketplace-sourced fleet, host performance variance.

## 10. Verdict

**RunPod genuinely accounts for long jobs; Vast genuinely does not** — the observation driving this question is correct. Queue-based endpoints with per-job 7-day execution timeouts, platform-held state, TIMED_OUT-on-silence, same-ID retry, and a progress channel are precisely the long-job affordances Vast lacks (Vast's serverless is a router + autoscaler; the *job* abstraction doesn't exist there).

But for **our** system the delta is smaller than it looks, because ADR 0003's design already rebuilds RunPod's affordances at the layer we control: webhook + S3 is our durable completion regardless of platform, the wrapper fork gives push progress (better than RunPod's poll), and the backend job table must exist anyway (user-facing state outlives any 30-min result retention or 7-day TTL). RunPod would let us delete the `/route/` re-poll loop, the stall-*recovery* logic, and the progress fork — maybe 1–2 weeks of build — in exchange for a permanent ~3× GPU rate and a narrower GPU market. That is "same inherent problems, much nicer queue, higher price."

**Recommendation: don't reopen ADR 0003 on this evidence.** The right move is cheaper: name RunPod as the second dispatcher behind the existing `IUpscaleDispatcher` seam (alongside the DIY-pool fallback), since the job contract — workflow JSON in, request_id correlation, S3 out, webhook — ports to a RunPod handler nearly verbatim. Revisit if (a) Vast's missing liveness/retry actually bites in production (hung workers eating jobs), (b) the warm-floor bill dominates and RunPod's queue+FlashBoot would let us run floorless, or (c) Vast's young platform disappoints operationally — the same trigger ADR 0003 already reserves for the DIY fallback.
