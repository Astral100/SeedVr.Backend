# V2 features

Useful features consciously deferred out of v1. Each entry names the feature, why it earns a place, and what v1 does instead. Entries are added as planning decisions defer them; an entry graduates by becoming an ordinary ticket when its time comes.

## Per-attempt raw log archive in R2

At attempt end the worker agent zips the raw log files (ComfyUI, wrapper, agent) and uploads the zip to R2 via one more presigned PUT carried in the dispatch payload — the same mechanism as the output upload. Earns a place by making log history durable and system-neutral: the zip survives telemetry-box loss, outlives the retention window, and re-loads into any future log store. V1 relies on the live attempt-tagged log stream alone (12-month retention on the unbacked-up telemetry box, per ADR 0010) — declined as premature durability at launch scale.

## Multipart / resumable browser uploads

Uploads beyond the 2 GB v1 cap, and resume-after-disconnect for large uploads, need backend-orchestrated multipart presigning (create-multipart, sign each part, complete — R2 requires equal-size parts). V1 uses a single presigned PUT with the 2 GB input cap; a dropped connection restarts the upload. The API contract carries an upload-mechanics discriminator so multipart can be added without breaking v1 clients.
