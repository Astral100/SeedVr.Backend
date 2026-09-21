#!/usr/bin/env python3
"""On-instance probe for the worker-side storage flow (wayfinder ticket #18).

Runs INSIDE the Vast.ai container, launched by hand from a Jupyter terminal:

    python3 wp.py wp.cfg.json

It stands in for the future bundled worker service and records, as a second
process alongside a wrapper-driven run:

  1. which local wrapper/ComfyUI endpoints answer (and with what auth)
  2. whether ComfyUI's WebSocket is watchable during a wrapper-driven run,
     and whether percent progress arrives on it
  3. outbound POST relay of progress updates (status + latency per POST)
  4. that /result's local_path is readable by this process and the file is
     complete when status turns completed (size-stable check + md5)
  5. presigned PUT of the real output and of a synthetic ~300 MB file to R2,
     with throughput timing — no storage credentials on this box (ADR 0005)

Everything lands in a JSON log that is PUT to R2 via a presigned URL at the
end (and printed to stdout as a fallback). Pure stdlib except an optional
best-effort `pip install websocket-client` for the WS watch.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CFG = json.load(open(sys.argv[1]))
LOG = {"started": None, "steps": [], "ws_events": [], "relay_posts": []}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(name, **kw):
    entry = {"t": now(), "name": name, **kw}
    LOG["steps"].append(entry)
    print(f"[{entry['t']}] {name}: {json.dumps(kw)[:300]}", flush=True)


def http(method, url, body=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── 1. which local endpoints answer ────────────────────────────────────────
def find_wrapper():
    auth = {"Authorization": f"Bearer {CFG['auth_token']}"}
    candidates = [
        ("http://127.0.0.1:8288", auth),
        ("http://127.0.0.1:8288", {}),
        ("http://127.0.0.1:18288", {}),
    ]
    for base, headers in candidates:
        try:
            status, body = http("GET", f"{base}/result/probe-nonexistent",
                                headers=headers, timeout=5)
            log("wrapper_endpoint", base=base, auth=bool(headers), http=status,
                body=body[:120].decode(errors="replace"))
            if status not in (401, 403):
                return base, headers
        except Exception as e:  # noqa: BLE001 — probe: record every failure
            log("wrapper_endpoint", base=base, auth=bool(headers), error=str(e))
    log("wrapper_endpoint", fatal="no local wrapper endpoint answered")
    sys.exit(1)


# ── 2+3. WebSocket watch + outbound relay ──────────────────────────────────
def relay(pct, source):
    body = json.dumps({"request_id": CFG["request_id"], "pct": pct,
                       "source": source, "t": now()}).encode()
    t0 = time.monotonic()
    try:
        status, _ = http("POST", CFG["relay_url"], body=body,
                         headers={"Content-Type": "application/json"}, timeout=10)
        LOG["relay_posts"].append({"t": now(), "pct": pct, "source": source,
                                   "http": status,
                                   "ms": round((time.monotonic() - t0) * 1000)})
    except Exception as e:  # noqa: BLE001
        LOG["relay_posts"].append({"t": now(), "pct": pct, "source": source,
                                   "error": str(e)})


def ws_watch(stop):
    try:
        import websocket  # noqa: F401
    except ImportError:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--user",
                            "websocket-client"], capture_output=True, text=True)
        log("ws_pip_install", rc=r.returncode, err=r.stderr[-200:])
        try:
            import websocket  # noqa: F401
        except ImportError:
            log("ws_watch", fatal="websocket-client unavailable — WS check skipped")
            return
    import websocket

    auth = [("Authorization", f"Bearer {CFG['auth_token']}")]
    candidates = [
        ("ws://127.0.0.1:8188/ws?clientId=probe-watcher", auth),
        ("ws://127.0.0.1:8188/ws?clientId=probe-watcher", []),
        ("ws://127.0.0.1:18188/ws?clientId=probe-watcher", []),
    ]
    ws = None
    for url, headers in candidates:
        try:
            ws = websocket.create_connection(url, header=dict(headers), timeout=5)
            log("ws_connected", url=url, auth=bool(headers))
            break
        except Exception as e:  # noqa: BLE001
            log("ws_connect_attempt", url=url, auth=bool(headers), error=str(e))
            ws = None
    if ws is None:
        log("ws_watch", fatal="could not connect to ComfyUI WebSocket")
        return

    ws.settimeout(2)
    types_seen, last_pct = {}, -1
    while not stop.is_set():
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as e:  # noqa: BLE001
            log("ws_recv_error", error=str(e))
            break
        if not isinstance(frame, str):
            types_seen["<binary>"] = types_seen.get("<binary>", 0) + 1
            continue
        try:
            msg = json.loads(frame)
        except ValueError:
            continue
        mtype = msg.get("type", "?")
        types_seen[mtype] = types_seen.get(mtype, 0) + 1
        if len(LOG["ws_events"]) < 400:
            LOG["ws_events"].append({"t": now(), "type": mtype,
                                     "data": json.dumps(msg.get("data"))[:200]})
        if mtype == "progress":
            d = msg.get("data", {})
            if d.get("max"):
                pct = int(100 * d.get("value", 0) / d["max"])
                if pct != last_pct:
                    last_pct = pct
                    relay(pct, "ws")
    ws.close()
    log("ws_summary", event_types=types_seen, progress_relayed=last_pct >= 0)


# ── 4. wait for completion, then read local_path as a second process ───────
def find_local_paths(obj, found):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "local_path" and isinstance(v, str):
                found.append(v)
            else:
                find_local_paths(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_local_paths(v, found)


def wait_for_completion(base, headers):
    log("waiting", request_id=CFG["request_id"])
    print("PROBE READY", flush=True)
    last, last_pct = None, -1
    while True:
        time.sleep(3)
        try:
            status, body = http("GET", f"{base}/result/{CFG['request_id']}",
                                headers=headers, timeout=15)
            res = json.loads(body)
        except Exception as e:  # noqa: BLE001
            log("result_poll_error", error=str(e))
            continue
        st = res.get("status")
        if st != last:
            log("result_status", status=st, http=status)
            last = st
        msg = str(res.get("message", ""))
        if "Progress:" in msg:
            try:
                pct = int(float(msg.split("Progress:")[1].split("%")[0]))
                if pct != last_pct:
                    last_pct = pct
                    relay(pct, "result_poll")
            except (ValueError, IndexError):
                pass
        if st in ("completed", "failed"):
            return st, res


def check_file_complete(path):
    t0 = time.monotonic()
    readable = os.access(path, os.R_OK)
    sizes = [os.path.getsize(path)]
    for _ in range(4):
        time.sleep(0.5)
        sizes.append(os.path.getsize(path))
    stable = len(set(sizes)) == 1
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            md5.update(chunk)
    log("local_path_check", path=path, readable=readable, size=sizes[-1],
        size_stable_over_2s=stable, md5=md5.hexdigest(),
        check_seconds=round(time.monotonic() - t0, 1))
    return md5.hexdigest(), sizes[-1]


# ── 5. presigned PUT uploads with throughput timing ────────────────────────
def put_stream(url, reader, length, label):
    t0 = time.monotonic()
    req = urllib.request.Request(url, data=reader, method="PUT",
                                 headers={"Content-Length": str(length),
                                          "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            status, etag = r.status, r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        status, etag = e.code, None
    dt = time.monotonic() - t0
    log("presigned_put", label=label, http=status, etag=etag,
        mb=round(length / 1e6, 1), seconds=round(dt, 1),
        mb_per_s=round(length / 1e6 / dt, 2))
    return status


class RepeatReader(io.RawIOBase):
    """Streams `total` bytes of a repeated random 1 MiB block (synthetic file)."""

    def __init__(self, total):
        self.block, self.total, self.sent = os.urandom(1 << 20), total, 0

    def readable(self):
        return True

    def readinto(self, b):
        if self.sent >= self.total:
            return 0
        n = min(len(b), self.total - self.sent, len(self.block))
        b[:n] = self.block[:n]
        self.sent += n
        return n


# ── instance metadata (region evidence for the throughput numbers) ─────────
def instance_metadata():
    meta = {k: v for k, v in os.environ.items()
            if any(s in k for s in ("VAST", "PUBLIC_IP", "GPU", "CONTAINER_ID"))}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name",
                            "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=10)
        meta["gpu"] = r.stdout.strip()
    except Exception as e:  # noqa: BLE001
        meta["gpu_error"] = str(e)
    try:
        _, body = http("GET", "https://ipinfo.io/json", timeout=10)
        geo = json.loads(body)
        meta["geo"] = {k: geo.get(k) for k in ("city", "region", "country", "org")}
    except Exception as e:  # noqa: BLE001
        meta["geo_error"] = str(e)
    log("instance_metadata", **meta)


def main():
    LOG["started"] = now()
    instance_metadata()
    base, headers = find_wrapper()

    stop = threading.Event()
    watcher = threading.Thread(target=ws_watch, args=(stop,), daemon=True)
    watcher.start()

    final_status, result = wait_for_completion(base, headers)
    LOG["final_result"] = result
    time.sleep(3)  # let trailing WS frames land
    stop.set()
    watcher.join(timeout=10)

    if final_status == "completed":
        try:
            paths = []
            find_local_paths(result, paths)
            log("local_paths_in_result", paths=paths)
            if paths:
                md5, size = check_file_complete(paths[0])
                LOG["output_md5"] = md5
                with open(paths[0], "rb") as f:
                    put_stream(CFG["put_output_url"], f, size, "real-output")
            else:
                log("local_path_check", fatal="no local_path in the /result payload")
        except Exception as e:  # noqa: BLE001 — keep going: synthetic PUT + log ship
            log("local_path_check", fatal=str(e))

    total = CFG["synthetic_mb"] * 1_000_000
    put_stream(CFG["put_synthetic_url"], RepeatReader(total), total,
               f"synthetic-{CFG['synthetic_mb']}mb")

    body = json.dumps(LOG, indent=1).encode()
    status, _ = http("PUT", CFG["put_log_url"], body=body,
                     headers={"Content-Type": "application/json"}, timeout=120)
    print(f"probe log PUT to R2 → HTTP {status}", flush=True)
    if status != 200:
        print(body.decode(), flush=True)  # stdout fallback
    print("PROBE DONE", flush=True)


if __name__ == "__main__":
    main()
