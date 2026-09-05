# Message brokers for job dispatch — feature comparison for the SeedVr control plane

Research for the wayfinder planning effort. All claims cite primary sources: official docs, vendor pricing pages (Azure via the Retail Prices API, since azure.com pages render "$-" without JS), and first-party GitHub repos. Researched 2026-09-05. Hosting-shape prices cross-check [control-plane-hosting-prices-2026-09.md](control-plane-hosting-prices-2026-09.md).

**Verdict: at this scale (tens of messages/minute, one internal consumer) every candidate is technically sufficient, so the decision is ops footprint + delayed-delivery support + .NET client + hosting fit.** Three candidates separate from the pack: **NATS + JetStream** (single ~15 MB-RAM binary that fits every hosting shape incl. the Hetzner VPS, native delayed scheduling since server 2.12, first-party .NET client, Apache-2.0 with the 2025 license dispute settled in the community's favor); **Azure Service Bus Standard** (~$10/mo, zero ops, the richest native job-queue semantics — scheduled messages, DLQ, TTL — and a first-party .NET SDK; usable from any host over AMQP but only *natural* if the ACA hosting option wins); and **RabbitMQ on CloudAMQP's free shared tier** (most mature queueing semantics, but its open-source delayed-message story regressed in 2025 and self-hosting it is the heaviest of the three). Kafka/Redpanda are disqualified on footprint. Redis Streams works but makes you build delay/DLQ/retry yourself — at which point Postgres `SKIP LOCKED` (§11) does the same job with one fewer service. Ranked shortlist in §13.

---

## 1. Requirements recap and scale reality

- Needed: durable messages, at-least-once + per-message ack, redelivery on consumer crash, delayed delivery (retry backoff), dead-lettering, FIFO-ish ordering, TTL. Nice-to-have: priorities, scheduled messages, request-reply.
- Volume: ≤30–40 concurrent jobs, tens of messages/minute peak (≈50–150K messages/month) — three to five orders of magnitude below where any candidate's throughput limits or paid-tier thresholds start to matter.
- Job *state* stays in Postgres (`JobState` is the source of truth); the broker carries dispatch/coordination only.
- The only consumer is the backend's own dispatcher — GPU workers speak HTTPS both ways and never touch the broker (security implications in §12).

## 2. Feature table

| Feature | NATS JetStream | RabbitMQ | Redis Streams | Azure Service Bus | AWS SQS | Kafka/Redpanda |
|---|---|---|---|---|---|---|
| Durable + at-least-once, per-msg ack | Yes (explicit ack, redelivery on ack timeout) [1] | Yes (quorum queues) [5] | Yes (PEL + XACK) [9] | Yes (PeekLock) [12] | Yes (visibility timeout) [15] | Offset-based, not per-msg ack |
| Exactly-once claims | Publish dedup by msg-id; delivery is at-least-once [1] | No | No | Duplicate detection (Standard+) [12] | FIFO "exactly-once processing" [16] | Transactions (streams, not queues) |
| Delayed/scheduled messages | **Native** (2.12 message schedules) [3] | OSS plugin **archived 2025**; TTL+DLX workaround [7] | DIY (sorted set) [9] | **Native**, all tiers [12] | Native, **max 15 min** [17] | DIY |
| Retry backoff | Native (`MaxDeliver` + `Backoff` list) [2] | Via TTL+DLX topology [6][7] | DIY | DIY (abandon + scheduled resubmit) | DIY (ChangeMessageVisibility) | DIY |
| Dead-letter | Advisory event + DIY capture stream [2] | **Native DLX** (reject/TTL/length/delivery-limit) [6] | DIY (delivery counter) [9] | **Native DLQ** per queue [12] | **Native** (redrive policy, maxReceiveCount) [15] | Convention only |
| Per-message TTL | Yes (2.11 `Nats-TTL` header) [4] | Yes (per-msg/per-queue) [6] | No (XDEL by hand) [9] | Yes [12] | Retention only (≤14 days) [15] | Retention only |
| FIFO-ish ordering | Per subject within a stream [1] | Per queue [5] | Per stream (ID-ordered) [9] | Per queue; strict needs sessions (Standard) [12] | Best-effort (Standard) / strict (FIFO) [16] | Per partition |
| Priorities | Pull-consumer priority groups (2.11) [4] | Yes (priority queues) [8] | No | No | No | No |
| Request-reply | Yes (core NATS) [1] | Direct reply-to | No | No (DIY sessions) | No | No |
| First-party .NET client | **NATS.Net** (official, Apache-2.0, .NET 8/10) [19] | **RabbitMQ.Client 7.x** (official, Broadcom) [20] | StackExchange.Redis (community-standard) | **Azure.Messaging.ServiceBus** (Microsoft) | **AWSSDK.SQS** (AWS) | Confluent.Kafka |
| Self-host idle footprint | <20 MB binary, ~15 MB RAM; ~128 MiB-class with JetStream [21][22] | 4 GiB RAM/node recommended for prod [23] | Few-MB in-memory server; persistence caveats [11] | n/a (managed only) | n/a | ≥2 cores, 2 GB/core [24] |
| Cheapest viable hosted tier | Synadia Cloud free; $49/mo Starter [25] | CloudAMQP Little Lemur **free** (1M msg/mo shared); dedicated $50/mo [26] | Render Key Value (free = non-persistent); Azure Cache C0 ~$16/mo [27][28] | Basic $0.05/M ops (≈$0.01/mo for us); Standard ~$10/mo base [13] | $0.40/M req after 1M free/mo ≈ **$0/mo for us** [16] | (Serverless Kafka tiers exist; out of scope) |
| License status 2026 | Apache-2.0, CNCF; 2025 Synadia BSL attempt settled — stays Apache-2.0 [29] | MPL-2.0 server; Broadcom-maintained [20] | Redis 8 tri-license incl. **AGPLv3** (May 2025); Valkey fork is BSD [30] | Proprietary service | Proprietary service | Apache-2.0 / Redpanda BSL |

## 3. NATS + JetStream

- JetStream layers persistence on core NATS: streams store messages (memory or disk), consumers track a cursor, clients ack each message, and "if a message isn't acknowledged in time, the server redelivers it, which is what gives you at-least-once delivery." [1] https://docs.nats.io/nats-concepts/jetstream
- A work-queue stream (`WorkQueuePolicy` retention) deletes each message once acknowledged — the natural stream type for a dispatch queue ( https://docs.nats.io/nats-concepts/jetstream/streams ).
- Retry backoff is first-class consumer config: `MaxDeliver` bounds attempts, `Backoff` gives a per-attempt delay list (e.g. `[5s, 30s, 300s…]`); backoff applies to ack timeouts. [2] https://docs.nats.io/using-nats/developer/develop_jetstream/consumers
- No automatic DLQ: exhausting `MaxDeliver` publishes an advisory on `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>`; the standard pattern captures advisories into their own stream and republishes failed messages to a DLQ subject — small but real DIY. [2]; pattern write-up: https://www.synadia.com/blog/jetstream-reliable-delivery-dlq-replay
- Delayed/scheduled messages became native in server 2.12 (Sep 2025): a stream with `AllowMsgSchedules` holds a message published with schedule headers and emits it at the target time. [3] https://nats.io/blog/nats-server-2.12-release/ , https://docs.nats.io/release-notes/whats_new/whats_new_212 (2.14 added recurring schedules: https://nats.io/blog/nats-server-2.14-release/)
- 2.11 added per-message TTL (`Nats-TTL` header) and pull-consumer priority groups (overflow + pinning). [4] https://nats.io/blog/nats-server-2.11-release/ , https://www.synadia.com/blog/pull-consumer-priority-groups
- .NET: **NATS.Net** is the official client (Apache-2.0, JetStream/KV/Object Store, .NET 8 & 10, v3 added OpenTelemetry; actively maintained). [19] https://github.com/nats-io/nats.net
- Footprint: single Go binary "less than 20 MB", "small memory footprint (typically ~15MB)" [21] https://docs.nats.io/reference/faq , https://docs.nats.io/concepts/what-is-nats ; with JetStream the sizing guide's example small deployments are in the ~128 MiB-RAM class [22] https://docs.nats.io/learn/deployment/sizing-and-resources . Single node with file storage is a supported topology; clustering is opt-in. Comfortably fits a Hetzner VPS beside the app.
- Hosted: Synadia Cloud — free Personal plan (10 connections, 5 GiB storage) covers this workload on paper; Starter is $49/mo. [25] https://www.synadia.com/cloud
- Licensing: April 2025 Synadia moved to relicense nats-server under BSL and reclaim the trademark; on 2025-05-01 CNCF and Synadia announced the resolution — trademarks assigned to the Linux Foundation, project stays in CNCF under Apache-2.0. [29] https://www.cncf.io/announcements/2025/05/01/cncf-and-synadia-align-on-securing-the-future-of-the-nats-io-project/
- **Fit: strongest self-host candidate — every required semantic native or one small pattern away, at the lightest ops cost.**

## 4. RabbitMQ

- Quorum queues give durable at-least-once with per-message ack; poison-message handling is built in — delivery-limit defaults to 20 since 4.0, after which the message is dropped or dead-lettered. [5] https://www.rabbitmq.com/docs/quorum-queues
- Dead-lettering is native and rich: DLX fires on consumer reject/nack (requeue=false), per-message TTL expiry, queue-length overflow, or delivery-limit. [6] https://www.rabbitmq.com/docs/dlx (TTL: https://www.rabbitmq.com/docs/ttl ; priorities: https://www.rabbitmq.com/docs/priority [8])
- **Delayed messages regressed**: the `rabbitmq-delayed-message-exchange` plugin is archived — it was Mnesia-based (removed in RabbitMQ 4.3), stored delayed messages unreplicated on one node, and the redesigned feature now ships only in VMware's commercial Tanzu RabbitMQ. OSS workaround is the classic per-queue-TTL + DLX retry topology (works, but each backoff step is its own queue). [7] https://github.com/rabbitmq/rabbitmq-delayed-message-exchange
- .NET: official `RabbitMQ.Client` 7.x, dual MPL-2.0/Apache-2.0, maintained by the RabbitMQ team at Broadcom, active. [20] https://github.com/rabbitmq/rabbitmq-dotnet-client
- Ops: the heaviest self-host of the viable three — production checklist recommends a minimum of 4 GiB RAM per node (Erlang runtime, memory/disk watermarks to tune). [23] https://www.rabbitmq.com/docs/production-checklist . It runs far smaller for toy loads, but the vendor guidance is what we'd be operating against.
- Hosted: CloudAMQP **Little Lemur free shared plan** — 1M messages/month, 100 queues, 10K queued messages, 20 connections — genuinely covers our volume; cheapest dedicated RabbitMQ is Sassy Squirrel $50/mo. (Their LavinMQ variant: free tier 2M msgs/mo, dedicated $49.) [26] https://www.cloudamqp.com/plans.html
- **Fit: mature and broker-abstraction-friendly, but delayed-retry needs queue topology gymnastics and self-hosting it is the most RAM-hungry option.**

## 5. Redis Streams

- Consumer groups give real at-least-once: `XREADGROUP` delivers, the pending entries list tracks unacked messages with a delivery counter, `XACK` completes, `XAUTOCLAIM` reassigns messages from crashed consumers. [9] https://redis.io/docs/latest/develop/data-types/streams/
- But **no native delayed delivery, no native DLQ, no per-message TTL** — all three are documented as application patterns (sorted-set scheduler, delivery-count-based manual DLQ stream, `XDEL`). [9] Durability also needs care: default persistence (AOF `everysec`) can lose the last second of writes on crash. [11] https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
- .NET: StackExchange.Redis (MIT, the de-facto standard client) supports streams; no first-party vendor client for this use.
- Licensing saga: 2024's RSALv2/SSPL move spawned the Linux Foundation's Valkey fork; Redis 8 (May 2025) added **AGPLv3** as a third license option, making core Redis OSI-open again. [30] https://redis.io/blog/agplv3/ , https://redis.io/legal/licenses/
- Hosted per our shapes: Render Key Value runs Valkey 8, free tier is explicitly **non-persistent** ("data persistence is not available for free Key Value instances") — disqualifying free for a durable queue [27] https://render.com/docs/key-value ; Azure Cache for Redis Basic C0 is ~$16/mo (service retiring 2028-04-30 in favor of Azure Managed Redis) [28] https://azure.microsoft.com/en-us/pricing/details/cache/ ; on Hetzner it's a tiny container.
- **Fit: viable only if Redis is already deployed for caching; as a dedicated broker you hand-build exactly the features you'd buy a broker for.**

## 6. Azure Service Bus (usable from any host)

- Cross-provider by design: plain AMQP 1.0 (or AMQP-over-WebSockets/HTTPS) from anywhere — a Hetzner or Render process connects the same as an Azure one; at our message sizes egress is pennies. Basic tier has queues, scheduled messages, and 256 KB messages but no topics/sessions/transactions/duplicate-detection (Standard adds those). [12] https://azure.microsoft.com/en-us/pricing/details/service-bus/
- Semantics are the most complete of any candidate out of the box: PeekLock per-message ack, automatic redelivery, per-entity and per-message TTL, native DLQ with MaxDeliveryCount, native scheduled messages (arbitrary future time). https://learn.microsoft.com/en-us/azure/service-bus-messaging/service-bus-dead-letter-queues , https://learn.microsoft.com/en-us/azure/service-bus-messaging/message-sequencing
- One consumer-side nuance: the PeekLock lock duration maxes out at 5 minutes and must be renewed by the client for longer handling ( https://learn.microsoft.com/en-us/azure/service-bus-messaging/message-transfers-locks-settlement ). Harmless here — the dispatcher's handling is seconds; the 10–30 min job runs on the GPU worker, not inside the message handler.
- Price (Retail Prices API, westeurope): Basic = $0.05 per million operations, no base — effectively **~$0.01/month** for us; Standard = $10.00/month base (or $0.013441/hr) with the first ~13M operations included, then tiered up to $0.80/M. [13] https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Service%20Bus%27
- .NET: `Azure.Messaging.ServiceBus` is Microsoft's first-party SDK; docs quality is the best in this field.
- **Fit: if the ACA hosting option wins, this is the obvious choice; if Hetzner/Render wins, it works fine but drags in an Azure subscription solely for a queue.**

## 7. AWS SQS (usable from any host)

- HTTPS API from anywhere; $0.40/M requests Standard, $0.50/M FIFO, with a permanent 1M-requests/month free tier — **$0/month at our volume** (long-polling receive calls included). [16] https://aws.amazon.com/sqs/pricing/ (per-request data transfer to/from internet billed at standard rates, negligible here [15])
- Semantics: at-least-once with visibility-timeout redelivery (timeout configurable up to 12 hours — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html ); native DLQ via redrive policy (`maxReceiveCount`); FIFO queues add ordering and "exactly-once processing" (5-min dedup). Retention max 14 days; message size up to 1 MiB since 2025-08-04 ( https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-sqs-max-payload-size-1mib ). [15] https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-quotas.html
- The gap: **delays cap at 15 minutes** (`DelaySeconds` / message timers) — AWS's own docs punt longer scheduling to EventBridge Scheduler, a second service. [17] https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-delay-queues.html . 15 min covers retry backoff for dispatch, just not long-horizon scheduling.
- .NET: first-party `AWSSDK.SQS`. **Fit: cheapest managed option and a fine queue, but it's an AWS account + IAM surface acquired for one feature, on a project otherwise showing no AWS gravity.**

## 8. Kafka / Redpanda — disqualified on footprint

- Log-streaming platforms, not job queues: no per-message ack (consumer offsets instead), no native delayed delivery, dead-lettering by convention only.
- Redpanda's own production floor is 2 physical cores (4 recommended) and ≥2 GB RAM per core with NVMe strongly recommended [24] https://docs.redpanda.com/current/deploy/deployment-option/self-hosted/manual/production/requirements/ — that alone exceeds the entire Hetzner VPS budget.
- Kafka adds a JVM cluster (KRaft controllers, per-broker heap) on top of the same semantic mismatch ( https://kafka.apache.org/documentation/#kraft ). Wrong tool at 10⁻⁴ of its design scale; cut.

## 9. .NET abstraction layer (lock-in hedge while hosting is undecided)

- **MassTransit**: v8 is Apache-2.0 and community-supported through end of 2026; **v9 went commercial** under Massient (prerelease Q3 2025, release Q1 2026, paid production license). Transports: RabbitMQ, Azure Service Bus, Amazon SQS, ActiveMQ, SQL (incl. **PostgreSQL as a transport**), Kafka/EventHub riders — **no NATS**. https://github.com/MassTransit/MassTransit , https://masstransit.massient.com/introduction/v9-announcement , https://massient.com/license
- **Wolverine** (JasperFx): MIT-licensed, paid support optional; transports include RabbitMQ, Azure Service Bus, SQS/SNS, GCP Pub/Sub, Kafka, **NATS**, MQTT, Pulsar, Redis, and **PostgreSQL/SQL Server as transports**. The only abstraction covering every candidate here plus the Postgres alternative. https://wolverinefx.net/guide/messaging/transports/ , https://wolverinefx.net/
- **NServiceBus** (Particular) covers RabbitMQ/ASB/SQS/SQL but has always been commercially licensed ( https://particular.net/pricing ) — no advantage over the two above for this project; noted and cut.
- Counterpoint: at one producer + one consumer in the same codebase, the raw first-party client (NATS.Net / Azure SDK / RabbitMQ.Client) is a few hundred lines and zero framework risk; an abstraction earns its keep mainly as a hosting-decision hedge.

## 10. What each broker looks like on each hosting shape

The hosting decision (parallel ticket) is between Azure Container Apps + Flexible Server, Render, and Hetzner VPS + Coolify. Broker cost and shape per combination, using the compute prices verified in [control-plane-hosting-prices-2026-09.md](control-plane-hosting-prices-2026-09.md) and the broker prices cited above:

| Broker | ACA + Azure | Render | Hetzner VPS + Coolify |
|---|---|---|---|
| NATS JetStream | Always-on min-replica container app (a 0.25 vCPU/0.5 GiB replica; ACA bills it active while the dispatcher holds a connection) — or skip self-host and use Synadia Cloud free | Extra private service; smallest paid instance 0.5 CPU/512 MB = **$7/mo** (no free tier for private services) — or Synadia Cloud free ($0) | Docker/Compose container beside the app; ~15 MB RAM + file storage — **$0 marginal**, the natural fit |
| RabbitMQ | Container app is possible but Erlang + watermarks on ACA is awkward; CloudAMQP free (shared, any cloud) is the sane route | CloudAMQP free; self-host on a $7 instance is below RabbitMQ's guidance | Container via Coolify one-click; runs at small scale but is the RAM hog on a 4–8 GB VPS (vendor floor 4 GiB [23]); CloudAMQP free avoids that |
| Redis/Valkey Streams | Azure Cache C0 ~$16/mo [28] or a container app | **Render Key Value**, but free tier is non-persistent [27] → paid instance required for a durable queue | Tiny container, $0 marginal — but all queue semantics are DIY (§5) |
| Azure Service Bus | **Native habitat**: same subscription/VNet, Entra auth; Basic ≈ $0.01/mo, Standard $10/mo [13] | Reachable over AMQP/WebSockets from Render; adds an Azure subscription + WAN hop for the queue alone | Same as Render: works, but an odd dependency for an otherwise Azure-free stack |
| AWS SQS | Cross-cloud over HTTPS, $0/mo at our volume [16] — but an AWS account grafted onto an Azure stack | Same: $0/mo, HTTPS long-polling from Render works fine | Same; cheapest managed option from a VPS, at the price of AWS IAM surface |

Pattern: on Hetzner, self-hosted NATS is effectively free; on Render, every self-host option costs a $7+ instance so hosted free tiers (Synadia, CloudAMQP, SQS) compete well; on ACA, Service Bus dominates.

## 11. The elephant: Postgres as the queue

The team is moving away from Postgres-backed dispatch, but it must be weighed honestly since Postgres stays regardless as the system of record.

- `SELECT … FOR UPDATE SKIP LOCKED` is the documented primitive: "any selected rows that cannot be immediately locked are skipped" — exactly a multi-worker job-claim without lock contention ( https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE ). Delayed retry is a `next_attempt_at` column; DLQ is a status value; TTL is a `DELETE`.
- At tens of jobs/minute, a 1–5 s poll is unmeasurable load, and the sibling hosting research already noted a `SKIP LOCKED` jobs table makes deploy blips harmless (control-plane-hosting-prices-2026-09.md, observation 5).

- **What a broker adds**: push/long-poll wakeups instead of poll loops; ack/redelivery/backoff/DLQ as configuration instead of code we test ourselves; clean separation if a second consumer process ever appears; per-broker extras (NATS request-reply, ASB scheduled messages).
- **What a broker costs here**: a second stateful service to run, secure, monitor, and upgrade — and the **dual-write problem**: job state lives in Postgres, so "commit `JobState` change + publish dispatch message" spans two systems that cannot share a transaction.
- The standard dual-write remedy is the **transactional outbox** — write the message into an outbox table in the same DB transaction, relay it to the broker afterwards ( https://microservices.io/patterns/data/transactional-outbox.html ) — which reintroduces a polled Postgres table *in front of* the broker. The lighter alternative is accepting at-least-once dispatch with idempotent consumers keyed on job id.
- Both MassTransit and Wolverine ship Postgres transports/outboxes precisely because this hybrid is common (§9) — Wolverine's Postgres transport is effectively a Postgres-backed queue behind a broker-shaped API.

Presented without advocacy: the broker buys real machinery and future headroom; the Postgres path buys one fewer moving part and transactional dispatch, at this scale with no performance penalty.

## 12. Security note (internal-only topology)

The broker faces only our own dispatcher over a private network — never the public internet, never the GPU workers (they get HTTPS calls and push HTTPS back). So multi-tenancy features are irrelevant and the bar is: TLS + one credential + not exposed. All candidates clear it:

- NATS supports TLS, token, user/password and NKey auth, but **starts open by default — auth must be enabled explicitly** ( https://docs.nats.io/running-a-nats-service/configuration/securing_nats ).
- RabbitMQ's default `guest` user can only connect from localhost; per-vhost users otherwise ( https://www.rabbitmq.com/docs/access-control ).
- Redis needs `requirepass`/ACLs; protected mode is on by default and refuses remote connections until configured ( https://redis.io/docs/latest/operate/oss_and_stack/management/security/ ).
- ASB (SAS/Entra ID) and SQS (IAM/SigV4) are TLS-only managed endpoints. The trade: used from Hetzner or Render they put broker traffic on the public internet — encrypted, but a WAN dependency inside the dispatch path.
- Self-hosted on ACA internal ingress, Render private services, or a Hetzner Docker network, the broker port is simply never published — the internal-only topology makes the hardening story short for every candidate.

## 13. Ranked shortlist

1. **NATS + JetStream** — the only candidate that is a first-class citizen on all three hosting shapes (a ~15 MB sidecar container on Hetzner/Render/ACA, or Synadia Cloud free), with native scheduling/backoff/TTL, an official modern .NET client, and settled Apache-2.0 governance.
   Its one gap — no automatic DLQ — is a small, well-documented advisory-handler pattern.
2. **Azure Service Bus (Basic → Standard)** — if hosting lands on ACA: ~$0.01–10/mo, zero ops, every required semantic native, best-in-class first-party .NET SDK.
   Demoted only because it couples the broker choice to the still-open hosting decision; from Hetzner/Render it means an Azure subscription and a WAN hop for the queue alone.
3. **RabbitMQ via CloudAMQP Little Lemur (free)** — zero-cost managed entry with the most battle-tested queue semantics and the widest abstraction-layer support (MassTransit and Wolverine both).
   Penalized for the archived delayed-message plugin (retry backoff becomes a TTL+DLX queue topology) and a 4 GiB-class self-host footprint if we ever pull it in-house.

Cross-cutting recommendation regardless of pick: keep dispatch idempotent on job id (every candidate is at-least-once in practice), and decide explicitly between a transactional outbox and accepted-duplicate dispatch before wiring the broker to `JobState` transitions (§11).
