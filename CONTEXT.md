# SeedVr.Backend

Production backend for the SeedVR2 video-upscaling service: accepts enhancement jobs over an API, runs them on Vast.ai GPU workers via ComfyUI, and delivers the results. Successor and future source of truth for the SeedVr proof of concept; its glossary starts from the POC's `CONTEXT.md` with the changes below.

## Language

### Job lifecycle

**Job state** (`JobState`):
The fine-grained, backend-only enum stored on a job, recording exactly where it is in processing (e.g. queued, processing, uploading output). Single source of truth that the dispatcher, crash recovery, cancellation and transition guards act on. Never shown to users. Exact value list is defined by the job-pipeline design.

**UI job status** (`UiJobStatus`):
The coarse, user-facing enum derived from the job state by a single pure mapping — never stored, so the two cannot drift. This is the vocabulary of API responses and the UI (e.g. queued, processing, done). Each job state maps to exactly one UI job status.

### Files

**Input file**:
A user's uploaded media asset, recorded as a row in its own table with its own lifecycle: storage key, size, probe metadata (duration, resolution, codec…), validation verdict, and per-file lifecycle stamps (expiry, deletion). An input file exists before and independently of any job — it is validated at upload confirmation and may feed many jobs. Expiry deletes the stored bytes only; the record persists, so jobs that used the file keep their history.

**Output file**:
The media file an attempt produced, stored under an attempt-scoped key and recorded with the same lifecycle stamps as an input file. Belongs to its job; only the succeeding attempt's output is the job's result. Like input files, the bytes can expire while the record lives on.

### Parameters

**Preset**:
A named, versioned parameter set defined and served by the backend. The frontend loads the preset catalogue and resolves a selected preset into raw parameters client-side; a job request therefore always carries raw parameters. A request submitted from the simplified preset tab also carries the preset reference (id + version); a request from the advanced tab carries raw parameters only. Whatever was carried is recorded with the job, since a preset's parameters can change across versions.

**Effective parameter set**:
The values a run actually used after the workflow template, request parameters (raw or preset-resolved) and any overrides are layered; recorded with the job.

### Workers

**Worker**:
A GPU instance recruited from the Vast.ai marketplace that processes exactly one job at a time; the number of ready workers is the number of jobs that can run in parallel.

**Warm floor**:
The minimum number of fully provisioned, ready workers kept rented even while idle so a job starts without provisioning delay; a replacement is recruited as soon as a warm worker takes a job.

**Dispatcher**:
The seam through which a job reaches GPU capacity. Exactly one dispatcher is active; implementations (serverless endpoint, self-managed pool) are interchangeable behind it because the job contract is identical.

**Attempt**:
One delivery of a job to a worker. A job may take several attempts before it succeeds or is abandoned; each attempt has its own identity, and anything a worker reports is tied to the attempt that produced it, so a superseded attempt can never speak for the current one.

**Worker agent**:
The program of ours bundled into the worker image alongside ComfyUI and the wrapper. It is the worker's voice: it reports heartbeats, progress and completion back to the backend, and uploads the finished output to storage.

**Heartbeat**:
The worker agent's periodic "still alive" signal, sent independently of job progress. Heartbeat silence — not progress silence — is what marks a worker as stalled; a job may legitimately report no progress for long stretches while heartbeats continue. Silence only counts while the backend was listening: the stall clock runs from the later of the last heard heartbeat and the moment the backend started listening.

**State timeout**:
The maximum time a job may spend in one state before its timeout action fires. Every non-terminal state has one; the durations are tunable configuration, the actions are design.

**Recovery check**:
A periodic pass that reads the database, finds every job matching some condition (typically past a state timeout), and applies that state's fix. Safe to run repeatedly — already-fixed jobs match nothing — which is why any combination of failures only ever delays recovery, never loses it.
