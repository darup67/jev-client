#!/usr/bin/env python3
"""Shared Jev client (TypeSafe System One) for the local agents. Stdlib only.

  import sys, os; sys.path.insert(0, os.path.expanduser("~/jev-client"))
  import jev
  res = jev.ask(state, questions, caller="coin-launch")   # dict, or None on any failure

  python3 jev.py --ping     one tiny call; checks the key and the endpoint
  python3 jev.py --usage    calls, failures, tokens and cost per caller per day

ask() never raises. A missing key, a timeout or an outage returns None, so a
caller that uses Jev keeps working without it. Every call is logged to
calls.jsonl (caller, model, latency, tokens, error). The state itself is not
logged here; callers keep their own record of what they asked.

The key comes from $TYPESAFE_API_KEY, else the Keychain:
  security add-generic-password -a "$USER" -s typesafe-jev -w '<key>'
"""
import json, os, subprocess, sys, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "calls.jsonl")
URL = "https://api.typesafe.ai/v1/systemone"
# Pinned, not jev-latest: the agents compare answers over weeks, and an alias
# that moves mid-trial would mix two models' answers in one comparison.
MODEL = "jev-1.13.0"
USD_PER_MTOK = 0.042
RETRY = {429, 500, 502, 503, 504, 529}

_key, _looked, _warned, _lock = "", 0.0, False, threading.Lock()


def api_key():
    """The key, or "". A missing key is looked up again every 5 min, so an
    always-on agent picks up a key added to the Keychain without a restart."""
    global _key, _looked
    if not _key and time.time() - _looked > 300:
        _looked = time.time()
        _key = os.environ.get("TYPESAFE_API_KEY") or ""
        if not _key:
            try:
                _key = subprocess.run(["security", "find-generic-password", "-s", "typesafe-jev", "-w"],
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            except Exception:
                _key = ""
    return _key


def _log(rec):
    with _lock:
        try:
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass


def ask(state, questions, caller="?", model=MODEL, timeout=10, tries=3):
    """POST one state and its questions. Returns {"model", "answers", "usage"} or None."""
    global _warned
    key = api_key()
    if not key:
        if not _warned:
            print("jev: no API key (Keychain service typesafe-jev); skipping", file=sys.stderr)
            _warned = True
        return None
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    t0, err = time.time(), None
    for i in range(tries):
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
            u = res.get("usage") or {}
            _log({"t": time.time(), "caller": caller, "model": res.get("model"), "ok": True,
                  "ms": round((time.time() - t0) * 1000), "in": u.get("input_tokens"),
                  "out": u.get("output_tokens"), "q": sorted(questions)})
            return res
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            err = f"HTTP {e.code} {detail}"
            if e.code not in RETRY:
                break
            wait = float(e.headers.get("retry-after") or 2 ** i)
        except Exception as e:
            err, wait = repr(e), 2 ** i
        if i < tries - 1:
            time.sleep(min(wait, 10))
    _log({"t": time.time(), "caller": caller, "model": model, "ok": False,
          "ms": round((time.time() - t0) * 1000), "err": err, "q": sorted(questions)})
    print(f"jev: call failed ({caller}): {err}", file=sys.stderr)
    return None


def usage():
    rows = {}
    try:
        with open(LOG) as f:
            for line in f:
                r = json.loads(line)
                k = (time.strftime("%Y-%m-%d", time.localtime(r["t"])), r["caller"])
                a = rows.setdefault(k, [0, 0, 0])
                a[0] += 1
                a[1] += 0 if r["ok"] else 1
                a[2] += r.get("in") or 0
    except FileNotFoundError:
        print("no calls yet")
        return
    print(f"{'day':<11} {'caller':<14} {'calls':>6} {'failed':>7} {'in tok':>9} {'cost':>8}")
    for (day, caller), (n, bad, tok) in sorted(rows.items()):
        print(f"{day:<11} {caller:<14} {n:>6} {bad:>7} {tok:>9} ${tok / 1e6 * USD_PER_MTOK:>7.4f}")


if __name__ == "__main__":
    if "--usage" in sys.argv:
        usage()
    elif "--ping" in sys.argv:
        r = ask("Help! My payouts have been failing for 3 days.",
                {"is_urgent": {"type": "noul", "instructions": "Does this message convey urgency?"}},
                caller="ping")
        print(json.dumps(r, indent=2) if r else "FAILED (see stderr / calls.jsonl)")
        sys.exit(0 if r else 1)
    else:
        print(__doc__)
