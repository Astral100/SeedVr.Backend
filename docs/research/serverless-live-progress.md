# Vast.ai Serverless — live job progress during a run

Research for issue #16. All claims cite primary sources: docs.vast.ai and source code read directly from the `vast-ai/vast-sdk`, `vast-ai/pyworker`, and `ai-dock/comfyui-api-wrapper` GitHub repos (main branches, read 2026-08-30). Builds on the wire-protocol findings in [vastai-serverless.md](vastai-serverless.md).

**Verdict: not out of the box, but one layer down everything needed already exists — the cheapest correct path is a small fork, and there are two viable shapes.** The stock `comfyui-json` PyWorker exposes exactly one route to the client, `POST /generate/sync`, which blocks until the job is done; no progress/status route is proxied, and the ai-dock webhook fires only once, at terminal state. However: (a) the ai-dock wrapper *already* consumes ComfyUI's `/ws` progress events, maintains a live per-request status (`pending → generating → generated → completed/failed`, with throttled percent messages), and serves it via `GET /result/{id}` and an SSE `POST /generate/stream` — these just aren't registered in the PyWorker; (b) the PyWorker framework has built-in SSE streaming passthrough and the Python SDK has an SSE client, both first-party and documented; (c) `auth_data` is reusable for repeated calls to the same worker (the signature covers only the worker URL; sessions are the documented affinity mechanism built on exactly this reuse). So the decided direction (worker-initiated push) is right if we want push, but a ~10-line fork of `workers/comfyui-json/worker.py` registering `/generate/stream` gives platform-native live progress with the least new machinery. Details in §5; cadence/stall caveats in §6.

---

## 1. What the worker URL actually exposes; `auth_data` reuse

### Routes on the worker

The PyWorker web server registers only: (a) the routes listed in `WorkerConfig.handlers` — each as **POST only** (`web.post(route_path, ...)` in `Worker.__init__`, `vastai/serverless/server/worker.py` in https://github.com/vast-ai/vast-sdk); (b) hardcoded session routes `POST /session/create|end|get|health`; (c) `POST /pyworker/update` (`start_server_async` in `vastai/serverless/server/lib/server.py`). The route table is also documented: "Your `HandlerConfig` routes, plus `/session/create`, `/session/end`, `/session/get`, `/session/health`, `/pyworker/update`" — https://docs.vast.ai/guides/serverless/sessions ("The worker web server").

The stock ComfyUI worker registers **exactly one handler**: `HandlerConfig(route="/generate/sync", allow_parallel_requests=False, max_queue_time=10.0, ...)` — `worker_config` at the bottom of `workers/comfyui-json/worker.py` in https://github.com/vast-ai/pyworker. Its README confirms: "The worker provides a single endpoint: `/generate/sync`" (https://github.com/vast-ai/pyworker/blob/main/workers/comfyui-json/README.md), as do the docs (https://docs.vast.ai/guides/serverless/comfy-ui, "Endpoints").

So the ai-dock wrapper's pollable routes — `GET /result/{request_id}`, `POST /generate` (async submit, 202), `POST /generate/stream` (SSE), `POST /cancel/{request_id}`, `GET /queue-info`, `GET /health` (all defined in `main.py` of https://github.com/ai-dock/comfyui-api-wrapper) — **exist on the instance at port 18288 but are unreachable through the serverless worker URL**. Nothing proxies them: PyWorker returns 404 for unregistered paths, and the framework can only create POST routes that forward the request `payload` via `session.post(url=handler.endpoint, ...)` (`Backend.__call_api`, `vastai/serverless/server/lib/backend.py` in vast-sdk), so a GET route with a path parameter like `/result/{id}` cannot be expressed without framework changes.

### `auth_data` reuse semantics

Verified from `Backend.__check_signature` in `vastai/serverless/server/lib/backend.py` (vast-sdk): the worker verifies an RSA (PKCS#1 v1.5 / SHA-256) signature — against a public key fetched from the autoscaler (`_fetch_pubkey`) — over **only** `{"url": auth_data.url}`:

```python
message = {"url": auth_data.url}
if verify_signature(json.dumps(message, indent=4, sort_keys=True), auth_data.signature):
    self.reqnum = max(auth_data.reqnum, self.reqnum)
    return True
```

No nonce, no expiry, no cost check; `reqnum` is recorded (max) but **not** enforced as monotonic and never rejects a request. `AuthData` carries `cost, endpoint, reqnum, request_idx, signature, url` (`vastai/serverless/server/lib/data_types.py`), and every request to a handler route must include the full `auth_data` object (`EndpointHandler.get_data_from_request`, same file). **Conclusion: one `/route/` response authorizes unlimited repeated POSTs to that same worker for as long as the worker is alive.**

This is not an accident — it is the foundation of the documented **sessions** feature: "The SDK keeps the worker's URL and `auth_data` on the returned `Session` object. Every subsequent `session.request(...)` posts straight to that URL with the `session_id` attached. The `/route/` call is skipped entirely," and sessions exist for, among other things, "Asynchronous work. You can kick off a long-running job, return immediately, and poll it later on the same worker." — https://docs.vast.ai/guides/serverless/sessions. A session additionally keeps the worker reserved (reports the session's `cost` as in-flight load so the engine doesn't scale it away) and extends a TTL per request. For our plain-HTTP .NET client, sessions are optional: bare `auth_data` reuse works for repeated calls; sessions add reservation + TTL semantics.

One metrics caveat for any polling design: every request through a handler route is counted against the worker with `count_workload()` (default **100** — `GenericApiPayload.count_workload` in `vastai/serverless/server/worker.py`), so a naive poll route would fabricate autoscaler load and trigger scaling; a fork adding poll routes must set a `workload_calculator` returning ~0.

## 2. Streaming / SSE through the PyWorker proxy

Yes — streaming passthrough is a built-in, first-party feature of the proxy, it is just not wired up for ComfyUI:

- **Server side**: the default `generate_client_response` in `GenericEndpointHandler` (`vastai/serverless/server/worker.py`, vast-sdk) detects streaming model responses (`Content-Type` starting `text/event-stream`, equal to `application/x-ndjson`/`application/jsonl`, containing "stream", or `Transfer-Encoding: chunked`) and copies chunks to the client through a `web.StreamResponse` as they arrive. Documented under "Default response behavior": https://docs.vast.ai/guides/serverless/creating-new-pyworkers. The LLM workers use exactly this path in production (`workers/openai/core.py` in pyworker registers `/v1/completions` and `/v1/chat/completions`, which stream; the docs note SSE chunk-boundary behavior of the proxy at https://docs.vast.ai/guides/serverless/openai-compatible-api).
- **Client side**: the Python SDK supports `endpoint.request(route, payload, stream=True)` (`Endpoint.request` in `vastai/serverless/client/endpoint.py`), returning an async iterator over parsed SSE `data:` JSON objects (`_iter_sse_json` and the `stream=True` branch of `_make_request` in `vastai/serverless/client/connection.py`, vast-sdk). From .NET this is just reading an SSE response body.
- **Model side**: the ai-dock wrapper's `POST /generate/stream` (`generate_stream` + `_stream_status_updates` in `main.py`, https://github.com/ai-dock/comfyui-api-wrapper) submits the job and returns `media_type="text/event-stream"`, polling the internal response store every 1 s and emitting an event whenever `status` or `message` changes (or queue position changes), each event carrying `request_id`, `status`, `message`, `timestamp`, `elapsed_time`, queue sizes and queue position, then a `final_result` event with the full serialized result at terminal state.

WebSockets: no. The proxy forwards a single POST body to the model server and relays the response (`Backend.__call_api`); there is no WS upgrade path anywhere in `server.py`/`backend.py`, and ComfyUI's own `/ws` is consumed *inside* the wrapper (§3), not exposed.

**The missing link is one `HandlerConfig`**: `workers/comfyui-json/worker.py` simply never registers `/generate/stream`. Registering it (in a fork pinned via `PYWORKER_REPO`/`PYWORKER_REF` — https://docs.vast.ai/guides/serverless/comfy-ui) makes live progress flow end-to-end with zero changes to the framework or the wrapper.

## 3. What progress actually exists inside the worker

The ai-dock wrapper's `GenerationWorker` (`workers/generation_worker.py`) connects to ComfyUI's WebSocket (`self.ws_url`, `_ws_listen_loop`) and handles, for its `prompt_id`:

- `execution_start` → `_update_progress(request_id, "Execution started...")`
- `executing` (node id) → `_update_progress(request_id, f"Processing node: {node}")`; `node: None` means complete
- `progress` → computes `progress_pct = value / max * 100`, appends `{time, value, max, percentage}` to `execution_result["progress_updates"]`, and — **throttled to at most one store write per 2 s** — `_update_progress(request_id, f"Progress: {pct:.1f}% ({value}/{max})")`
- `execution_error` → failure

`_update_progress` writes only the free-text `result.message`; the `Result` model has no structured percent field (`id, message, status, comfyui_response, output, timings` — `responses/result.py`). Status transitions (`pending → generating → generated → completed/failed/cancelled`) are set in `generation_worker.py`/`postprocess_worker.py`/`main.py`. The full `progress_updates` array is merged into `comfyui_response` on completion, but `_shape_result` (`main.py`) strips `comfyui_response` from responses by default (opt back in per request with header `X-Include-ComfyUI-Response: 1` or env `INCLUDE_COMFYUI_RESPONSE=true`). The docs' phrase "detailed process updates emitted by ComfyUI during generation" (https://docs.vast.ai/guides/serverless/comfy-ui) refers to this after-the-fact data plus the live `message` field — so out of the box, live percent reaches a client only as a parseable `message` string via `/result/{id}` or `/generate/stream`.

## 4. Webhook: completion-only, once per job

The wrapper's webhook fires in exactly one place: the `finally` block of `PostprocessWorker.work()` (`workers/postprocess_worker.py`, https://github.com/ai-dock/comfyui-api-wrapper) — i.e., after asset move/S3 upload, once, when the job reaches a terminal state (completed *or* failed; the block runs on the failure path too). `send_webhook` POSTs the serialized `Result` (with `comfyui_response` stripped as "typically large") plus `webhook.extra_params`, optionally HMAC-signed (`webhook.secret`; see `tests/test_webhook_signing.py`). No call sites exist in `preprocess_worker.py`, `generation_worker.py`, or `main.py`. The docs match: "`WEBHOOK_URL`: Optional webhook to call after generation completion or failure" — https://docs.vast.ai/guides/serverless/comfy-ui. **There is no progress-cadence webhook.**

## 5. Extension points (concrete)

The decided direction is worker-initiated push if the platform has nothing native. Findings: the platform has a native *stream* (one fork-line away) but no native *push*. Both are cheap; ranked by cost:

1. **Registered SSE stream (smallest change, pull-ish but continuous).** Fork `vast-ai/pyworker`, in `workers/comfyui-json/worker.py` add to `worker_config.handlers`: `HandlerConfig(route="/generate/stream", allow_parallel_requests=False, workload_calculator=lambda p: 100.0)` (no `benchmark_config` — only one handler may have one, per `EndpointHandlerFactory.get_benchmark_handler` in vast-sdk `worker.py`). The framework's default response generator (§2) relays the wrapper's SSE verbatim; our backend holds one HTTP response open per running job and gets an event on every status/message change (percent at ≤2 s cadence during sampling). Pin with `PYWORKER_REPO`/`PYWORKER_REF` (https://docs.vast.ai/guides/serverless/creating-new-pyworkers, https://docs.vast.ai/guides/serverless/comfy-ui). Note we already planned this fork for `misc/benchmark.json` (vastai-serverless.md §6). The FIFO queue is shared across both routes and the streamed request occupies the worker's single slot — same as `/generate/sync`, so no capacity change.
2. **Worker→backend progress push (fork the ai-dock wrapper — the right layer for push).** The wrapper is baked into the Docker image (https://docs.vast.ai/guides/serverless/comfy-ui, "Template Components"), and we already plan a custom image. The hook point is `GenerationWorker._update_progress(request_id, message)` in `workers/generation_worker.py` — it is the single funnel every live progress event already passes through, pre-throttled to 2 s. Extend it (or replace its body) to also POST `{request_id, status, message, percent, timestamp}` to a backend URL taken from the request payload (mirror `input.webhook`, e.g. `input.progress_webhook`) or env; reuse the HMAC signing from `PostprocessWorker.send_webhook` (hoist it to a shared module). For structured percent, pass `value`/`max` from the `progress` branch of `_ws_listen_loop` into `_update_progress` instead of a preformatted string. Phase = the existing `status` transitions plus which queue the request is in (`preprocess/generation/postprocess`, visible in `main.py`'s `_get_queue_position`). This gives true push with no long-lived connections and works even if the client HTTP path drops.
3. **Polling `/result/{id}` through the proxy — not recommended.** Requires both a wrapper change (make it POST-able with the id in the body, since the framework can only forward POST payloads to a same-path endpoint — §1) and a `workload_calculator` returning ~0 to avoid fabricating autoscaler load (§1). Strictly more moving parts than option 1 for a worse signal.

A PyWorker-side push (e.g. in `vastai/serverless/server/`) is the wrong layer: the PyWorker only proxies HTTP and tails a log file (`Backend.__read_logs`); it never sees ComfyUI's WS events. The wrapper does.

Whichever shape is chosen, `auth_data` reuse (§1) additionally allows the backend to re-attach after a restart: re-`/route/` is only needed if the worker died; the original worker URL + `auth_data` remain valid for a reconnecting `/generate/stream`-style call only for *new* requests, though — the wrapper's SSE stream is bound to the submitting request, so re-attach to a *running* job would need option 2's push or a poll route. This asymmetry favors option 2 for crash-resilience.

## 6. Fit with the "no update for T seconds" stall signal

Cadence realities, from `_ws_listen_loop`/`_update_progress` (§3):

- During sampling, ComfyUI emits `progress` per step; the wrapper throttles store writes to one per 2 s. Good signal density.
- Between sampler nodes there are only `executing`-node messages; long single-node phases with no step counter (model load into VRAM, VAE decode on long videos, upload during postprocess) produce **legitimate silent gaps** — the SSE generator emits nothing when nothing changes (no keepalive/heartbeat in `_stream_status_updates`), and option 2's push would be equally silent.
- Terminal detection is robust independently of progress: webhook (§4) + S3 objects remain the durable completion signal (vastai-serverless.md §6).

Therefore T must be phase-aware (generous during `generating` before first `progress` event and during postprocess, tight during stepped sampling), or the push hook (option 2) should add its own fixed-interval heartbeat (e.g. repost last-known state every 15 s from a timer in the wrapper) so the backend's stall detector can be a single simple timeout. The latter is a ~10-line addition inside the same fork and is the recommended companion to option 2.
