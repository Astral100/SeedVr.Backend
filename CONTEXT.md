# SeedVr.Backend

Production backend for the SeedVR2 video-upscaling service: accepts enhancement jobs over an API, runs them on Vast.ai GPU workers via ComfyUI, and delivers the results. Successor and future source of truth for the SeedVr proof of concept; its glossary starts from the POC's `CONTEXT.md` with the changes below.

## Language

### Job lifecycle

**Job state** (`JobState`):
The fine-grained, backend-only enum stored on a job, recording exactly where it is in processing (e.g. input uploaded, dispatched, output uploaded). Single source of truth that the dispatcher, crash recovery, cancellation and transition guards act on. Never shown to users. Exact value list is defined by the job-pipeline design.

**UI job status** (`UiJobStatus`):
The coarse, user-facing enum derived from the job state by a single pure mapping — never stored, so the two cannot drift. This is the vocabulary of API responses and the UI (e.g. queued, processing, done). Each job state maps to exactly one UI job status.

### Files

**Job file**:
A stored media file attached to a job — an input or an output — recorded as a row in its own table (`job_files`): storage key, size, probe metadata (duration, resolution, codec…), content hash, and per-file lifecycle stamps (expiry, deletion). Job files can expire and be deleted from storage while the job record lives on. Supersedes the POC meaning of "job file" (the JSON job definition loaded by the console app), which does not exist in the backend.

### Parameters

**Preset**:
A named, versioned parameter set defined and served by the backend. The frontend loads the preset catalogue and resolves a selected preset into raw parameters client-side; a job request therefore always carries raw parameters. A request submitted from the simplified preset tab also carries the preset reference (id + version); a request from the advanced tab carries raw parameters only. Whatever was carried is recorded with the job, since a preset's parameters can change across versions.

**Effective parameter set**:
The values a run actually used after the workflow template, request parameters (raw or preset-resolved) and any overrides are layered; recorded with the job.
