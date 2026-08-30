# Vast.ai serverless is the primary worker orchestration path

Jobs are dispatched to a Vast.ai serverless endpoint — the platform's autoscaler recruits, benchmarks and retires marketplace GPU workers — rather than to a self-managed pool of rented instances. The choice sits behind a dispatcher seam: the serverless dispatcher is the only implementation built, and a managed-pool dispatcher over the plain instance REST API stays a designed-but-unbuilt fallback should the young platform disappoint. The job contract is identical in both worlds (workflow JSON in, request_id correlation, S3 delivery out, completion webhook), which is what makes the seam cheap.

Scaling policy: a warm floor of two fully provisioned Ready workers at all times, with a replacement recruited the moment a warm worker takes a job — Inactive (stopped) workers are ruled out entirely, since the host GPU is too likely re-rented for reactivation to be dependable. The worker ceiling (~30–40) is an accident guard, not a capacity plan; the endpoint scales back down to the floor after ~10 idle minutes. On-Demand pricing only (Interruptible kills long single-shot upscales mid-run), one job per worker (platform-enforced ComfyUI serial rule). The platform Vast account keeps auto-top-up on: availability is preferred at every branch, with runaway spend bounded by the worker ceiling and surfaced by alerting rather than by a hard balance stop.

## Considered Options

- Self-managed pool from day one. Rejected — reimplements recruitment, benchmarking, warm pools and queue-aware scaling the platform already provides at the same per-second GPU rates.
- Hybrid with both paths built. Rejected — doubles the build for no launch benefit.
- Serverless-primary behind a dispatcher seam (chosen): the fallback stays reachable because the job contract does not change.
- Scale-to-zero or an Inactive warm pool instead of Ready workers. Rejected — instant starts are worth the idle rent at launch scale, and a stopped instance can stall indefinitely when its GPU was re-rented.
