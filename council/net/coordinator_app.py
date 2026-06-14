#!/usr/bin/env python3
"""
council/net/coordinator_app.py — the networked coordinator (FastAPI, hardened)
==============================================================================
The open-source, self-hostable hub. Holds the ledger, job queue, node registry, and
telemetry; routes tasks and settles credit. Provider-agnostic (all config via env).

AuthN/Z:
  • X-PW-Token  — shared operator token, required on every write endpoint.
  • X-Node-Secret — per-node secret (minted at register, shown once). Required on
    heartbeat/next/result; the node is identified FROM the secret, and a node may only
    complete its OWN tasks (no task hijacking, no score/ledger forgery).

Endpoints:
  POST /nodes/register     {name,country,owner,answer_model,lens,can_judge,judge_model,profile}
                                                       → {node_id, node_secret}   (secret shown once)
  POST /nodes/heartbeat    {load}                      [X-Node-Secret]
  GET  /tasks/next                                     [X-Node-Secret]  → task or 204
  POST /tasks/{task_id}/result   {…result…}            [X-Node-Secret]
  POST /jobs               {asker, question}           → {job_id, status}
  GET  /jobs/{job_id}                                  → full job view
  GET  /status                                         → telemetry (no node_id/IP leak)
  GET  /dashboard                                      → live operator map
  GET  /healthz

Run:  PW_TOKEN=… python -m council.net.coordinator_app
"""

from __future__ import annotations

import ipaddress
import os
import threading

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from council.net.app import APP_HTML
from council.net.baseline import generate_baseline
from council.net.config import CONFIG, JOB_TYPES, task_behavior
from council.net.dashboard import DASHBOARD_HTML
from council.net.ratelimit import RateLimiter
from council.net.store import Store


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _startup_guard() -> None:
    """Refuse to expose the coordinator publicly with a default/empty token."""
    if not _is_loopback(CONFIG.host) and CONFIG.token in ("", "dev-token"):
        raise RuntimeError(
            f"refusing to bind {CONFIG.host} with a weak PW_TOKEN. Set a strong PW_TOKEN, "
            "or bind 127.0.0.1 and front it with a tunnel/reverse proxy.")


_startup_guard()
if os.environ.get("PW_TRUST_XFF", "0").lower() in ("1", "true", "yes"):
    # D36 review: trusting X-Forwarded-For for rate-limit keys is only safe behind a reverse proxy
    # that SETS and STRIPS client-supplied XFF — otherwise clients rotate it to bypass the limit.
    print("[coordinator] PW_TRUST_XFF enabled — rate-limit keys trust X-Forwarded-For. Ensure your "
          "reverse proxy sets AND sanitizes that header (drops client-supplied values), or attackers "
          "can rotate it to defeat per-client limits.", flush=True)
app = FastAPI(title="Passive Workers — Council Coordinator")
store = Store()
_rl = RateLimiter()   # per-process sliding-window rate limiter (D36)


def _client_key(request: Request) -> str:
    """A per-client key for pre-auth endpoints. By DEFAULT keys on the socket peer (behind a tunnel
    that's usually loopback → effectively a conservative GLOBAL cap, which is spoof-proof). Only when
    PW_TRUST_XFF is set — i.e. the operator runs a trusted reverse proxy that sets AND sanitizes
    X-Forwarded-For — do we key on the first XFF hop for per-client granularity. Trusting XFF blindly
    would let an attacker rotate the header to mint unlimited keys and bypass the limit entirely."""
    if os.environ.get("PW_TRUST_XFF", "0").lower() in ("1", "true", "yes"):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "?")[:64]


def _ratelimit(key: str, env: str, default: str) -> None:
    """Raise 429 if `key` exceeds its per-60s limit. Limit is env-tunable; `<=0` disables it."""
    try:
        limit = int(os.environ.get(env, default))
    except (TypeError, ValueError):
        limit = int(default)
    if not _rl.allow(key, limit, 60.0):
        raise HTTPException(status_code=429, detail="rate limit exceeded — slow down")


def _enroll_enabled() -> bool:
    """D37: when on, the starter grant (and node registration) requires an enrollment token, so
    Sybil identities can't mint free credits. Off by default → current open behavior is unchanged."""
    return os.environ.get("PW_ENROLL", "0").lower() in ("1", "true", "yes")


def _auth(token: str | None) -> None:
    if token != CONFIG.token:
        raise HTTPException(status_code=401, detail="bad or missing X-PW-Token")


def _node_auth(secret: str | None) -> str:
    node_id = store.node_for_secret(secret or "")
    if not node_id:
        raise HTTPException(status_code=401, detail="bad or missing X-Node-Secret")
    return node_id


def _user_auth(secret: str | None) -> str:
    handle = store.user_for_secret(secret or "")
    if not handle:
        raise HTTPException(status_code=401, detail="sign in first (bad or missing X-User-Secret)")
    return handle


class RegisterBody(BaseModel):
    name: str = Field("node", max_length=80)
    country: str = Field("?", max_length=80)
    owner: str = Field(..., max_length=80)
    answer_model: str = Field("", max_length=80)
    lens: str = Field("neutral", max_length=80)
    can_judge: bool = False
    judge_model: str = Field("", max_length=80)
    machine_id: str = Field("?", max_length=120)
    profile: dict = {}


class HeartbeatBody(BaseModel):
    load: float = 0.0


class UserBody(BaseModel):
    handle: str = Field(..., max_length=40)


class JobBody(BaseModel):
    question: str = Field(..., max_length=4000)
    minds: int | None = Field(default=None, ge=1, le=16)   # responder dial (clamped to online)
    type: str | None = Field(default=None, max_length=32)  # job type (see GET /job-types)
    items: list[str] | None = Field(default=None, max_length=200)  # shard_map: the work items
    requires: dict | None = None    # capability gate, e.g. {"model": "qwen3:14b", "min_ram_gb": 16}
    fetch: bool = False             # shard_map: items are PUBLIC URLs to fetch+process (D15 rules)
    context: str = Field(default="", max_length=4000)   # assisted: bounded context for the operator
    encrypt_to: str = Field(default="", max_length=100)  # assisted: asker box pubkey for E2E file encryption (D23)
    split: list[float] | None = Field(default=None, max_length=16)  # shard_map: explicit per-worker weights (else capacity-weighted, D32)
    then: dict | None = None        # stage chaining (D35): {question, requires?} → an assisted follow-on at completion
    as_file: bool = False           # D38: deliver the assembled sharded output as one downloadable file


class FeedbackBody(BaseModel):
    verdict: str = Field(..., max_length=12)


class ProgressBody(BaseModel):
    done: int = Field(..., ge=0)
    total: int = Field(..., ge=1)


class EnrollBody(BaseModel):
    owner: str = Field(default="", max_length=80)
    kind: str = Field(default="any", pattern="^(any|node|user)$")     # reject typo'd kinds (review)
    # finite + non-negative: a non-finite/negative grant would corrupt ledger conservation (review)
    grant: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_uses: int = Field(default=1, ge=1, le=10000)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/nodes/register")
def register(body: RegisterBody, request: Request, x_pw_token: str | None = Header(default=None),
             x_enroll_token: str | None = Header(default=None)):
    _ratelimit(f"reg:{_client_key(request)}", "PW_RL_REGISTER", "60")   # D36 (per client/global-behind-tunnel)
    grant = None
    if _enroll_enabled():
        # D37: registration requires a per-operator enrollment token (the shared PW_TOKEN is now the
        # ADMIN token, used only to mint these) — so one leaked token can't enroll for everyone, and
        # the owner's starter grant comes from (and is bounded by) the redeemed token.
        red = store.redeem_enrollment(x_enroll_token or "", kind="node")
        if not red.get("ok"):
            raise HTTPException(status_code=403, detail=red.get("error", "valid enrollment token required"))
        grant = red.get("grant")
    else:
        _auth(x_pw_token)
    ip = request.client.host if request.client else ""
    return store.register_node(body.model_dump(), ip=ip, grant_amount=grant)   # {node_id, node_secret}


@app.post("/nodes/heartbeat")
def heartbeat(body: HeartbeatBody, x_pw_token: str | None = Header(default=None),
              x_node_secret: str | None = Header(default=None)):
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    store.heartbeat(node_id, body.load)
    return {"ok": True}


@app.get("/tasks/next")
def next_task(x_pw_token: str | None = Header(default=None),
              x_node_secret: str | None = Header(default=None)):
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    task = store.next_task(node_id)
    if task is None:
        return Response(status_code=204)
    return task


@app.post("/tasks/{task_id}/result")
def task_result(task_id: str, result: dict, x_pw_token: str | None = Header(default=None),
                x_node_secret: str | None = Header(default=None)):
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    accepted = store.complete_task(task_id, result, node_id=node_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="task not yours, unknown, or already done")
    return {"accepted": True}


@app.post("/tasks/{task_id}/progress")
def task_progress(task_id: str, body: ProgressBody, x_pw_token: str | None = Header(default=None),
                  x_node_secret: str | None = Header(default=None)):
    """A worker reports mid-flight progress on its claimed task (D32). Best-effort: a rejected
    report (not yours / already done / bad numbers) just isn't recorded — it never errors the run."""
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    _ratelimit(f"prog:{node_id}", "PW_RL_PROGRESS", "600")   # generous; legit progress is throttled client-side (D36)
    store.update_task_progress(node_id, task_id, body.done, body.total)
    return {"ok": True}


@app.get("/tasks/offers")
def assisted_offers(x_pw_token: str | None = Header(default=None),
                    x_node_secret: str | None = Header(default=None)):
    """Open assisted offers this operator may consent to (D21). Returns brief + bounded context."""
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    node = store.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="unknown node")
    return {"offers": store.assisted_offers(dict(node))}


@app.post("/tasks/{task_id}/accept")
def accept_assisted(task_id: str, x_pw_token: str | None = Header(default=None),
                    x_node_secret: str | None = Header(default=None)):
    """Operator gives informed consent to + claims an assisted offer (D21)."""
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    node = store.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="unknown node")
    out = store.accept_assisted(task_id, node_id, node["owner"])
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail=out.get("error", "cannot accept"))
    return out


class DeliverBody(BaseModel):
    deliverable: str = Field(..., max_length=200_000)
    signature: str = Field(default="", max_length=200)    # operator's Ed25519 sig (D23)
    signer_pub: str = Field(default="", max_length=100)    # operator's verify key (b64)


@app.post("/tasks/{task_id}/deliver")
def deliver_assisted(task_id: str, body: DeliverBody,
                     x_pw_token: str | None = Header(default=None),
                     x_node_secret: str | None = Header(default=None)):
    """Operator returns the owned deliverable; the ledger settles (D21)."""
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    out = store.deliver_assisted(task_id, node_id, body.deliverable,
                                 signature=body.signature, signer_pub=body.signer_pub)
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail=out.get("error", "cannot deliver"))
    return out


_BLOB_CAP = 512 * 1024   # max bytes for one content-addressed chunk


@app.post("/jobs/{job_id}/blobs/{blob_hash}")
async def put_blob(job_id: str, blob_hash: str, request: Request,
                   x_pw_token: str | None = Header(default=None),
                   x_node_secret: str | None = Header(default=None)):
    """Operator uploads a content-addressed chunk for a job it has claimed (D22)."""
    _auth(x_pw_token)
    node_id = _node_auth(x_node_secret)
    if store.assisted_claimant(job_id) != node_id:
        raise HTTPException(status_code=403, detail="not the claiming operator for this job")
    # Stream the body with a HARD cap and abort early, so a chunked / no-Content-Length upload can't
    # buffer unbounded memory before the size check (the old Content-Length guard was advisory —
    # a client can omit/lie about it; review D32 finding). Check BEFORE extending so the accumulator
    # never grows past the cap (review D34) — peak ≈ _BLOB_CAP + one server-sized stream chunk.
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > _BLOB_CAP:
            raise HTTPException(status_code=413, detail="chunk too large")
        buf.extend(chunk)
    out = store.put_blob(job_id, blob_hash, bytes(buf))
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error", "rejected"))
    return out


class RateBody(BaseModel):
    score: float = Field(..., ge=0, le=10)


@app.post("/jobs/{job_id}/rate")
def rate_assisted(job_id: str, body: RateBody, x_user_secret: str | None = Header(default=None)):
    """The asker rates a completed assisted deliverable (0-10) → operator reputation (D24)."""
    handle = _user_auth(x_user_secret)
    out = store.rate_assisted(job_id, handle, body.score)
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail=out.get("error", "cannot rate"))
    return out


@app.get("/jobs/{job_id}/blob/{blob_hash}")
def get_blob(job_id: str, blob_hash: str, x_user_secret: str | None = Header(default=None)):
    """The job's asker downloads a chunk to reassemble the deliverable (D22)."""
    handle = _user_auth(x_user_secret)
    if store.job_asker(job_id) != handle:
        raise HTTPException(status_code=403, detail="not your job")
    data = store.get_blob(job_id, blob_hash)
    if data is None:
        raise HTTPException(status_code=404, detail="no such blob")
    return Response(content=data, media_type="application/octet-stream")


@app.post("/users")
def make_user(body: UserBody, request: Request, x_enroll_token: str | None = Header(default=None)):
    # unauthenticated mint → the prime flood/ledger-inflation target; bound it per client (D36)
    _ratelimit(f"usr:{_client_key(request)}", "PW_RL_USERS", "20")
    grant = None
    if _enroll_enabled():
        # D37: signup stays open, but the STARTER GRANT requires a valid enrollment token — without
        # one the account is created with ZERO credit (it can still earn), so minting many handles
        # yields no free credits. This closes the Sybil starter-grant hole the D36 review surfaced.
        red = store.redeem_enrollment(x_enroll_token or "", kind="user")
        grant = red.get("grant") if red.get("ok") else 0.0
    res = store.register_user(body.handle, grant_amount=grant)
    if res.get("error"):
        raise HTTPException(status_code=409, detail=res["error"])
    return res


@app.post("/admin/enroll")
def admin_enroll(body: EnrollBody, x_pw_token: str | None = Header(default=None)):
    """Admin mints a per-operator enrollment token (D37). The shared PW_TOKEN is the ADMIN token.
    Returns the plaintext token ONCE — hand it to the operator to register / claim their grant."""
    _auth(x_pw_token)
    return store.mint_enrollment(owner=body.owner, kind=body.kind,
                                 grant=body.grant, max_uses=body.max_uses)


@app.get("/me")
def me(x_user_secret: str | None = Header(default=None)):
    return store.user_balance(_user_auth(x_user_secret))


def _baseline_async(job_id: str, question: str) -> None:
    """Generate the independent single-model baseline.

    API baseline → immediately (no local resources used). Local Ollama baseline → AFTER
    the council job settles: on a CPU-only host the council's own inference and a 14B
    baseline would fight for the same cores and both time out (measured: 99s idle vs
    >300s contended). The app keeps polling a few minutes past 'done' to pick it up.
    """
    if not CONFIG.baseline_api_key:
        import time
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            st = store.job_status(job_id)
            if st not in ("pending_answers", "judging"):
                break
            time.sleep(5)
    data = generate_baseline(question)
    if not data:
        print(f"[baseline] none stored for job {job_id[:8]} (generation returned nothing)", flush=True)
        return
    try:
        store.set_baseline(job_id, data)
        print(f"[baseline] stored for job {job_id[:8]}: {data['model']} in {data['elapsed_s']}s", flush=True)
    except Exception as e:  # baseline is best-effort; never disturb the job — but say so
        print(f"[baseline] store failed for job {job_id[:8]}: {type(e).__name__}: {e}", flush=True)


@app.get("/job-types")
def job_types():
    """The marketplace catalog — what kinds of work computers can request here."""
    per_mind = CONFIG.worker_pool / CONFIG.fleet_size
    return {k: {"label": v["label"], "eta": v["eta"],
                "price_per_mind": round(per_mind * v["pool_mult"], 1),
                "judge_fee": CONFIG.judge_fee,
                "deadline_s": v["deadline_s"]}
            for k, v in JOB_TYPES.items()}


@app.post("/jobs")
def submit_job(body: JobBody, x_user_secret: str | None = Header(default=None)):
    handle = _user_auth(x_user_secret)
    _ratelimit(f"job:{handle}", "PW_RL_JOBS", "120")   # per-asker job-creation cap (D36)
    out = store.create_job(handle, body.question, minds=body.minds,
                           job_type=body.type or "chat", items=body.items,
                           requires=body.requires, fetch=body.fetch, context=body.context,
                           encrypt_to=body.encrypt_to, split=body.split, then=body.then,
                           as_file=body.as_file)
    out["balance"] = store.user_balance(handle)
    if out.get("status") == "pending_answers" and not task_behavior(body.type).sharded:
        # the honest single-model compare only makes sense for answer/report jobs —
        # a one-shot model can't process a sharded item batch (shard_map/download_extract/code_generation)
        threading.Thread(target=_baseline_async, args=(out["job_id"], body.question),
                         daemon=True, name="pw-baseline").start()
    return out


@app.get("/jobs/mine")
def my_jobs(x_user_secret: str | None = Header(default=None)):
    handle = _user_auth(x_user_secret)
    return store.jobs_for_asker(handle)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    view = store.job_view(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return view


@app.post("/jobs/{job_id}/feedback")
def feedback(job_id: str, body: FeedbackBody, x_user_secret: str | None = Header(default=None)):
    who = _user_auth(x_user_secret)
    if not store.record_feedback(job_id, body.verdict, who):
        raise HTTPException(status_code=400,
                            detail="bad verdict, unknown job, or not the job's asker")
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return store.metrics()


@app.get("/status")
def status():
    return store.status()


@app.get("/", response_class=HTMLResponse)
def home():
    return APP_HTML


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port)
