#!/usr/bin/env python3
"""Shared Jev client (TypeSafe System One) for the local agents. Stdlib only.

  import sys, os; sys.path.insert(0, os.path.expanduser("~/jev-client"))
  import jev
  res = jev.ask(state, questions, caller="coin-launch")   # dict, or None on any failure

  python3 jev.py --ping     one tiny call; checks the key and the endpoint
  python3 jev.py --usage    calls, failures, tokens and cost per caller per day, plus this month vs the budget
  python3 jev.py --set-key  paste a TypeSafe key (hidden); stores it in the Keychain, then pings

BUDGET: budget.json {"monthly_usd": 25} caps spend across ALL agents per calendar month.
Spend = this month's input tokens in calls.jsonl x $0.042/Mtok (output is free). Once the next
call would cross the cap, ask() returns None (like an outage) and logs a "budget" failure.

ask() never raises. A missing key, a timeout or an outage returns None, so a
caller that uses Jev keeps working without it. Every call is logged to
calls.jsonl (caller, model, latency, tokens, error). The state itself is not
logged here; callers keep their own record of what they asked.

The key comes from $TYPESAFE_API_KEY, else the Keychain:
  security add-generic-password -a "$USER" -s typesafe-jev -w '<key>'
"""
import hashlib, json, os, subprocess, sys, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "calls.jsonl")
URL = "https://api.typesafe.ai/v1/systemone"
# Pinned, not jev-latest: the agents compare answers over weeks, and an alias
# that moves mid-trial would mix two models' answers in one comparison.
MODEL = "jev-1.13.0"
USD_PER_MTOK = 0.042
RETRY = {429, 500, 502, 503, 504, 529}

_key, _looked, _warned, _lock = "", 0.0, False, threading.Lock()
BUDGET = os.path.join(HERE, "budget.json")
_spend = {"month": None, "offset": 0, "tokens": 0}
_budget_warned = None


def month_spend():
    """(usd spent this calendar month by every caller, monthly cap). Reads only the part of
    calls.jsonl appended since the last look, so it stays cheap as the log grows."""
    month = time.strftime("%Y-%m")
    with _lock:
        if _spend["month"] != month:
            _spend.update(month=month, offset=0, tokens=0)
        try:
            with open(LOG, "rb") as f:
                f.seek(_spend["offset"])
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1                # only whole lines
            for line in chunk[:end].splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("in") and time.strftime("%Y-%m", time.localtime(r["t"])) == month:
                    _spend["tokens"] += r["in"]
            _spend["offset"] += end
        except FileNotFoundError:
            pass
        tokens = _spend["tokens"]
    try:
        cap = float(json.load(open(BUDGET))["monthly_usd"])
    except Exception:
        cap = 25.0                                    # budget file missing or broken: keep the default cap
    return tokens / 1e6 * USD_PER_MTOK, cap


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


_cache = {}


def _coarse(o):
    """Numbers to 2 significant figures, so a state that has not moved by more than a percent or so hashes the same as before."""
    if isinstance(o, bool):
        return o
    if isinstance(o, float):
        return float(f"{o:.2g}") if o == o else o
    if isinstance(o, int):
        return int(float(f"{o:.2g}")) if abs(o) >= 100 else o
    if isinstance(o, dict):
        return {k: _coarse(v) for k, v in sorted(o.items())}
    if isinstance(o, (list, tuple)):
        return [_coarse(v) for v in o]
    return o


def ask(state, questions, caller="?", model=MODEL, timeout=10, tries=3, reuse=None):
    """POST one state and its questions. Returns {"model", "answers", "usage"} or None.
    reuse=(name, ttl_seconds): within ttl, the SAME question set on a state that matches to 2 significant figures returns the earlier answer
    (marked "_cached": True, logged with zero tokens) instead of paying again. Nothing is reused across different questions or a changed state."""
    global _warned
    ck = None
    if reuse:
        ck = reuse[0] + ":" + hashlib.md5(json.dumps([_coarse(state), questions], default=str, sort_keys=True).encode()).hexdigest()
        hit = _cache.get(ck)
        if hit and time.time() - hit[0] < reuse[1]:
            _log({"t": time.time(), "caller": caller, "model": hit[1].get("model"), "ok": True, "ms": 0, "in": 0, "out": 0, "cached": True, "q": sorted(questions)})
            return {**hit[1], "_cached": True}
    key = api_key()
    if not key:
        if not _warned:
            print("jev: no API key (Keychain service typesafe-jev); skipping", file=sys.stderr)
            _warned = True
        return None
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    spent, cap = month_spend()
    if spent + len(body) / 1e6 * USD_PER_MTOK >= cap:     # ~1 token per byte is an over-estimate: safe side
        global _budget_warned
        if _budget_warned != time.strftime("%Y-%m"):
            print(f"jev: monthly budget reached (${spent:.2f} of ${cap:.2f}); skipping calls until next month "
                  f"or until budget.json is raised", file=sys.stderr)
            _budget_warned = time.strftime("%Y-%m")
        _log({"t": time.time(), "caller": caller, "model": model, "ok": False, "ms": 0,
              "err": f"budget: ${spent:.4f} of ${cap:.2f} this month", "q": sorted(questions)})
        return None
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
            if ck:
                if len(_cache) > 3000:
                    _cache.clear()
                _cache[ck] = (time.time(), res)
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
    spent, cap = month_spend()
    print(f"\n{time.strftime('%Y-%m')}: ${spent:.4f} of the ${cap:.2f} monthly budget ({100 * spent / cap:.2f}%)")


def set_key():
    """Prompt for the key without echo and store it in the Keychain (service typesafe-jev).
    Run in your own terminal; the key never touches a file or shell history."""
    import getpass
    k = getpass.getpass("TypeSafe API key (input hidden): ").strip()
    if not k:
        print("nothing entered; unchanged")
        return 1
    # -U updates an existing item instead of failing on a duplicate
    r = subprocess.run(["security", "add-generic-password", "-U", "-a", os.environ.get("USER", "jev"),
                        "-s", "typesafe-jev", "-w", k], capture_output=True, text=True)
    if r.returncode:
        print(f"Keychain write failed: {r.stderr.strip()}")
        return 1
    global _key, _looked
    _key, _looked = "", 0.0
    print("stored in Keychain (service typesafe-jev). Testing it...")
    ok = ask("Help! My payouts have been failing for 3 days.",
             {"is_urgent": {"type": "noul", "instructions": "Does this message convey urgency?"}}, caller="ping")
    print("Jev answered: the key works. Agents pick it up within 5 minutes." if ok else
          "Stored, but the test call failed; see calls.jsonl. Check the key or TypeSafe's status.")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--set-key" in sys.argv:
        sys.exit(set_key())
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
