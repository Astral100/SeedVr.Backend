#!/usr/bin/env python3
"""Local driver for the worker-side storage flow probe (wayfinder ticket #18).

Runs on the developer machine, which is the only place storage credentials
live (ADR 0005). Subcommands, in wizard order:

  r2check   validate R2 credentials with a put/get/delete round-trip
  check     confirm the Vast instance's wrapper answers through the proxy
  prepare   upload input video + on-instance probe to R2, presign all URLs,
            print the one-line command to paste into the instance's Jupyter
  submit    submit the wrapper job (presigned GET URL as the video input)
            and poll /result from outside, exactly as the backend would
  collect   pull the on-instance probe log and the uploaded output back
            from R2, verify integrity, print the consolidated summary

State between subcommands lives in out/state.json. Config comes from
probe.env next to this script (written by wizard.sh).
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
STATE = OUT / "state.json"
PRESIGN_SECONDS = 6 * 24 * 3600  # under R2's 7-day presign cap
SYNTHETIC_MB = 300


def save_state(state):
    # Atomic: a crash mid-write must never leave a truncated state.json —
    # an unreadable state hides whether a GPU job was already submitted.
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def check_presigns_fresh(state):
    age = time.time() - state.get("prepared_at", 0)
    if age > PRESIGN_SECONDS - 3600:
        sys.exit("the presigned URLs from 'prepare' are older than ~6 days "
                 "and have expired — re-run the wizard from stage 6 "
                 "(wizard.sh --from=6) to re-prepare before continuing.")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env():
    env = {}
    path = HERE / "probe.env"
    if not path.exists():
        sys.exit(f"missing {path} — run wizard.sh first")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def s3_client(env):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def presign(s3, bucket, method, key):
    op = {"GET": "get_object", "PUT": "put_object"}[method]
    return s3.generate_presigned_url(
        op, Params={"Bucket": bucket, "Key": key}, ExpiresIn=PRESIGN_SECONDS
    )


def http(method, url, body=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def cmd_r2check(env):
    s3 = s3_client(env)
    bucket = env["R2_BUCKET"]
    key = "probe/r2check.txt"
    payload = f"r2check {now()}".encode()
    s3.put_object(Bucket=bucket, Key=key, Body=payload)
    got = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    assert got == payload, "read back different bytes"
    # Presigned URLs must work from a plain HTTP client too.
    status, body = http("GET", presign(s3, bucket, "GET", key))
    assert status == 200 and body == payload, f"presigned GET failed: {status}"
    s3.delete_object(Bucket=bucket, Key=key)
    print(f"R2 OK: put/get/delete + presigned GET against bucket '{bucket}'")


def cmd_check(env):
    url = f"{env['WRAPPER_URL'].rstrip('/')}/result/{uuid.uuid4()}"
    status, body = http(
        "GET", url, headers={"Authorization": f"Bearer {env['AUTH_TOKEN']}"}, timeout=15
    )
    print(f"wrapper answered HTTP {status} on /result/<unknown-id>: {body[:200]!r}")
    if status in (401, 403):
        sys.exit("wrapper reachable but the auth token is rejected — check AUTH_TOKEN")
    print("wrapper reachable through the proxy — OK")


def cmd_prepare(env):
    OUT.mkdir(exist_ok=True)
    # Clear old state FIRST: a partway-failed prepare must leave nothing a
    # later submit could mistake for freshly prepared. A state already
    # marked submitted may belong to a job still running on the instance,
    # so discarding it demands an explicit yes.
    if STATE.exists():
        try:
            old = json.loads(STATE.read_text())
        except (ValueError, OSError):
            old = None  # unreadable — treat like a possibly-submitted job
        if not isinstance(old, dict):
            old = None  # valid JSON of the wrong shape gets the same gate
        if old is None:
            print("WARNING: out/state.json exists but is unreadable or not "
                  "in the expected format. It may belong to a job that was "
                  "already submitted and could still be running on the "
                  "instance — check the Jupyter terminal / Vast dashboard "
                  "before discarding it.")
        elif old.get("submitted_at"):
            print(f"WARNING: the previously prepared job (request_id "
                  f"{old.get('request_id', '<unknown>')}) was submitted at "
                  f"{old['submitted_at']} and may still be running on the "
                  "instance. Re-preparing discards its tracking here; its "
                  "output stays in R2 under the old keys but 'collect' will "
                  "no longer find it.")
        if old is None or old.get("submitted_at"):
            try:
                answer = input("Type 'yes' to discard it and prepare a "
                               "fresh job: ")
            except EOFError:
                answer = ""
            if answer.strip() != "yes":
                sys.exit("aborted — the old state was kept")
        STATE.unlink()
    s3 = s3_client(env)
    bucket = env["R2_BUCKET"]
    job_id = f"probe-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    file_id = uuid.uuid4().hex[:12]
    request_id = str(uuid.uuid4())

    input_key = f"in/{job_id}/{file_id}.mp4"
    video = (HERE / "input.mp4").read_bytes()
    t0 = time.monotonic()
    s3.put_object(Bucket=bucket, Key=input_key, Body=video, ContentType="video/mp4")
    print(f"uploaded input video {len(video)/1e6:.1f} MB → {input_key} "
          f"in {time.monotonic()-t0:.1f}s")

    script_key = "probe/worker_probe.py"
    s3.put_object(Bucket=bucket, Key=script_key,
                  Body=(HERE / "worker_probe.py").read_bytes())

    keys = {
        "output": f"out/{job_id}/{file_id}.mp4",
        "synthetic": f"out/{job_id}/synthetic-{SYNTHETIC_MB}mb.bin",
        "log": f"probe/{job_id}/probe-log.json",
    }
    cfg = {
        "request_id": request_id,
        "auth_token": env["AUTH_TOKEN"],
        "relay_url": env.get("RELAY_URL", "https://httpbin.org/post"),
        "synthetic_mb": SYNTHETIC_MB,
        "put_output_url": presign(s3, bucket, "PUT", keys["output"]),
        "put_synthetic_url": presign(s3, bucket, "PUT", keys["synthetic"]),
        "put_log_url": presign(s3, bucket, "PUT", keys["log"]),
    }
    cfg_key = f"probe/{job_id}/cfg.json"
    s3.put_object(Bucket=bucket, Key=cfg_key, Body=json.dumps(cfg).encode())

    state = {
        "job_id": job_id, "file_id": file_id, "request_id": request_id,
        "prepared_at": time.time(),
        "keys": keys,
        "input_get_url": presign(s3, bucket, "GET", input_key),
        "log_get_url": presign(s3, bucket, "GET", keys["log"]),
        "output_get_url": presign(s3, bucket, "GET", keys["output"]),
    }
    save_state(state)

    script_url = presign(s3, bucket, "GET", script_key)
    cfg_url = presign(s3, bucket, "GET", cfg_key)
    print(f"\njob_id={job_id}  request_id={request_id}")
    print("\nPaste this ONE line into a Jupyter terminal on the instance:\n")
    print(f"cd /tmp && curl -sfL '{script_url}' -o wp.py && "
          f"curl -sfL '{cfg_url}' -o wp.cfg.json && python3 wp.py wp.cfg.json")
    print("\nWait until it prints 'PROBE READY', then continue the wizard.")


def cmd_submit(env):
    if not STATE.exists():
        sys.exit("out/state.json missing — 'prepare' (stage 6) did not "
                 "complete; re-run it before submitting.")
    state = json.loads(STATE.read_text())
    if state.get("submitted_at"):
        sys.exit(f"this prepared job (request_id {state['request_id']}) was "
                 f"already submitted at {state['submitted_at']} — submitting "
                 "again would start a second GPU job under the same id. Run "
                 "'collect' for its results, or re-run stage 6 "
                 "(wizard.sh --from=6) to prepare a fresh job.")
    check_presigns_fresh(state)
    workflow = json.loads((HERE / "SeedVR2_HD_video_upscale_api.json").read_text())
    # The probe itself: a presigned R2 GET URL as the LoadVideo input. The
    # wrapper is documented to download URL-looking inputs into ComfyUI's
    # input/ dir (verified for images only — this run verifies video).
    workflow["21"]["inputs"]["file"] = state["input_get_url"]
    workflow["23"]["inputs"]["filename_prefix"] = f"probe/{state['job_id']}"

    payload = json.dumps(
        {"input": {"request_id": state["request_id"], "workflow_json": workflow}}
    ).encode()
    base = env["WRAPPER_URL"].rstrip("/")
    auth = {"Authorization": f"Bearer {env['AUTH_TOKEN']}",
            "Content-Type": "application/json"}
    status, body = http("POST", f"{base}/generate", body=payload, headers=auth,
                        timeout=60)
    print(f"[{now()}] POST /generate → HTTP {status}: {body[:300]!r}")
    if status >= 400:
        sys.exit("submit failed")
    # Stamp BEFORE polling: from this moment a real GPU job exists, and any
    # later submit against this state must refuse (see the guard above).
    state["submitted_at"] = now()
    save_state(state)

    timeline = [{"t": now(), "event": "submitted", "http": status}]
    last_msg = None
    deadline = time.monotonic() + 2 * 3600
    while True:
        if time.monotonic() > deadline:
            (OUT / "driver-result.json").write_text(json.dumps(
                {"timeline": timeline, "final": None, "timed_out_after": "2h"},
                indent=2))
            sys.exit("gave up waiting after 2h — the job may still be running "
                     "on the instance; check the Jupyter terminal, then run "
                     "'collect'. Do NOT re-run 'submit': it would start a "
                     "second GPU job under the same request id.")
        time.sleep(5)
        status, body = http("GET", f"{base}/result/{state['request_id']}",
                            headers=auth, timeout=30)
        try:
            res = json.loads(body)
        except ValueError:
            print(f"[{now()}] non-JSON /result answer HTTP {status}: {body[:200]!r}")
            continue
        st = res.get("status")
        msg = json.dumps(res.get("message", ""))[:160]
        if (st, msg) != last_msg:
            print(f"[{now()}] status={st} message={msg}")
            timeline.append({"t": now(), "status": st, "message": res.get("message")})
            last_msg = (st, msg)
        if st in ("completed", "failed"):
            (OUT / "driver-result.json").write_text(json.dumps(
                {"timeline": timeline, "final": res}, indent=2))
            print(f"final status: {st} — full payload in out/driver-result.json")
            break


def cmd_collect(env):
    if not STATE.exists():
        sys.exit("out/state.json missing — nothing has been prepared or "
                 "submitted, so there is nothing to collect.")
    state = json.loads(STATE.read_text())
    s3 = s3_client(env)
    bucket = env["R2_BUCKET"]
    # Unlike submit, collect never needs a re-prepare on expiry: this side
    # holds the credentials, so stale GET URLs are re-minted for the SAME
    # keys the finished job wrote to.
    if time.time() - state.get("prepared_at", 0) > PRESIGN_SECONDS - 3600:
        print("stored presigned URLs have expired — minting fresh ones for "
              "the same keys")
        state["log_get_url"] = presign(s3, bucket, "GET", state["keys"]["log"])
        state["output_get_url"] = presign(s3, bucket, "GET",
                                          state["keys"]["output"])

    status, body = http("GET", state["log_get_url"], timeout=60)
    if status != 200:
        sys.exit(f"probe log not in R2 yet (HTTP {status}) — did the on-instance "
                 "probe finish? Check the Jupyter terminal.")
    (OUT / "probe-log.json").write_text(body.decode())
    log = json.loads(body)

    for name in ("output", "synthetic"):
        try:
            head = s3.head_object(Bucket=bucket, Key=state["keys"][name])
            print(f"R2 has {state['keys'][name]}: {head['ContentLength']/1e6:.1f} MB")
        except Exception as e:  # noqa: BLE001 — probe: record, don't crash
            print(f"MISSING in R2: {state['keys'][name]} ({e})")

    t0 = time.monotonic()
    status, body = http("GET", state["output_get_url"], timeout=600)
    dl = time.monotonic() - t0
    verdict = "download failed"
    if status == 200:
        (OUT / f"output-{state['file_id']}.mp4").write_bytes(body)
        md5 = hashlib.md5(body).hexdigest()
        remote_md5 = log.get("output_md5")
        verdict = (f"{len(body)/1e6:.1f} MB in {dl:.1f}s "
                   f"({len(body)/1e6/dl:.1f} MB/s), md5 "
                   + ("MATCHES worker's" if md5 == remote_md5 else
                      f"MISMATCH (local {md5} vs worker {remote_md5})"))
    print(f"presigned GET of output: HTTP {status} — {verdict}")

    print("\n=== on-instance probe summary ===")
    for step in log.get("steps", []):
        print(f"  [{step.get('t','')}] {step.get('name')}: "
              f"{json.dumps({k: v for k, v in step.items() if k not in ('t', 'name')})[:400]}")
    print("\nfull log: out/probe-log.json — bring out/ back to the wayfinder "
          "session to resolve ticket #18")


if __name__ == "__main__":
    cmds = {"r2check": cmd_r2check, "check": cmd_check, "prepare": cmd_prepare,
            "submit": cmd_submit, "collect": cmd_collect}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(f"usage: driver.py [{'|'.join(cmds)}]")
    cmds[sys.argv[1]](load_env())
