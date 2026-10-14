"""Jev token optimizer (2026-10-14): spend fewer Jev tokens for the SAME decisions. Stdlib only; imported by jev.py, never raises into a caller.

Four parts, all driven by ~/jev-client/policy.json (hot-reloaded every 30 s; "optimizer.enabled": false turns every part off and jev.ask() behaves exactly as before):
  1. QUANTIZED KEYS   The answer-reuse key is built from a quantized state: noisy fields a judgement does not hinge on (exact price, spread, book depth, funding ticks...) are ignored or bucketed
                      instead of compared to 2 significant figures, so "the same situation" really hashes the same. Categorical fields (trend labels, setups) always stay exact.
  2. SHARED CACHE     Answers live in cache.db (sqlite, WAL) shared by EVERY process: the three Majors lanes, both Markets lanes and Predict all reuse each other's paid answers; a
                      single-flight lock makes a second asker wait a few seconds for the first instead of paying twice.
  3. AUDITED REUSE    A small share of cache hits (audit_p) is re-asked for real; the divergence between the reused and the fresh answer (0 = identical, 1 = opposite) is logged to audit.jsonl and
                      drives a per-caller TTL scale: if reuse starts to disagree with fresh answers the TTL halves automatically (down to 10%); if it stays tight the TTL recovers. Performance is
                      protected by measurement, not by assumption.
  4. SPEND GOVERNOR   Projects the month's spend from the pace so far. Over the line it first serves low-priority callers from cache only, then doubles the TTL of high-priority ones.
policy.json: {"optimizer": {"enabled", "audit_p", "max_div", "pace_warn", "pace_cut"}, "callers": {name: {"priority": "high|low", "ttl_s", "ignore": [glob paths], "abs": {glob: step}, "rel_step": 0.15}}}
CLI: python3 jev.py --report  (spend by caller, hit rate, audit divergence, projection)"""
import fnmatch, hashlib, json, math, os, sqlite3, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("JEVOPT_DIR", HERE)                       # tests point this at a scratch folder
POLICY = os.path.join(DIR, "policy.json")
DB = os.path.join(DIR, "cache.db")
AUDIT = os.path.join(DIR, "audit.jsonl")
GOVERN = os.path.join(DIR, "govern.json")
_lock = threading.Lock()
_pol = {"t": 0.0, "v": {}}
_db = {"c": None}
_aud = {}                                    # caller -> recent divergences (rolling)


def policy():
    with _lock:
        if time.time() - _pol["t"] > 30:
            try:
                _pol["v"] = json.load(open(POLICY))
            except Exception:
                _pol["v"] = _pol["v"] or {}
            _pol["t"] = time.time()
        return _pol["v"]


def enabled():
    return bool((policy().get("optimizer") or {}).get("enabled"))


def caller_policy(caller):
    return {**((policy().get("callers") or {}).get("*") or {}), **((policy().get("callers") or {}).get(caller) or {})}


def _conn():
    with _lock:
        if _db["c"] is None:
            c = sqlite3.connect(DB, timeout=10, check_same_thread=False, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, t REAL, caller TEXT, res TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS inflight(k TEXT PRIMARY KEY, t REAL)")
            _db["c"] = c
        return _db["c"]


# ------------------------------------------------------------------ quantized key
def _flat(o, p=""):
    if isinstance(o, dict):
        for k in sorted(o):
            yield from _flat(o[k], f"{p}{k}.")
    elif isinstance(o, (list, tuple)):
        for i, v in enumerate(o):
            yield from _flat(v, f"{p}{i}.")
    else:
        yield p[:-1], o


def quant_key(state, questions, name, cp):
    """Hash of (name, quantized state, questions). Numbers: ignored / absolute-step buckets by path glob / log buckets (rel_step) by default; everything else exact."""
    ignore, absr, rel = cp.get("ignore") or [], cp.get("abs") or {}, float(cp.get("rel_step") or 0.15)
    lg = math.log(1 + rel)
    q = []
    for path, v in _flat(state):
        if any(fnmatch.fnmatch(path, g) for g in ignore):
            continue
        if isinstance(v, bool) or v is None or isinstance(v, str):
            q.append((path, v))
        elif isinstance(v, (int, float)):
            step = next((s for g, s in absr.items() if fnmatch.fnmatch(path, g)), None)
            if step:
                q.append((path, round(v / step)))
            elif v == 0 or abs(v) < 1e-9:
                q.append((path, 0))
            else:
                q.append((path, (1 if v > 0 else -1) * round(math.log(abs(v)) / lg)))
        else:
            q.append((path, str(v)))
    return name + ":" + hashlib.md5(json.dumps([q, questions], sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------ cache
def ttl_for(caller, base_ttl, level=0):
    cp = caller_policy(caller)
    t = float(cp.get("ttl_s") or base_ttl or 0)
    t *= govern().get("ttl_scale", {}).get(caller, 1.0)
    if level >= 2 and cp.get("priority") != "low":
        t *= 2
    return t


def get(k, ttl, stale_ok_s=0):
    try:
        r = _conn().execute("SELECT t, res FROM cache WHERE k=?", (k,)).fetchone()
        if r and time.time() - r[0] < max(ttl, stale_ok_s):
            return json.loads(r[1]), time.time() - r[0]
    except Exception:
        pass
    return None, None


def put(k, caller, res):
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO cache(k,t,caller,res) VALUES(?,?,?,?)", (k, time.time(), caller, json.dumps(res, default=str)))
        if int(time.time()) % 50 == 0:
            c.execute("DELETE FROM cache WHERE t<?", (time.time() - 6 * 3600,))
    except Exception:
        pass


def lead(k):
    """True if THIS process should pay for key k (nobody asked in the last 30 s); False if another process is already asking."""
    try:
        c = _conn()
        c.execute("DELETE FROM inflight WHERE t<?", (time.time() - 30,))
        c.execute("INSERT INTO inflight(k,t) VALUES(?,?)", (k, time.time()))
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception:
        return True


def done(k):
    try:
        _conn().execute("DELETE FROM inflight WHERE k=?", (k,))
    except Exception:
        pass


def wait_for(k, ttl, max_s=8.0):
    t0 = time.time()
    while time.time() - t0 < max_s:
        res, age = get(k, ttl)
        if res is not None:
            return res, age
        time.sleep(0.25)
    return None, None


# ------------------------------------------------------------------ audit + TTL governor
def divergence(a, b):
    """0 = identical, 1 = opposite, over the questions both answers have. choice: total-variation distance of the probabilities; score: |d|/3; noul: |d|."""
    ds = []
    for q, x in (a or {}).items():
        y = (b or {}).get(q)
        if not y or x.get("type") != y.get("type"):
            continue
        t = x["type"]
        try:
            if t == "choice":
                px, py = x.get("probabilities") or {}, y.get("probabilities") or {}
                ds.append(0.5 * sum(abs(px.get(k, 0) - py.get(k, 0)) for k in set(px) | set(py)) if px and py else float(x.get("choice") != y.get("choice")))
            elif t == "score":
                ds.append(abs(x["score"] - y["score"]) / 3.0)
            elif t == "noul":
                ds.append(abs(x["noul"] - y["noul"]))
        except Exception:
            continue
    return sum(ds) / len(ds) if ds else None


def govern():
    try:
        return json.load(open(GOVERN))
    except Exception:
        return {}


def record_audit(caller, name, age, div):
    pol = policy().get("optimizer") or {}
    with _lock:
        _aud.setdefault(caller, []).append(div)
        _aud[caller] = _aud[caller][-60:]
        win = list(_aud[caller])
    try:
        with open(AUDIT, "a") as f:
            f.write(json.dumps({"t": time.time(), "caller": caller, "name": name, "age_s": round(age or 0), "div": round(div, 4)}) + "\n")
    except OSError:
        pass
    if len(win) >= 15:                                           # enough evidence: retune this caller's TTL
        mean, mx = sum(win) / len(win), float(pol.get("max_div", 0.10))
        g = govern()
        sc = g.setdefault("ttl_scale", {})
        cur = sc.get(caller, 1.0)
        new = max(0.1, cur * 0.5) if mean > mx else min(1.0, cur * 1.25) if mean < 0.5 * mx else cur
        if new != cur:
            sc[caller] = new
            g.setdefault("log", []).append({"t": time.time(), "caller": caller, "mean_div": round(mean, 3), "ttl_scale": new})
            g["log"] = g["log"][-50:]
            try:
                json.dump(g, open(GOVERN, "w"))
            except OSError:
                pass
            with _lock:
                _aud[caller] = []


# ------------------------------------------------------------------ pace governor
_pace = {"t": 0.0, "level": 0, "proj": 0.0}


def pace(month_spend_fn, rate_fn=None):
    """0 = on plan, 1 = projected over `pace_warn` x cap (low-priority callers cache-only), 2 = projected over `pace_cut` x cap (high-priority TTL doubled).
    Projection = spent so far + the last 72 hours' average daily spend x days left (a month-to-date average would hide a recent ramp-up)."""
    if time.time() - _pace["t"] < 60:
        return _pace["level"]
    pol = policy().get("optimizer") or {}
    try:
        spent, cap = month_spend_fn()
        lt = time.localtime()
        days = 31 if lt.tm_mon in (1, 3, 5, 7, 8, 10, 12) else 30 if lt.tm_mon != 2 else 28
        left = max(0.0, days - (lt.tm_mday - 1) - (lt.tm_hour * 3600 + lt.tm_min * 60) / 86400.0)
        daily = (rate_fn(72) / 3.0) if rate_fn else spent / max(1.0, days - left)
        proj = spent + daily * left
        lvl = 2 if proj > float(pol.get("pace_cut", 1.05)) * cap else 1 if proj > float(pol.get("pace_warn", 0.90)) * cap else 0
        _pace.update(t=time.time(), level=lvl, proj=proj, daily=daily)
    except Exception:
        pass
    return _pace["level"]


def projection():
    return _pace["proj"]
