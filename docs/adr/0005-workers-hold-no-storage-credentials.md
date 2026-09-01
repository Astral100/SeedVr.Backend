# Workers hold no storage credentials

GPU workers run on Vast.ai marketplace machines — rented hardware whose host operator has root on the Docker host and can read anything a container holds, a risk Vast's own security guidance acknowledges and its trust tiers only reduce. Therefore no S3 credential of any kind travels to a worker. Inputs reach the worker as presigned GET URLs embedded in the workflow JSON; outputs are uploaded by our own service bundled into the worker image, using a per-job presigned PUT URL minted by the backend for the predetermined output key (`out/{jobId}/{fileId}`). A stolen presigned URL grants one operation on one object for a bounded time — there is nothing to rotate and no bucket-wide blast radius.

This rules out the ai-dock wrapper's built-in S3 upload mode (`input.s3`), which requires a real access key and secret in every request: R2's static tokens have no write-only level (a leak reads, overwrites and deletes every object in scope until rotated, silently), and R2's temporary credentials need a session token the stock wrapper cannot carry. The bundled uploader also puts the output key layout under our control, which the wrapper (no prefix setting) never offered.

Consequence: outputs beyond the single-PUT limit (~5 GiB) need backend-orchestrated multipart presigning; at the typical ≤ ~300 MB output this is a rare path, not the norm.

## Considered Options

- Static bucket-scoped read-write token in each request (Vast's own serverless template pattern). Rejected — a silent leak from any rented host exposes all customer files until a rotation nobody is prompted to run; write-only static tokens do not exist on R2.
- Per-job temporary credentials (bucket/prefix-scoped, short TTL, optionally write-only via local signing). Viable and researched, but requires threading a session token through the upload path; presigned PUTs achieve stricter containment with less machinery.
- Per-job presigned PUT/GET URLs, zero credentials on the host (chosen) — the industry-standard answer for untrusted workers.
