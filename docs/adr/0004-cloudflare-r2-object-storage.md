# Cloudflare R2 is the object storage

All job files — uploaded inputs and produced outputs — live in Cloudflare R2, accessed through the standard S3 API (AWSSDK.S3, region `auto`) behind our own storage interface. R2 wins on the pipeline's defining trait: every stored byte is transferred out at least once (worker pull + user download, plus retries and re-downloads), and R2 egress is genuinely free and uncapped, so downloads, previews and retries never enter a cost or design conversation. Storage is $0.015/GB-month with no minimum; at the realistic launch profile (short-to-medium videos, ≤ ~300 MB typical, ~500 jobs/month, 30-day retention) that is single-digit dollars per month, with ~$60/month as the heavy-usage ceiling (500 jobs/month at 8 GB per job).

Buckets are per-environment (`seedvr-prod`, `seedvr-dev`), created with default placement (no location hint — changing placement later means a new bucket plus a copy, acceptable while data is small). Object keys are direction-rooted — `in/{jobId}/{fileId}`, `out/{jobId}/{fileId}` — because R2 lifecycle rules match literal prefixes only, and the top-level split keeps per-direction retention rules expressible; per-job grouping is the database's job (`job_files` rows hold full keys), never the bucket's.

R2 caveats carried into the design: presigned uploads are PUT only (no POST policies), presigned URLs cap at 7 days, multipart parts must be equal-sized, and the abort-incomplete-multipart lifecycle rule ships on day one.

## Considered Options

- AWS S3. Rejected — ~7x the cost at our egress profile (~$443 vs ~$60/month at the heavy ceiling); the egress line dominates and widens with growth.
- Wasabi. Rejected — 90-day minimum storage duration bills 30-day-deleted objects for 90 days, and its egress fair-use line (egress ≤ stored volume) sits exactly at our profile, with suspension as the enforcement.
- Backblaze B2 (the named fallback). Cheaper on paper (~$28 vs ~$60/month at the ceiling; both round toward zero at realistic launch sizes), and safe at our 1:1 egress ratio — its free-egress cap of 3x stored volume only starts costing beyond ~3.8x, and overage is billed, never suspended. R2 chosen anyway: egress stays free under any future download-heavy behavior (previews, shorter retention shrinking B2's allowance), and the credential and presigning flows were verified on R2. Both speak S3, so the storage interface keeps the switching cost near zero.
