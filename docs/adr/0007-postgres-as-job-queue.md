# Postgres is the job queue

Pending jobs wait in the PostgreSQL jobs table and are claimed by the dispatcher with `FOR UPDATE SKIP LOCKED`; there is no message broker. The decisive facts: under push-dispatch (ADR 0003) the queue has exactly one client — our own backend — so no external party ever connects to it, which dissolved the security concern that originally argued against Postgres-as-queue; and at launch scale (~300 jobs/month, single-digit concurrency) a broker adds an always-on service to deploy, monitor and secure without adding a capability the pipeline needs.

What Postgres-as-queue buys outright: claiming a job and recording its state change is one transaction, so the dual-write problem (job row saved but queue message lost, or vice versa) structurally cannot occur — no outbox, no idempotent-republish sweep. `SKIP LOCKED` also makes scale-out safe for free: multiple API instances each running an embedded dispatcher cannot claim the same job twice.

Consequence: queue throughput is bounded by the database, and queue semantics (delays, dead-lettering, fan-out) must be hand-expressed as columns and sweeps rather than broker features. Acceptable at this scale; the dispatch code stays behind its own seam, so swapping the queue is contained.

## Considered Options

- Postgres `SKIP LOCKED` on the jobs table (chosen) — one less service, transactional with job state, the boring default for a single internal consumer.
- NATS + JetStream — the broker-comparison winner (`docs/research/message-brokers.md`): ~15 MB container, fits every shortlisted hosting shape, native delays/backoff, official .NET client. The pick if a real broker is ever wanted, but reintroduces the dual-write problem and a second service today.
- Azure Service Bus — managed and mature, but only sensible if hosting lands on Azure Container Apps; couples the queue to a hosting decision that is still open.
