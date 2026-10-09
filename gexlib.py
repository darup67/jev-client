"""Single-stock GEX (dealer gamma exposure) + options flow summary from Cboe delayed option chains (free JSON, ~15 min delayed, open interest as of the prior close).
Shared by the options-IV email (~/market-iv-agent) and Jev Options IV (~/jev-options-iv). The index-futures version lives in ~/jev-markets/gex.py (same method).
READ-ONLY CONTEXT, not a signal and not backtested: the dealer-positioning convention (dealers long customer-sold calls, short customer-bought puts) is an assumption.
  summarize("AAPL") -> {"spot", "regime", "net_gex_m", "call_wall", "put_wall", "flip", "flow": {...}, "line": "one-line text"} or None when the chain is unavailable."""
import datetime as dt, math, time
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
_cache = {}


def _sym(t):
    return t.replace(".", "").replace("-", "")


def _gamma(S, K, T, iv, r=0.04):
    if T <= 0 or iv <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + iv * iv / 2) * T) / (iv * math.sqrt(T))
    return math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def _parse(sym, name):
    t = name[len(sym):]
    return dt.date(2000 + int(t[0:2]), int(t[2:4]), int(t[4:6])), t[6], int(t[7:]) / 1000.0


def monthly_expiry(today=None, now=None):
    """The next standard monthly expiration (third Friday) that has not expired yet (4 PM ET)."""
    now = now or dt.datetime.now(ET)
    today = today or now.date()
    for add in (0, 1, 2):
        y, mo = today.year + (today.month - 1 + add) // 12, (today.month - 1 + add) % 12 + 1
        d = dt.date(y, mo, 15)
        d += dt.timedelta(days=(4 - d.weekday()) % 7)             # third Friday = first Friday on/after the 15th
        if dt.datetime.combine(d, dt.time(16, 0), ET) > now:
            return d
    return None


def hedge_levels(opts, S, now=None, step=0.25, span=5.0):
    """Monthly hedge pressure and dynamic hedge-pressure walls from a list of option dicts (keys: exp, dte, cp, K, T, iv, oi, gamma).
    MONTHLY: only the next standard monthly expiry: its net GEX (dealer gamma $ per 1%), call wall, put wall and the 'key strike' = the strike with the largest absolute net gamma
      (positive = a magnet/pin, negative = a repel zone).
    DYNAMIC: the total GEX profile is recomputed on a grid of hypothetical prices (own Black-Scholes gamma, Cboe IV, time to expiry) instead of using today's static strike open interest, so the
      walls move with price, IV and time: up_wall = the price above spot where dealer gamma peaks (strongest dampening / ceiling), dn_wall = the same below spot (floor), flip = zero crossing.
    ASSUMPTION: dealers are long customer-sold calls and short customer-bought puts (calls +, puts -)."""
    now = now or dt.datetime.now(ET)
    mult = lambda s: 100 * s * s * 0.01
    liquid = [o for o in opts if o["oi"] > 0 and 0.03 < o["iv"] < 4.0 and o["T"] > 0]
    n = int(round(span / step))
    grid = [S * (1 + (i * step) / 100.0) for i in range(-n, n + 1)]
    prof = [sum(_gamma(s, o["K"], o["T"], o["iv"]) * o["oi"] * mult(s) * (1 if o["cp"] == "C" else -1) for o in liquid) for s in grid]
    inner = list(zip(prof, grid))[1:-1]                            # a peak on the grid's edge is just a profile still rising: not a wall
    up = [(p, s) for p, s in inner if s > S * 1.0005 and p > 0]
    dn = [(p, s) for p, s in inner if s < S * 0.9995 and p > 0]
    acc = [(p, s) for p, s in inner if s < S * 0.9995 and p < 0]
    out = {"dyn_accel": min(acc)[1] if acc else None, "dyn_accel_gex": min(acc)[0] if acc else None, "dyn_up": max(up)[1] if up else None, "dyn_dn": max(dn)[1] if dn else None, "dyn_up_gex": max(up)[0] if up else None, "dyn_dn_gex": max(dn)[0] if dn else None,
           "profile": [[round(s, 4), round(p / 1e6, 1)] for s, p in zip(grid[::4], prof[::4])]}
    me = monthly_expiry(now.date(), now)
    mo = [o for o in opts if o["exp"] == me]
    if me and mo and sum(o["oi"] for o in mo) > 0:
        by = {}
        for o in mo:
            g = o["gamma"] if o["gamma"] > 0 else (_gamma(S, o["K"], o["T"], o["iv"]) if 0.03 < o["iv"] < 4.0 else 0.0)
            gx = g * o["oi"] * mult(S)
            d = by.setdefault(o["K"], {"c": 0.0, "p": 0.0})
            d["c" if o["cp"] == "C" else "p"] += gx
        net = sum(v["c"] - v["p"] for v in by.values())
        ca = {k: v["c"] for k, v in by.items() if k >= S and v["c"] > 0}
        pb = {k: v["p"] for k, v in by.items() if k <= S and v["p"] > 0}
        key = max(by, key=lambda k: abs(by[k]["c"] - by[k]["p"]))
        out["monthly"] = {"exp": me.isoformat(), "dte": (me - now.date()).days, "net_gex": net, "call_wall": max(ca, key=ca.get) if ca else None, "put_wall": max(pb, key=pb.get) if pb else None,
                          "key_strike": key, "key_net": by[key]["c"] - by[key]["p"]}
    return out


def summarize(ticker, dte_max=45, band=0.25, max_age=900, timeout=40):
    sym = _sym(ticker)
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        r = requests.get(URL.format(sym), headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code != 200:
            _cache[sym] = (time.time(), None)
            return None
        raw = r.json()["data"]
        out = _compute(sym, raw, dte_max, band)
    except Exception:
        return hit[1] if hit else None
    _cache[sym] = (time.time(), out)
    return out


def _compute(sym, data, dte_max, band):
    S = float(data["current_price"])
    now = dt.datetime.now(ET)
    today = now.date()
    opts = []
    for o in data["options"]:
        try:
            exp, cp, K = _parse(sym, o["option"])
        except (ValueError, IndexError):
            continue
        dte = (exp - today).days
        if dte < 0 or dte > dte_max or abs(K / S - 1) > band:
            continue
        T = (dt.datetime.combine(exp, dt.time(16, 0), ET) - now).total_seconds() / (365 * 86400)
        if T <= 0:
            continue
        opts.append({"exp": exp, "dte": dte, "cp": cp, "K": K, "T": T, "iv": float(o.get("iv") or 0), "oi": float(o.get("open_interest") or 0), "vol": float(o.get("volume") or 0),
                     "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0), "last": float(o.get("last_trade_price") or 0), "delta": float(o.get("delta") or 0), "gamma": float(o.get("gamma") or 0)})
    if not opts or sum(o["oi"] for o in opts) < 200:
        return None
    mult = 100 * S * S * 0.01
    by_k, net = {}, 0.0
    for o in opts:
        g = o["gamma"] if o["gamma"] > 0 else (_gamma(S, o["K"], o["T"], o["iv"]) if 0.03 < o["iv"] < 4.0 else 0.0)
        gx = g * o["oi"] * mult
        d = by_k.setdefault(o["K"], {"c": 0.0, "p": 0.0})
        d["c" if o["cp"] == "C" else "p"] += gx
        net += gx if o["cp"] == "C" else -gx
    above = {k: v["c"] for k, v in by_k.items() if k >= S and v["c"] > 0}
    below = {k: v["p"] for k, v in by_k.items() if k <= S and v["p"] > 0}
    call_wall = max(above, key=above.get) if above else None
    put_wall = max(below, key=below.get) if below else None
    liquid = [o for o in opts if o["oi"] > 0 and 0.03 < o["iv"] < 4.0]
    grid = [S * (1 + x / 100.0) for x in range(-12, 13)]          # -12% .. +12% (stocks move more than indexes)
    tot = []
    for s in grid:
        m = 100 * s * s * 0.01
        tot.append(sum(_gamma(s, o["K"], o["T"], o["iv"]) * o["oi"] * m * (1 if o["cp"] == "C" else -1) for o in liquid))
    flip = None
    for i in range(len(grid) - 1):
        a, b = tot[i], tot[i + 1]
        if a == 0 or (a < 0) != (b < 0):
            x = grid[i] + (grid[i + 1] - grid[i]) * (0 if b == a else -a / (b - a))
            if flip is None or abs(x - S) < abs(flip - S):
                flip = x
    hl = hedge_levels(opts, S, now, step=1.0, span=12.0)
    cv = sum(o["vol"] for o in opts if o["cp"] == "C")
    pv = sum(o["vol"] for o in opts if o["cp"] == "P")
    signed = absn = 0.0
    for o in opts:
        if o["vol"] <= 0 or o["ask"] <= 0 or o["last"] <= 0:
            continue
        sp = max(o["ask"] - o["bid"], 0.01)
        aggr = 1 if o["last"] >= o["ask"] - 0.1 * sp else (-1 if o["last"] <= o["bid"] + 0.1 * sp else 0)
        dn = o["vol"] * o["delta"] * 100 * S
        signed += aggr * dn
        absn += abs(dn)
    bias = round(signed / absn, 2) if absn else None
    pcv = round(pv / cv, 2) if cv else None
    regime = "positive" if net > 0 else "negative"
    near = flip is not None and abs(flip / S - 1) < 0.015
    pct = lambda x: f"{(x / S - 1):+.0%}"
    parts = [f"{'+' if net > 0 else '−'}gamma{' (near flip)' if near else ''}"]
    if call_wall:
        parts.append(f"call wall ${call_wall:g} ({pct(call_wall)})")
    if put_wall:
        parts.append(f"put wall ${put_wall:g} ({pct(put_wall)})")
    if flip:
        parts.append(f"flip ${flip:.2f} ({pct(flip)})")
    mo = hl.get("monthly")
    if mo and mo.get("key_strike"):
        parts.append(f"monthly {'magnet' if mo['key_net'] > 0 else 'repel'} ${mo['key_strike']:g} ({pct(mo['key_strike'])})")
    if hl.get("dyn_up"):
        parts.append(f"dynamic wall ${hl['dyn_up']:.2f} ({pct(hl['dyn_up'])})")
    if pcv is not None:
        parts.append(f"P/C vol {pcv}")
    if bias is not None and abs(bias) >= 0.1:
        parts.append("flow lean " + ("bullish" if bias > 0 else "bearish"))
    return {"spot": S, "regime": regime, "near_flip": near, "net_gex_m": round(net / 1e6, 1), "call_wall": call_wall, "put_wall": put_wall, "flip": flip,
            "flow": {"call_vol": int(cv), "put_vol": int(pv), "pc_vol": pcv, "bias": bias}, "monthly": mo, "dyn_up": hl.get("dyn_up"), "dyn_dn": hl.get("dyn_dn"), "line": " · ".join(parts), "t": time.time()}


if __name__ == "__main__":
    import json, sys
    for t in sys.argv[1:] or ["RKLB"]:
        print(t, json.dumps(summarize(t), default=str))
