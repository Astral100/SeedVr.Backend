# Storage research: Cloudflare R2 for video upscaling pipeline

Resolves GitHub issue #3 (wayfinder ticket "Cloudflare R2 storage validation").
Researched 2026-08-27 from primary sources; every claim cites its source URL.

**Verdict: R2 is the right choice** for this workload (multi-GB videos, uploads from
users' browsers, pulls/pushes by Vast.ai GPU workers in arbitrary datacenters, user
downloads), primarily because egress is genuinely free and the S3 API surface we need
(multipart upload, presigned PUT/GET, lifecycle expiry) is fully supported. Caveats in
section 7. Backblaze B2 is the credible cheaper fallback; Wasabi is disqualified by its
90-day minimum storage duration; S3 is ~7x the cost at our egress profile.

---

## 1. R2 pricing

Source: https://developers.cloudflare.com/r2/pricing/

| Item | Standard | Infrequent Access |
|---|---|---|
| Storage | $0.015 / GB-month | $0.01 / GB-month |
| Class A ops (writes/lists) | $4.50 / million | $9.00 / million |
| Class B ops (reads) | $0.36 / million | $0.90 / million |
| Egress | **$0** | **$0** |

- **Class A** includes: `PutObject`, `CopyObject`, `CreateMultipartUpload`,
  `UploadPart`, `UploadPartCopy`, `CompleteMultipartUpload`, `ListObjects`,
  `ListParts`, `PutBucketLifecycleConfiguration`, etc.
- **Class B** includes: `GetObject`, `HeadObject`, `HeadBucket`,
  `GetBucketLifecycleConfiguration`, etc.
- **Free tier** (Standard only): 10 GB-month storage, 1M Class A ops/month,
  10M Class B ops/month.
- **Zero egress, verified**: the pricing page states egress from R2 is free whether
  accessed via the Workers API, the S3 API, or `r2.dev` domains. No metered egress
  line item exists, and the pricing page attaches no fair-use cap to egress.
  (`r2.dev` public dev domains are rate-limited and not for production — see §4 —
  but S3-API egress, which is what we use, carries no documented cap.)
- Infrequent Access has a 30-day minimum storage duration (charged even if deleted
  earlier) — irrelevant for us; Standard has no minimum duration.

## 2. S3 API compatibility

Sources:
- https://developers.cloudflare.com/r2/api/s3/api/
- https://developers.cloudflare.com/r2/api/s3/presigned-urls/
- https://developers.cloudflare.com/r2/platform/limits/
- https://developers.cloudflare.com/r2/objects/multipart-objects/

**Supported and relevant to us**
- Full multipart upload workflow: `CreateMultipartUpload`, `UploadPart`,
  `CompleteMultipartUpload`, `UploadPartCopy`
  (https://developers.cloudflare.com/r2/api/s3/api/).
- `GetObject`/`PutObject`/`HeadObject`/`DeleteObject`/`ListObjectsV2`/`CopyObject`,
  conditional headers (`If-Match`, `If-None-Match`, `If-(Un)Modified-Since`)
  (https://developers.cloudflare.com/r2/api/s3/api/).
- Presigned URLs, SigV4, for **GET, HEAD, PUT, DELETE**; validity from 1 second up to
  **7 days (604,800 s)** max; each URL authorizes a single operation on a single
  object (https://developers.cloudflare.com/r2/api/s3/presigned-urls/).
- Storage classes `STANDARD` and `STANDARD_IA`
  (https://developers.cloudflare.com/r2/api/s3/api/).
- Works with the standard AWS SDKs (incl. AWSSDK.S3 for .NET) pointed at the account
  endpoint with region `auto`.

**Limits** (https://developers.cloudflare.com/r2/platform/limits/,
https://developers.cloudflare.com/r2/objects/multipart-objects/)
- Max object size: ~5 TiB (4.995 TiB via multipart).
- Max single-part upload (incl. presigned PUT): ~5 GiB (4.995 GiB).
- Multipart: part size 5 MiB–5 GiB, max 10,000 parts.
- Concurrent writes to the *same key*: 1/second (429 above that) — no issue for our
  unique-key-per-job layout.
- The 1,200 requests / 5 min limit applies to the Cloudflare REST API (bucket
  management), listed on the limits page — plan data-plane traffic via the S3
  endpoint, not the account API.

**Incompatibilities / gotchas**
- **All multipart parts except the last must be the same size** — stricter than AWS
  S3 (https://developers.cloudflare.com/r2/objects/multipart-objects/: "All parts
  except the last must be the same size"). Fixed-size chunking in our uploader; the
  AWS SDK's `TransferUtility` already does this.
- **No POST policy uploads** (multipart form uploads via HTML forms) — browser
  direct upload must use presigned **PUT** (or presigned `UploadPart` URLs for >5 GiB
  files) (https://developers.cloudflare.com/r2/api/s3/presigned-urls/).
- Presigned URLs work only on the S3 API domain (`<account>.r2.cloudflarestorage.com`),
  **not on custom domains** (https://developers.cloudflare.com/r2/api/s3/presigned-urls/).
- Only region `auto`; no object tagging, no ACLs, no SSE-KMS
  (https://developers.cloudflare.com/r2/api/s3/api/). None of these block us
  (lifecycle filtering is by prefix, not tags).
- Re-uploading the same part number replaces the previous part; if the retry fails
  the original part is lost and must be re-uploaded
  (https://developers.cloudflare.com/r2/api/s3/api/).

## 3. Lifecycle / TTL

Source: https://developers.cloudflare.com/r2/buckets/object-lifecycles/

- Rules support: **object expiration (delete after N days)**, transition to
  Infrequent Access, and **abort incomplete multipart uploads after N days**.
- Rules filter by **prefix** — so `inputs/` and `outputs/` can carry independent
  30-day expiry rules, plus an abort-incomplete-multipart rule (important: abandoned
  browser uploads would otherwise accrue storage forever).
- Configurable via dashboard, Wrangler, or S3 `PutBucketLifecycleConfiguration`.
- Max 1,000 rules per bucket; expiry typically processes within 24 hours (may lag on
  large buckets); expiration takes precedence over a conflicting transition within
  the same 24 h window.

This covers the input/output TTL requirement exactly; no app-side reaper needed
(though a DB-driven reaper remains an option for job-level semantics).

## 4. Performance from arbitrary external datacenters (Vast.ai workers)

Sources:
- https://developers.cloudflare.com/r2/reference/data-location/
- https://developers.cloudflare.com/r2/platform/limits/
- https://developers.cloudflare.com/r2/examples/rclone/

- An R2 bucket is stored in **one region**, chosen automatically from the location of
  the bucket-creation call, or steered with a **location hint** (`wnam`, `enam`,
  `weur`, `eeur`, `apac`, `oc`) — hints are best-effort, set only at creation
  (https://developers.cloudflare.com/r2/reference/data-location/). There is no
  multi-region replication.
- Clients anywhere reach R2 through Cloudflare's anycast edge; the docs frame
  location choice as a latency optimization but publish **no throughput numbers or
  SLA** for single connections. Practical implication: a Vast.ai worker in an
  arbitrary datacenter enters Cloudflare at its nearest PoP regardless of where the
  bucket lives — good and consistent reachability, but per-connection throughput
  from a random host is not guaranteed anywhere in the docs.
- The documented lever for large-transfer throughput is **parallelism**: multipart
  parts upload concurrently, and Cloudflare's own rclone guide recommends raising
  upload concurrency for large files
  (https://developers.cloudflare.com/r2/examples/rclone/). Workers should pull
  inputs with parallel ranged GETs and push outputs as concurrent multipart parts
  (Class B/A op costs of extra parallelism are negligible at our volume, and free
  egress means retries cost nothing).
- `r2.dev` public domains are explicitly rate-limited ("hundreds of requests/second"
  may 429) and not meant for production
  (https://developers.cloudflare.com/r2/platform/limits/) — workers and users must
  use presigned S3-endpoint URLs, which we planned anyway.
- Risk assessment: workers in random locations are **not a blocker** — free egress
  makes cross-continent pulls cost-free, and parallel transfer masks per-connection
  latency. Pick the bucket location hint to match the primary user base (uploads and
  downloads are user-facing; worker transfer time is background). Benchmark from a
  couple of Vast.ai regions during the spike, since no provider documents guaranteed
  throughput to arbitrary hosts.

## 5. Comparison: R2 vs S3 vs B2 vs Wasabi

| Axis | R2 | AWS S3 (Standard, us-east-1) | Backblaze B2 | Wasabi |
|---|---|---|---|---|
| Storage | $0.015/GB-mo [1] | $0.023/GB-mo [2] | $6.95/TB-mo (~$0.00695/GB) [3] | from $7.99/TB-mo [4] |
| Egress | Free [1] | $0.09/GB after 100 GB/mo free [2] | Free up to 3x avg monthly storage, then $0.01/GB; unlimited via partner CDNs [3] | "Free" but fair-use: monthly egress must not exceed stored volume, else Wasabi may limit/suspend [5] |
| Requests | $4.50/M Class A, $0.36/M Class B, generous free tier [1] | $0.005/1k PUT, $0.0004/1k GET [2] | Class A/B/C free; Class D $0.004/10k [3] | Free [4] |
| S3 compat: multipart + presigned | Yes (PUT/GET presigned; no POST forms; equal part sizes) [6][7] | Native | Yes — presigned up/download supported; no POST presigned, no tagging/ACL-per-object [8] | Yes (S3-compatible API) [4] |
| Lifecycle expiry | Yes, prefix rules + abort-incomplete-multipart [9] | Native | Yes (lifecycle rules) [8] | Yes, but 90-day min storage duration: objects deleted at 30 days still billed for 90 [10] |
| Min storage duration | None (Standard) [1] | None (Standard) | None | **90 days (pay-as-you-go)** [10] |

[1] https://developers.cloudflare.com/r2/pricing/
[2] https://aws.amazon.com/s3/pricing/
[3] https://www.backblaze.com/cloud-storage/pricing
[4] https://wasabi.com/pricing
[5] https://docs.wasabi.com/docs/how-do-wasabis-fair-use-policies-work
[6] https://developers.cloudflare.com/r2/api/s3/api/
[7] https://developers.cloudflare.com/r2/api/s3/presigned-urls/
[8] https://www.backblaze.com/docs/cloud-storage-s3-compatible-api
[9] https://developers.cloudflare.com/r2/buckets/object-lifecycles/
[10] https://docs.wasabi.com/docs/how-does-wasabis-minimum-storage-duration-policy-work

## 6. Cost model at launch scale

Assumptions: 50 active users x 10 jobs/month = **500 jobs/month**; each job = 2 GB
input + 6 GB output = 8 GB written; 30-day retention with uniform arrivals gives a
steady state of **4,000 GB (4 TB) stored**; egress = worker pulls input (2 GB) + user
downloads output once (6 GB) = **4,000 GB (4 TB)/month**. Operations (100 MiB parts):
~500 x (21 input-part ops + 61 output-part ops + ~10 misc) ≈ **~46k Class A** and
well under 1M Class B — inside R2's free op tier and negligible everywhere.

| Provider | Storage | Requests | Egress | **Total/mo** |
|---|---|---|---|---|
| **R2** | 4,000 GB x $0.015 = $60.00 | $0 (free tier: 1M Class A, 10M Class B) | $0 | **~$60** |
| **S3** | 4,000 GB x $0.023 = $92.00 | ~46k PUT x $0.005/1k ≈ $0.23 | (4,000-100) GB x $0.09 = $351.00 | **~$443** |
| **B2** | 4 TB x $6.95 = $27.80 | $0 (Class A/B/C free) | $0 (4 TB ≤ 3 x 4 TB allowance) | **~$28** |
| **Wasabi** | 4 TB x $7.99 = $31.96, **but** 30-day-deleted objects billed for 90 days -> effective ~12 TB-mo = ~$95.88 | $0 | "$0", and 4 TB egress = 4 TB stored sits exactly at the fair-use line | **~$96 + suspension risk** |

Scaling note: costs scale linearly with jobs; at 10x volume (5,000 jobs/mo, 40 TB
stored/egressed) R2 ≈ $600/mo vs S3 ≈ $4,430/mo — the egress line dominates S3 and
the gap widens with any re-downloads, retries, or multi-worker pulls, all of which
are free on R2/B2.

## 7. Verdict and caveats

**R2 — recommended.** Free egress fits a pipeline whose defining trait is that every
byte stored is also transferred out at least once (worker pull + user download), and
often more (retried jobs, re-downloads). The needed API surface — presigned PUT for
browser upload, presigned GET for worker/user download, multipart for 6 GB outputs,
prefix-scoped 30-day expiry, abort-incomplete-multipart — is all documented and
supported. .NET works via the standard AWS S3 SDK against the R2 endpoint.

Caveats to carry into the design:
1. **Equal part sizes** in multipart uploads (stricter than S3) — use fixed-size
   chunking everywhere.
2. **No POST policy uploads** — browser direct upload is presigned PUT (fine for
   2 GB inputs, under the ~5 GiB single-PUT cap); larger inputs later would need
   presigned `UploadPart` URLs.
3. **Presigned URLs max 7 days** and only on the S3 API domain, not custom domains.
4. **Single-region bucket** — set a location hint (`enam`/`weur`/...) at creation to
   match the primary user base; hints cannot be changed later. No throughput SLA is
   documented for arbitrary external clients; benchmark from 2-3 Vast.ai regions
   during the tracer-bullet phase and rely on parallel multipart/ranged transfers.
5. **Add the abort-incomplete-multipart lifecycle rule on day one** — abandoned
   browser uploads otherwise accumulate invisible storage cost.
6. **B2 is the fallback** (~$28/mo vs ~$60/mo at launch scale, S3-compatible incl.
   presigned URLs and lifecycle). Since both speak S3, keeping the storage client
   behind our own interface keeps switching cost near zero. Wasabi is ruled out
   (90-day minimum duration vs our 30-day retention; egress fair-use line exactly at
   our profile). S3 is ruled out on egress cost (~7x total).
