# Worker-side storage flow live probe

Probe harness for wayfinder ticket [#18](https://github.com/Astral100/SeedVr.Backend/issues/18)
(mirrored as POC PLAN.md Milestone 13). Nothing in the chosen no-credentials
worker storage flow (ADR 0005) has ever run live; this harness runs it once,
end to end, against a real Vast.ai instance and a real R2 bucket, and records
what actually happens. The worker-image ticket (#17) bakes what this verifies.

## What it verifies

1. **Video input via URL** — a presigned R2 GET URL as `LoadVideo.file` inside
   `workflow_json`; the wrapper documents URL download for images only.
2. **Result pickup from disk** — `/result`'s `local_path` is readable by a
   second process, and the file is complete (size-stable + md5) at `completed`.
3. **Presigned PUT upload** — the real output plus a synthetic ~300 MB file
   PUT to predetermined keys, with throughput measured from a non-US host.
4. **Progress relay** — a second process watches ComfyUI's WebSocket alongside
   the wrapper-driven run and POSTs percent updates outward.
5. **WAL archiving (from #9 / ADR 0009)** — barman-cloud archive → backup →
   restore round-trip against R2, in a local throwaway postgres:17 container.

## How to run

```bash
bash probes/storage/wizard.sh
```

The wizard walks through everything: R2 bucket + S3 token creation, renting a
non-US Vast.ai instance from the SeedVR2 template, arming the on-instance
probe (one line pasted into a Jupyter terminal), the run itself, result
collection, the WAL probe, and teardown. About 75 minutes, most of it waiting.

## Pieces

- `wizard.sh` — the interactive walkthrough (generated from the /wizard skill).
- `driver.py` — local driver; the only place storage credentials are used.
- `worker_probe.py` — runs on the instance; stands in for the future bundled
  worker service. Gets everything via presigned URLs — no credentials.
- `barman_probe.sh` — the WAL round-trip in Docker.
- `SeedVR2_HD_video_upscale_api.json`, `input.mp4` — workflow and 10s test
  clip, copied from the POC (`Astral100/SeedVr`).
- `probe.env` (gitignored) — credentials and endpoints, written by the wizard.
- `out/` (gitignored) — everything captured: `state.json`,
  `driver-result.json`, `probe-log.json`, `barman-probe.log`, the downloaded
  output. **Bring this directory to a wayfinder session to resolve #18.**
