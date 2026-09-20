# API Contract v1

The client-facing HTTP contract of the core services: what the frontend (and any future API consumer) sees. Resolved by [API contract v1 (#10)](https://github.com/Astral100/SeedVr.Backend/issues/10).

**Scope.** This document covers the client-facing surface only. The backend↔worker-agent protocol (both directions) and the worker-internal one are specified with the [worker image (#17)](https://github.com/Astral100/SeedVr.Backend/issues/17). The backend also exposes one webhook receiver for Vast.ai's completion notice (request-id-checked; one of the three completion signals from the job-pipeline design) — its details likewise live with #17.

This is the authoritative human-first contract. A machine-readable OpenAPI description is generated from the implementation code later, with this document as its acceptance reference — no hand-maintained OpenAPI file exists.

## Foundations

- **Base path**: every route starts with `/api/v1/`. Versioning is by path prefix; a breaking change stands up `/api/v2/` beside it.
- **Auth**: every endpoint acts as the signed-in user; "the user's jobs/files" always means the authenticated caller's. Mechanics (tokens, sessions, accounts) are the auth ticket's (#11); nothing in this contract depends on their outcome.
- **Conventions**: JSON bodies; camelCase field names; timestamps ISO 8601 UTC (`2026-09-20T14:03:00Z`); ids are UUID strings (UUIDv7); enums are strings (`"Processing"`); unknown fields in requests are rejected (400), not silently ignored.
- **Error model**: errors are RFC 9457 Problem Details (`application/problem+json`) extended with a stable machine-readable `code` the frontend branches on; `detail` carries the human-friendly text. Which outcomes are errors at all is governed by the **promise rule** ([ADR 0012](adr/0012-http-promise-rule.md)): the status code reports whether the endpoint delivered its promise; domain findings — even negative ones — are data in a 2xx body.
- **Graceful degradation**: when the database is unavailable the API retries briefly internally, then returns 503 `service-unavailable` with a friendly "system temporarily unavailable — please try again later"; polling pages show "connection lost, retrying". Whole-backend unreachability is handled client-side the same way.
- **Job statuses** (the user-facing 5-value enum, derived from the internal `JobState` by the pipeline design): `Queued`, `Processing`, `Completed`, `Failed`, `Cancelled`.
- **Polling model**: no push channels in v1 (settled on the job-pipeline ticket). Clients poll at a 2–3 s cadence: the **status ticker** while a job's detail page is visible, the **jobs list** while the jobs screen is visible. Both are documented poll-safe at that cadence.

## Record vs. ticker

A job has two representations with distinct roles:

- **The job record** (`GET /jobs/{jobId}`, and the rows of `GET /jobs`): the durable facts — identity, input file, parameters, timestamps, status, and the ending (output or failure) once there is one. Running jobs additionally carry a **progress snapshot**, correct as of the fetch, so screens render instantly.
- **The status ticker** (`GET /jobs/{jobId}/status`): the live view — status, progress percent, time estimate — polled only while the job is live. The status field appears in both; it is the ticker's exit signal.

Rule: while a job runs, the ticker is the truth; snapshot fields in records are as-of-fetch.

## Feature: uploading a video

Input files exist independently of jobs: uploaded and validated first, then referenced by any number of submits (re-run = same file id, new job). Video bytes never pass through the core services — the browser talks to object storage directly with presigned links.

### `POST /api/v1/files`

Declare an intended upload. Request: `{ fileName, sizeBytes, contentType }`.

- **201** `{ fileId, uploadUrl, uploadUrlExpiresAt }` — the file record is created in awaiting-upload state; `uploadUrl` is a presigned direct-to-storage PUT link (single PUT, validity a few hours).
- **422** `file-too-large` — declared size above the 2 GB single-PUT cap (multipart upload is a v2 feature). Refused before any transfer.

The browser then PUTs the bytes straight to storage. A single PUT is all-or-nothing: an aborted upload leaves zero bytes and zero cost — only the awaiting-upload record, which expires with its link and is removed by the periodic deletion pass. Retry while the link is valid = simply PUT again (overwrites cleanly); after expiry = start over with a fresh `POST /files`.

### `POST /api/v1/files/{fileId}/uploaded`

The client reports the upload finished; the backend validates synchronously and answers with a verdict.

- **200** `{ verdict: "valid", media: { durationSeconds, width, height, videoCodec, containerFormat } }` — the file is usable in submits.
- **200** `{ verdict: "invalid", reasonCode, reason }` — validation ran; the file is not a usable video. An expected outcome, not an error (promise rule: this endpoint promises a verdict). The record is marked invalid; no job can reference it.
- **409** `upload-not-complete` — the bytes aren't there (nothing uploaded, or size mismatch with the declaration). The frontend re-offers the upload.
- **404** `file-not-found`.

Repeating the call is harmless: it returns the stored verdict.

**Validation mechanism (a contract constraint, not an implementation choice).** The synchronous answer is only possible because validation never downloads the file: a storage HEAD checks existence and that stored size equals declared size, then ranged reads probe the container's metadata block (duration, resolution, codec) — a few MB of a 2 GB file, seconds at most. **Stated limitation**: corruption buried mid-file is not detectable here; it surfaces at render time, where the failure-recovery design (#21) marks the job Failed as a permanent content failure (no retry). If validation were ever reimplemented as a full download + decode, the synchronous confirm — and this contract — would break; don't.

## Feature: running a job

### `POST /api/v1/jobs`

Submit a job. Request:

```json
{
  "type": "video-upscale",
  "inputFileId": "…",
  "parameters": { /* raw parameters; schema depends on type */ },
  "preset": { "presetId": "…", "version": 3 }   // optional, for the record only
}
```

One collection serves all job types: `type` selects the parameter schema (per the shared-Job domain model, ADR 0002 layering). Parameters are always raw — presets resolve on the frontend (ADR 0001); a submission from the simplified preset tab also carries the preset reference purely for the record.

- **201** — the job record. The job is born directly in `Queued` and dispatch is triggered in the same transaction.
- **422** `input-file-not-usable` — the referenced file is missing, invalid, or its bytes have expired. No job is created from bad input, ever.
- **409** `duplicate-active-job` + `existingJobId` — see the duplicate rule below.

**The duplicate rule.** A submit identical to an existing job of the caller's — same input file id, same parameters — whose user-facing status is `Queued` or `Processing` creates nothing. The answer names the existing job, and the frontend tells the user: *"This work is already in progress. To resubmit it, cancel it first."* Restart is then the ordinary cancel followed by an ordinary submit — no override flag, no special call. Because a cancel-requested job already shows `Cancelled`, it no longer blocks resubmission: what the user sees and what the server enforces never disagree. The check is backed by a database uniqueness constraint on active jobs (user + file + parameter fingerprint), so simultaneous double-clicks cannot both slip through; there is **no client idempotency token**. Accepted residuals (recorded on #21's terms): a repeat landing after the original finished creates a new job (indistinguishable from a deliberate re-run), and a quick cancel-and-restart can briefly overlap with the old attempt still finishing ("Succeeded wins" race — minutes of overlap at worst, never a second full run).

### `GET /api/v1/jobs/{jobId}`

The job record — fetched when a job's page opens, when the ticker reports a terminal status, after a `duplicate-active-job` answer, and as the source for re-run.

```json
{
  "jobId": "…", "type": "video-upscale", "status": "Processing",
  "createdAt": "…", "startedAt": "…", "finishedAt": null,
  "inputFile": { "fileId": "…", "fileName": "…", "sizeBytes": 0, "durationSeconds": 0, "width": 0, "height": 0 },
  "parameters": { /* effective raw parameters */ },
  "preset": { "presetId": "…", "version": 3 },
  "progress": { "percent": 42, "etaSeconds": 180 },      // running jobs only; snapshot
  "failure": { "code": "…", "message": "…" },            // Failed only
  "output": { "sizeBytes": 0, "expiresAt": "…" }         // Completed only; no URL — see result endpoint
}
```

- **404** `job-not-found` — the one true 404: the URL names a missing resource. (A broken reference inside a body is 422, never 404.)

`etaSeconds` is nullable and may be absent until the estimator subsystem (currently unspecified on the map) is ported; the field is reserved now so its arrival changes nothing.

### `GET /api/v1/jobs/{jobId}/status`

The live ticker, polled every 2–3 s while the detail page is visible and the job is live.

- **200** `{ jobId, status, progressPercent, etaSeconds }`.

When `status` leaves `Queued`/`Processing`, stop polling and re-fetch the record. Progress percent is not guaranteed monotonic — an internal retry may legally reset it.

### `POST /api/v1/jobs/{jobId}/cancel`

Request cancellation. Cancel is an action, not a removal — the record lives on.

- **202** — the job shows `Cancelled` from this moment; confirmation machinery (worker interrupt, escalation ladder) runs behind the scenes. Repeating the call is a no-op 202. By the "Succeeded wins" rule a cancel that races an already-finishing render may still end `Completed`.
- **409** `job-already-finished` — the job is in a terminal status.
- **404** `job-not-found`.

## Feature: getting the result

### `GET /api/v1/jobs/{jobId}/result`

Mints a fresh time-limited download link for a completed job's output. Links are minted on demand and never stored in job payloads.

- **200** `{ url, expiresAt }` — a presigned storage GET, 2-day validity (storage ticket). Call again any time for a fresh link while the output bytes are stored.
- **409** `output-not-ready` — the job isn't `Completed`.
- **410** `output-expired` — the job completed but the stored bytes have passed their retention and are gone; the record remains.
- **404** `job-not-found`.

## Feature: seeing your jobs

### `GET /api/v1/jobs`

The caller's jobs, newest first, capped at 50. **No pagination in v1** — revisited only if a real history UI materializes. Rows are job records (including progress snapshots on running jobs).

The jobs screen polls this endpoint every 2–3 s while visible, which keeps every row's progress moving with no extra machinery; it is also the after-reload safety net — a refreshed or second-device session sees its running jobs instead of resubmitting them. (A batch status endpoint was considered and deferred: at launch scale the list query is one indexed read returning a few KB.)

## Feature: the submit form

### `GET /api/v1/presets`

The preset catalogue. Presets are backend-defined and versioned but resolved into raw parameters by the frontend (ADR 0001) — this endpoint is where the frontend gets them; without it presets would be hardcoded client-side and could never change without a frontend release.

- **200** `{ presets: [ { presetId, version, name, description, parameters } ] }` — current versions only; a job records what it actually ran with, so historical versions serve no reader.

## Error code registry (initial)

| `code` | Status | Meaning |
|---|---|---|
| `validation-error` | 400 | Malformed request (unknown fields, bad types, missing required) |
| `file-too-large` | 422 | Declared upload size above the 2 GB cap |
| `file-not-found` | 404 | File id in the URL doesn't exist |
| `upload-not-complete` | 409 | Uploaded-report but bytes absent or size mismatch |
| `input-file-not-usable` | 422 | Submit references a missing/invalid/expired file |
| `duplicate-active-job` | 409 | Identical job already Queued/Processing; carries `existingJobId` |
| `job-not-found` | 404 | Job id in the URL doesn't exist |
| `job-already-finished` | 409 | Cancel on a terminal job |
| `output-not-ready` | 409 | Result requested before completion |
| `output-expired` | 410 | Output bytes past retention |
| `service-unavailable` | 503 | Database/system outage; friendly retry message |

New codes are added by the promise rule: 4xx = the request cannot be honored (always with a code); 5xx = the system failed; negative *findings* of a delivered promise are 2xx data, never codes.

## Client obligations

- Poll cadences as documented (2–3 s); poll only while the relevant screen is visible.
- On `duplicate-active-job`: show the existing job and the "cancel first to restart" message — never auto-cancel.
- When a ticker leaves the live statuses: stop polling, re-fetch the record.
- Re-fetch the jobs list on tab re-focus (jobs submitted from other devices become visible).
- Treat `progress.percent` in records as a snapshot and ticker values as live; treat progress as possibly non-monotonic.

## Deliberate absences

- **No pagination** (v1 cap instead) · **no file-browsing endpoints** (files are reached through jobs; re-run reuses a known file id) · **no historical preset versions** · **no batch status endpoint** (list polling suffices; reconsider with scale) · **no push/SSE** (pipeline decision) · **no client idempotency token** (the duplicate rule replaces it) · **no multipart upload** (v2 feature, 2 GB single-PUT cap meanwhile) · **per-user quotas/rate limits** remain unspecified on the map and are absent here.
