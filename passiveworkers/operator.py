#!/usr/bin/env python3
"""
passiveworkers/operator.py — the operator side of assisted tasks (D21)
================================================================
"Computers doing work for other computers", with a human in the loop. An operator who
has joined a coordinator can see OPEN assisted offers, give informed consent to one,
do the work themselves (with their own agentic AI — Claude, Codex — or by hand), and
deliver the owned result. Our software NEVER automates the operator's computer; the
human is always the agent and always consents to a bounded brief.

    pworkers tasks                       list open assisted offers you're eligible for
    pworkers accept <task_id>            consent to + claim an offer (prints the full brief)
    pworkers deliver <task_id> <text>    deliver your result (text, or @path to a file)
    pworkers fingerprint                 print your signing pubkey + fingerprint to share (operator)
    pworkers trust add <op> <key>        pin an operator's signing key out of band (asker)
    pworkers trust list | remove <op>    show / drop pinned operators (asker)
    pworkers fetch <job> <dir>           download + verify a delivered file (asker)
    pworkers keygen | rate <job> <0-10>  encryption key / rate a deliverable (asker)

Requires (operator env): PW_COORDINATOR, PW_TOKEN, PW_OWNER (your handle). Optional:
PW_NAME, PW_COUNTRY. Identity (node_id + secret) is cached in ~/.passiveworkers/operator.json
per coordinator so accept→deliver use the same node.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import socket
import sys

import requests

STATE = pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                    str(pathlib.Path.home() / ".passiveworkers"))) / "operator.json"


def _profile() -> dict:
    prof = {"os": platform.system(), "machine": platform.machine()}
    try:
        import psutil
        prof["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
        prof["cores"] = psutil.cpu_count(logical=False) or psutil.cpu_count()
    except Exception:
        pass
    try:
        base = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")
        r = requests.get(f"{base}/api/tags", timeout=5)
        prof["models"] = sorted(m["name"] for m in r.json().get("models", []))[:40]
    except Exception:
        prof["models"] = []
    return prof


class Operator:
    def __init__(self):
        self.base = os.environ.get("PW_COORDINATOR", "").rstrip("/")
        self.token = os.environ.get("PW_TOKEN", "dev-token")
        if not self.base:
            raise SystemExit("set PW_COORDINATOR (e.g. http://127.0.0.1:8791)")
        self.owner = os.environ.get("PW_OWNER", socket.gethostname())
        self.node_id, self.secret = self._identity()

    def _headers(self) -> dict:
        h = {"X-PW-Token": self.token, "Content-Type": "application/json"}
        if self.secret:
            h["X-Node-Secret"] = self.secret
        return h

    def _sign_pub(self) -> str:
        """The operator's persisted Ed25519 verify key (b64), or '' without the crypto extra."""
        try:
            from passiveworkers import crypto as C
            if not C.available():
                return ""
            kp = C.load_or_create(STATE.parent / "operator_keys.json", "sign")
            self._sign_priv = kp["priv"]
            return kp["pub"]
        except Exception:
            return ""

    def _sign(self, data: bytes) -> tuple[str, str]:
        """(signature_b64, signer_pub_b64) over data, or ('','') without the crypto extra."""
        pub = self._sign_pub()
        if not pub:
            return "", ""
        try:
            from passiveworkers import crypto as C
            return C.sign(self._sign_priv, data), pub
        except Exception:
            return "", ""

    def _identity(self) -> tuple[str, str]:
        """Reuse a cached node identity for this coordinator, else register one.

        Prefers this machine's `pworkers join` identity for the same coordinator (join.json) —
        mirrors the fallback pattern `_cached_node_secret()` already uses below — so a machine
        that has both `pworkers join`ed and runs `pworkers tasks`/`pworkers accept`/`pworkers deliver` shares ONE node
        identity instead of showing up twice on the coordinator's live map (store.online_nodes()
        keys nodes by node_id, not owner). Falls through to the operator.json cache-then-register
        path unchanged for machines that have never `pworkers join`ed (backward compatible)."""
        from passiveworkers.net.agent import _load_join
        from passiveworkers import paths
        joined = paths.coordinator_entries(_load_join())
        entry = joined.get(self.base)
        if entry and entry.get("node_id") and entry.get("node_secret"):
            return entry["node_id"], entry["node_secret"]
        if STATE.exists():
            try:
                cache = json.loads(STATE.read_text()).get(self.base)
                if cache:
                    return cache["node_id"], cache["secret"]
            except Exception:
                pass
        prof = _profile()
        prof["sign_pub"] = self._sign_pub()   # publish the operator's verify key (D23)
        body = {"name": os.environ.get("PW_NAME", socket.gethostname()),
                "country": os.environ.get("PW_COUNTRY", "local"), "owner": self.owner,
                "answer_model": "", "lens": "operator", "can_judge": False,
                "judge_model": "", "machine_id": os.environ.get("PW_MACHINE_ID", socket.gethostname()),
                "profile": prof}
        r = requests.post(f"{self.base}/nodes/register", json=body,
                          headers={"X-PW-Token": self.token}, timeout=15)
        r.raise_for_status()
        d = r.json()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        all_state = {}
        if STATE.exists():
            try:
                all_state = json.loads(STATE.read_text())
            except Exception:
                all_state = {}
        all_state[self.base] = {"node_id": d["node_id"], "secret": d["node_secret"]}
        from passiveworkers import paths
        paths.write_private_json(STATE, all_state)   # node_secret is a bearer credential —
                                                       # owner-only with no world-readable window
                                                       # (review finding: write-then-chmod left one)
        return d["node_id"], d["node_secret"]

    def tasks(self) -> int:
        offers = requests.get(f"{self.base}/tasks/offers", headers=self._headers(),
                              timeout=15).json().get("offers", [])
        if not offers:
            print("No open assisted offers you're eligible for right now.")
            return 0
        for o in offers:
            print(f"\n● {o['task_id']}   (reward {o['price']} cr · open {o['age_s']:.0f}s)")
            print(f"  brief: {o['brief']}")
            if o.get("requires"):
                print(f"  needs: {o['requires']}")
        print("\nAccept one with:  pworkers accept <task_id>")
        return 0

    def accept(self, task_id: str) -> int:
        r = requests.post(f"{self.base}/tasks/{task_id}/accept", headers=self._headers(), timeout=15)
        if not r.ok:
            print(f"✗ {r.json().get('detail', r.text)}"); return 1
        d = r.json()
        print(f"✓ accepted {task_id}\n\nBRIEF:\n{d['brief']}\n")
        if d.get("context"):
            print(f"CONTEXT:\n{d['context']}\n")
        print("Do the work (your own AI or by hand), then:\n"
              f"  pworkers deliver {task_id} \"<your result>\"   (or @path/to/file)")
        return 0

    def _send_deliver(self, task_id: str, deliverable: str) -> "requests.Response":
        payload = deliverable[:200_000]                   # the exact bytes that get stored/verified
        sig, signer = self._sign(payload.encode())        # sign EXACTLY what is delivered (D23)
        return requests.post(f"{self.base}/tasks/{task_id}/deliver", headers=self._headers(),
                             data=json.dumps({"deliverable": payload,
                                              "signature": sig, "signer_pub": signer}), timeout=30)

    def deliver(self, task_id: str, deliverable: str, job_id: str = "") -> int:
        # @path delivers a real FILE: chunk → upload content-addressed blobs → deliver a signed
        # manifest the asker fetches+verifies+reassembles (D22). Plain text stays inline. If the
        # asker published an encryption key (encrypt_to), each chunk is sealed to it (D23).
        if deliverable.startswith("@"):
            from passiveworkers import artifacts as A
            path = deliverable[1:]
            if not job_id:
                print("✗ file delivery needs the job id: pworkers deliver <task_id> @file <job_id>")
                return 2
            view = requests.get(f"{self.base}/jobs/{job_id}", timeout=15).json()
            encrypt_to = view.get("encrypt_to") or ""
            if encrypt_to:
                from passiveworkers import crypto as C
                if not C.available():
                    print("✗ this job requires encryption — install the crypto extra: pip install 'passiveworkers[crypto]'")
                    return 2
                manifest, blobs = A.chunk_file_encrypted(path, lambda b: C.seal(encrypt_to, b))
                tag = " (encrypted)"
            else:
                manifest, blobs = A.chunk_file(path)
                tag = ""
            bh = {"X-PW-Token": self.token, "Content-Type": "application/octet-stream"}
            if self.secret:
                bh["X-Node-Secret"] = self.secret
            for h, buf in blobs.items():
                rb = requests.post(f"{self.base}/jobs/{job_id}/blobs/{h}", headers=bh,
                                   data=buf, timeout=60)
                if not rb.ok:
                    print(f"✗ upload failed: {rb.json().get('detail', rb.text)}"); return 1
            r = self._send_deliver(task_id, A.wrap_artifact(manifest))
            if not r.ok:
                print(f"✗ {r.json().get('detail', r.text)}"); return 1
            print(f"✓ delivered file '{manifest['name']}' ({len(blobs)} chunks){tag} — you've been paid.")
            return 0
        r = self._send_deliver(task_id, deliverable)
        if not r.ok:
            print(f"✗ {r.json().get('detail', r.text)}"); return 1
        print(f"✓ delivered — you've been paid. job {r.json().get('job_id', '')[:8]}")
        return 0


def _verify_delivery_signature(view: dict, merged: str) -> tuple[bool, int]:
    """Decide whether a delivery's signature clears out-of-band trust (D25). Pure except for the
    TOFU pin side effect (which it surfaces, never swallows). Returns (ok, exit_code): ok=False
    means the caller must abort with exit_code.

    The guarantee for a PINNED operator holds even against a fully hostile coordinator:
      • it can't simply omit the signature (a pinned operator MUST sign → refused),
      • it can't blank the operator handle to dodge the pin (signed-but-unidentified → refused),
      • it can't present a different key (classify → MISMATCH → refused),
      • it can't forge a signature under the pinned key (crypto.verify fails → refused),
      • it can't make us accept unverified by hiding the crypto extra (→ refused, not downgraded).
    """
    from passiveworkers import trust
    sig, signer = view.get("signature"), view.get("signer_pub")
    operator = (view.get("operator") or "").strip()
    # A signed delivery must name its operator, or there's no out-of-band anchor to trust.
    if sig and signer and not operator:
        print("✗ signed delivery has no operator handle to anchor trust — refusing")
        return False, 1
    pinned = trust.get(operator) if operator else None
    # A pinned operator always signs; an unsigned delivery from them is a downgrade attack.
    if pinned and not (sig and signer):
        print(f"✗ operator '{operator}' is pinned but this delivery is unsigned — refusing")
        return False, 1
    if not (sig and signer):
        print("⚠ delivery is unsigned and the operator is unpinned — integrity is NOT "
              "cryptographically verified (ask the operator to deliver with the crypto extra, "
              "then pin them with `pworkers trust add`)")
        return True, 0
    status, existing = trust.classify(operator, signer)
    if status == trust.MISMATCH:
        print(f"✗ operator '{operator}' presented key {trust.fingerprint(signer)} but the pinned "
              f"key is {existing['fp']} — refusing (key rotation, another machine, or tampering).\n"
              f"  If you trust the new key, verify it out of band then: pworkers trust add {operator} {signer}")
        return False, 1
    from passiveworkers import crypto as C
    if not C.available():
        print("✗ signature present but the crypto extra isn't installed — cannot verify, refusing "
              "to accept unverified (pip install 'passiveworkers[crypto]')")
        return False, 2
    verify_key = existing["pub"] if existing else signer
    if not C.verify(verify_key, merged.encode(), sig):
        print("✗ signature INVALID — deliverable may be tampered or signed by another key")
        return False, 1
    if status == trust.PINNED_MATCH:
        print(f"signature: ✓ valid · operator '{operator}' matches pinned key {existing['fp']}")
    else:                                     # UNPINNED → TOFU-pin a VALID key (never a bad one)
        try:
            trust.pin(operator, signer, source="tofu")
            print(f"signature: ✓ valid · first contact — pinned operator '{operator}' as "
                  f"{trust.fingerprint(signer)} (confirm out of band: ask them for `pworkers fingerprint`)")
        except Exception as e:               # surface a persistence failure, don't hide it
            print(f"signature: ✓ valid, but could NOT save the trust pin ({e}) — future deliveries "
                  f"from '{operator}' won't be auto-verified until you run: pworkers trust add {operator} {signer}")
    return True, 0


def _resolve_coordinator_base() -> str:
    base = os.environ.get("PW_COORDINATOR", "").rstrip("/")
    if base:
        return base
    from passiveworkers.net.submit import _load_asker_state
    return (_load_asker_state().get("default") or "").rstrip("/")


def _resolve_asker_secret(base: str) -> str:
    sec = os.environ.get("PW_USER_SECRET", "")
    if sec:
        return sec
    from passiveworkers.net.submit import _persisted_secret
    cached = _persisted_secret(base) if base else None
    return cached[1] if cached else ""


def fetch(job_id: str, out_dir: str) -> int:
    """Asker side: download a delivered FILE artifact, verify every chunk, reassemble.
    Needs PW_COORDINATOR + PW_USER_SECRET (the asker's secret) — or falls back to the
    identity `pworkers ask` persisted (R23 review)."""
    from passiveworkers import artifacts as A
    base = _resolve_coordinator_base()
    sec = _resolve_asker_secret(base)
    if not base or not sec:
        print(f"✗ no asker identity found — run `pworkers ask \"...\"` first (or sign up at "
              f"{base or '<coordinator>'}/ and export PW_USER_SECRET)")
        return 2
    uh = {"X-User-Secret": sec}
    view = requests.get(f"{base}/jobs/{job_id}", headers=uh, timeout=15).json()
    merged = view.get("merged") or ""
    ok, code = _verify_delivery_signature(view, merged)
    if not ok:
        return code
    # downgrade guard: if WE required encryption (encrypt_to set), refuse a plaintext deliverable
    if view.get("encrypt_to"):
        m = A.read_artifact(merged)
        if not (m and m.get("encrypted")):
            print("✗ this job required encryption but the deliverable is not encrypted — rejecting")
            return 1
    manifest = A.read_artifact(merged)
    if not manifest:
        print("This job's deliverable is text, not a file:\n")
        print(merged[:2000]); return 0
    decrypt = None
    if manifest.get("encrypted"):
        from passiveworkers import crypto as C
        if not C.available():
            print("✗ encrypted deliverable — install: pip install 'passiveworkers[crypto]'"); return 2
        kp = C.load_or_create(_asker_keys_path(), "box")
        decrypt = lambda ct: C.unseal(kp["priv"], ct)
    def _chunk(h):
        r = requests.get(f"{base}/jobs/{job_id}/blob/{h}", headers=uh, timeout=60)
        return r.content if r.ok else None
    dest = A.reassemble(manifest, _chunk, out_dir, decrypt=decrypt)
    print(f"✓ verified + reassembled → {dest}")
    return 0


def _asker_keys_path():
    return pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                       str(pathlib.Path.home() / ".passiveworkers"))) / "asker_keys.json"


def rate(job_id: str, score: str) -> int:
    """Asker side: rate a completed assisted job (0-10) → the operator's reputation.
    Needs PW_COORDINATOR + PW_USER_SECRET (the asker's secret) — or falls back to the
    identity `pworkers ask` persisted (R23 review)."""
    base = _resolve_coordinator_base()
    sec = _resolve_asker_secret(base)
    if not base or not sec:
        print(f"✗ no asker identity found — run `pworkers ask \"...\"` first (or sign up at "
              f"{base or '<coordinator>'}/ and export PW_USER_SECRET)")
        return 2
    try:
        s = float(score)
    except ValueError:
        print("✗ score must be a number 0-10"); return 2
    # `json=` sets Content-Type: application/json + serializes — a bare data=json.dumps(...) with no
    # Content-Type made FastAPI reject the body as a string (RateBody wouldn't parse): `pworkers rate` 422'd.
    r = requests.post(f"{base}/jobs/{job_id}/rate", headers={"X-User-Secret": sec},
                      json={"score": s}, timeout=15)
    if not r.ok:
        print(f"✗ {r.json().get('detail', r.text)}"); return 1
    print(f"✓ rated — operator reputation now {r.json().get('operator_reputation')}")
    return 0


def _cached_node_secret(base: str) -> str:
    """This machine's node_secret for `base`, from `pworkers join`'s identity first (a worker's own
    account), falling back to the assisted-flow operator.json identity. Never constructs a
    fresh Operator() here — that would mint a THIRD node identity for one machine, compounding
    the F8 review finding rather than working around it."""
    from passiveworkers.net.agent import _load_join
    from passiveworkers import paths
    joined = paths.coordinator_entries(_load_join())
    entry = joined.get(base)
    if entry and entry.get("node_secret"):
        return entry["node_secret"]
    try:
        cached = json.loads(STATE.read_text()).get(base)
        if cached and cached.get("secret"):
            return cached["secret"]
    except Exception:
        pass
    return ""


def credit() -> int:
    """`pworkers credit` — this worker's own balance/reputation/jobs-helped (R33 review) — previously
    visible only via the public, all-operators GET /leaderboard."""
    base = os.environ.get("PW_COORDINATOR", "").rstrip("/")
    if not base:
        print("✗ set PW_COORDINATOR (or run `pworkers join` first)"); return 2
    secret = _cached_node_secret(base)
    if not secret:
        print(f"✗ no joined identity found for {base} — run `pworkers join {base} <token>` first")
        return 2
    try:
        r = requests.get(f"{base}/nodes/me", headers={"X-Node-Secret": secret}, timeout=15)
    except requests.RequestException as exc:
        print(f"✗ could not reach {base}: {exc}"); return 1
    if not r.ok:
        print(f"✗ {r.json().get('detail', r.text)}"); return 1
    d = r.json()
    print(f"balance: {d['balance']:.1f} cr   reputation: {d['reputation']:.1f}   "
          f"jobs helped: {d['helped']}")
    return 0


def invite(argv: list) -> int:
    """`pworkers invite [--owner N] [--kind any|node|user] [--grant N] [--max-uses N]` — mint an
    enrollment token (D37) with a real CLI call instead of raw curl against /admin/enroll
    (R33 review). Requires PW_COORDINATOR + PW_TOKEN (the coordinator's ADMIN token)."""
    import argparse
    ap = argparse.ArgumentParser(prog="pworkers invite", add_help=False)
    ap.add_argument("--owner", default="")
    ap.add_argument("--kind", choices=["any", "node", "user"], default="any")
    ap.add_argument("--grant", type=float, default=None)
    ap.add_argument("--max-uses", type=int, default=1)
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        print("usage: pworkers invite [--owner NAME] [--kind any|node|user] [--grant N] [--max-uses N]")
        return 2
    base = os.environ.get("PW_COORDINATOR", "").rstrip("/")
    token = os.environ.get("PW_TOKEN", "")
    if not base or not token:
        print("✗ set PW_COORDINATOR and PW_TOKEN (the coordinator's ADMIN token)"); return 2
    body = {"owner": args.owner, "kind": args.kind, "max_uses": args.max_uses}
    if args.grant is not None:
        body["grant"] = args.grant
    try:
        r = requests.post(f"{base}/admin/enroll", json=body, headers={"X-PW-Token": token}, timeout=15)
    except requests.RequestException as exc:
        print(f"✗ could not reach {base}: {exc}"); return 1
    if not r.ok:
        print(f"✗ {r.json().get('detail', r.text)}"); return 1
    d = r.json()
    print(f"✓ enrollment token minted (kind={d['kind']}, owner={d.get('owner') or '(any)'}, "
          f"max_uses={d['max_uses']}):\n\n  {d['enroll_token']}\n\n"
          f"Worker: pworkers join {base} {d['enroll_token']}\n"
          f"Asker:  pworkers ask \"...\" --enroll-token {d['enroll_token']}")
    return 0


def keygen() -> int:
    """Asker: create/reveal your encryption PUBLIC key. Include it as `encrypt_to` when posting
    an assisted job so operators encrypt the deliverable so only you can open it."""
    from passiveworkers import crypto as C
    if not C.available():
        print("install the crypto extra first: pip install 'passiveworkers[crypto]'"); return 2
    kp = C.load_or_create(_asker_keys_path(), "box")
    print("Your encryption public key (pass as encrypt_to when posting a job):\n")
    print(kp["pub"])
    return 0


def fingerprint() -> int:
    """Operator: print your SIGNING public key + its fingerprint so an asker can pin you out of
    band (the trust anchor a hostile coordinator can't forge, D25)."""
    from passiveworkers import crypto as C
    from passiveworkers import trust
    if not C.available():
        print("install the crypto extra first: pip install 'passiveworkers[crypto]'"); return 2
    kp = C.load_or_create(STATE.parent / "operator_keys.json", "sign")
    print("Your signing public key (share with askers so they can `pworkers trust add <you> <key>`):\n")
    print(kp["pub"])
    print(f"\nFingerprint (read this to them over a trusted channel): {trust.fingerprint(kp['pub'])}")
    return 0


def trust_cmd(args: list) -> int:
    """Asker: manage pinned operator signing keys (out-of-band trust, D25).
        pworkers trust add <operator> <pubkey>   pin an operator's signing key
        pworkers trust list                      show pinned operators + fingerprints
        pworkers trust remove <operator>         unpin
    """
    from passiveworkers import trust
    sub = args[0] if args else ""
    if sub == "add" and len(args) >= 3:
        rec = trust.pin(args[1], args[2], source="manual")
        print(f"✓ pinned operator '{args[1]}' → {rec['fp']}\n"
              f"  Confirm this fingerprint matches what the operator told you (pworkers fingerprint).")
        return 0
    if sub == "list":
        pins = trust.list_pins()
        if not pins:
            print("No pinned operators yet. They're pinned on first signed delivery (TOFU), or "
                  "explicitly with: pworkers trust add <operator> <pubkey>")
            return 0
        for op, rec in sorted(pins.items()):
            print(f"  {op:<20} {rec['fp']}   ({rec.get('source', '?')})")
        return 0
    if sub == "remove" and len(args) >= 2:
        print("✓ unpinned" if trust.remove(args[1]) else "✗ no such pinned operator")
        return 0
    print("usage: pworkers trust add <operator> <pubkey> | pworkers trust list | pworkers trust remove <operator>")
    return 2


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: pworkers tasks | accept <id> | deliver <id> <text|@file <job>> | fetch <job> <dir>\n"
              "       keygen | rate <job> <0-10> | fingerprint | trust add|list|remove")
        return 2
    if args[0] == "fetch" and len(args) >= 3:
        return fetch(args[1], args[2])
    if args[0] == "keygen":
        return keygen()
    if args[0] == "fingerprint":
        return fingerprint()
    if args[0] == "trust":
        return trust_cmd(args[1:])
    if args[0] == "rate" and len(args) >= 3:
        return rate(args[1], args[2])
    if args[0] == "credit":
        return credit()
    if args[0] == "invite":
        return invite(args[1:])
    op = Operator()
    cmd, rest = args[0], args[1:]
    if cmd == "tasks":
        return op.tasks()
    if cmd == "accept" and rest:
        return op.accept(rest[0])
    if cmd == "deliver" and len(rest) >= 2:
        # file form: pworkers deliver <task_id> @path <job_id>   ·   text form: pworkers deliver <task_id> <text…>
        if rest[1].startswith("@"):
            return op.deliver(rest[0], rest[1], rest[2] if len(rest) > 2 else "")
        return op.deliver(rest[0], " ".join(rest[1:]))
    print("usage: pworkers tasks | pworkers accept <id> | pworkers deliver <id> <text | @file <job_id>>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
