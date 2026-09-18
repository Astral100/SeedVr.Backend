# HTTP status codes report promise delivery (the promise rule)

Every endpoint promises one thing, and the HTTP status answers exactly one question: **did the endpoint deliver its promise?** Domain findings — even negative ones — are data in a 2xx body; status codes are never used to tunnel them, and 200 is never used to wrap a refusal. Decided while resolving [API contract v1 (#10)](https://github.com/Astral100/SeedVr.Backend/issues/10); every future endpoint grounds its status-code choices on this rule.

- **2xx** — the promise was delivered. Whatever the operation *found* rides in the body as data, even when the finding is negative (an invalid uploaded file, a job whose status is Failed).
- **4xx** — the request cannot be honored as sent: malformed, referencing something that does not exist or is not usable, or conflicting with current state. Always carries a machine-readable, stable `code` the client branches on.
- **5xx** — the system failed; the request was fine. Includes the graceful-degradation 503 with a friendly "temporarily unavailable" message when Postgres is down.

## Worked examples (normative)

| Situation | Status | Why |
|---|---|---|
| Upload confirmation finds the file is not a valid video | 200, verdict `invalid` + reason | Confirmation promises a verdict; it delivered one |
| Polling a job that failed during rendering | 200, status `Failed` + reason | The poll promises current state; failure is a state |
| Submit references an input file whose bytes are gone (expired / never completed) | 422, `code: input-file-not-usable` | Submit promises a created job; the request cannot be honored |
| Submit duplicates a still-active identical job | 409, `code: duplicate-active-job` + existing job id | No job created; state conflict |
| GET on a job id that does not exist | 404, `code: job-not-found` | The URL names a missing resource — the only 404 case; a broken reference *inside a body* is 422, never 404 |
| Database unavailable | 503, friendly message | The system failed |

## Considered options

- **Every negative outcome as an HTTP error**: floods error-rate dashboards with everyday user mistakes (wrong file picked), making real API failures indistinguishable from user noise.
- **Everything 200 with errors inside the body**: blinds the entire HTTP toolchain — the status-code-keyed Prometheus alerting on the telemetry box, load-balancer logs, browser devtools — which would report a healthy API while everything fails.

The promise rule is the principled hybrid: real status codes for real refusals and failures, domain findings as data.
