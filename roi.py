#!/usr/bin/env python3
"""Standard ROI scoreboard for every Jev desk and lane (stdlib only). One row per paper book:
  start capital, equity, ROI since start (equity / start - 1), 7-day and month-to-date ROI, annualised ROI (only once the book is >= 14 days old: a few days annualised is noise), max drawdown, days running.
Equity series come from each desk's own data/equity.jsonl (or, for the FOMO lane, realised P/L from fomo.db). Read-only. python3 roi.py  -> table;  import roi; roi.collect() -> list of dicts."""
import datetime as dt, json, os, sqlite3, time

HOME = os.path.expanduser("~")
MIN_DAYS_ANNUAL = 14


def bench():
    b = {"min": 0.5, "strong": 0.7}
    try:
        b.update(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "roi_benchmark.json"))))
    except (OSError, ValueError):
        pass
    return b

OPT = os.path.join(HOME, "jev-options-iv")


def _jl(path):
    out = []
    try:
        for l in open(path):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _jf(path, d=None):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return d


def _kv(db, k):
    try:
        r = sqlite3.connect(db, timeout=5).execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None
    except Exception:
        return None


def _stats(name, group, series, start, note="", key=None):
    """series: [(t, equity)] ascending."""
    if not series or not start:
        return {"name": name, "group": group, "key": key, "start": start, "equity": None, "roi": None, "note": note or "no data yet"}
    now = time.time()
    eq = series[-1][1]
    t0 = series[0][0]
    days = max((now - t0) / 86400, 0.0)

    def ret_since(t):
        base = next((e for tt, e in series if tt >= t), series[0][1])
        prev = [e for tt, e in series if tt < t]
        base = prev[-1] if prev else base
        return eq / base - 1 if base else None
    mo = dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    peak, mdd = series[0][1], 0.0
    for _, e in series:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1) if peak else mdd
    roi = eq / start - 1
    ann = ((eq / start) ** (365.0 / days) - 1) if days >= MIN_DAYS_ANNUAL and eq > 0 else None
    bm = bench()
    pace = (1 + bm["min"]) ** (days / 365.0) - 1 if days > 0 else 0.0           # ROI the book should have by now to be on a minimum-target pace
    if ann is not None:
        status = "strong" if ann >= bm["strong"] else "meets minimum" if ann >= bm["min"] else "below minimum"
    elif days >= 3:
        status = "on pace" if roi >= pace else "behind pace"
    else:
        status = "too early"
    return {"status": status, "pace_min": pace, "name": name, "group": group, "key": key, "start": round(start, 2), "equity": round(eq, 2), "pnl": round(eq - start, 2), "roi": roi, "roi_7d": ret_since(now - 7 * 86400) if days >= 1 else None, "roi_mtd": ret_since(mo),
            "annualised": ann, "max_dd": mdd, "days": round(days, 1), "note": note, "spark": _spark(series, start)}


def _spark(series, start, n=40):
    """Up to n evenly spaced ROI points (equity / start - 1) for the HQ sparkline; the last point is always the latest equity."""
    if not series or not start:
        return []
    step = max(1, len(series) // n)
    pts = series[::step][-(n - 1):] + [series[-1]]
    return [round(e / start - 1, 4) for _, e in pts]


def _eq_series(path, key="eq"):
    return [(r["t"], r[key]) for r in _jl(path) if key in r and r.get("t")]


def collect():
    rows = []
    # Jev Markets
    d = os.path.join(HOME, "jev-markets")
    rows.append(_stats("Jev Markets", "Desks", _eq_series(os.path.join(d, "data/equity.jsonl")), _kv(os.path.join(d, "data/desk.db"), "paper_start_bank") or 30000.0))
    # Jev Perps (renamed from Jev Majors 2026-10-22; folders keep their jev-majors* names) + A/B lanes (Markets invert arm B joins the same group)
    for nm, folder in (("Jev Perps", "jev-majors"), ("Perps lane A (baseline)", "jev-majors-guarded"), ("Perps lane B (invert)", "jev-majors-invert"), ("Markets lane B (invert)", "jev-markets-invert")):
        d = os.path.join(HOME, folder)
        rows.append(_stats(nm, "Desks" if nm == "Jev Perps" else "A/B lanes", _eq_series(os.path.join(d, "data/equity.jsonl")), _kv(os.path.join(d, "data/desk.db"), "paper_start_bank")))
    # Jev Markets swing lane (daily dip-buy long + small downtrend shorts, MNQ/MES/MYM/MGC): A/B lane against the intraday scalper
    d = os.path.join(HOME, "jev-markets")
    sb = _jf(os.path.join(d, "data/swing_book.json"), {}) or {}
    rows.append(_stats("Markets swing lane", "A/B lanes", _eq_series(os.path.join(d, "data/swing_equity.jsonl")), sb.get("start_bank") or 100000.0))
    # Jev Predict
    d = os.path.join(HOME, "jev-predict")
    bk = _jf(os.path.join(d, "data/book.json"), {}) or {}
    ext = bk.get("ext_credit") or 0.0                                   # demo-balance syncs are not trading results: take them out of equity
    pr = _stats("Jev Predict", "Desks", [(t, e - ext) for t, e in _eq_series(os.path.join(d, "data/equity.jsonl"))], bk.get("start_bank"), "excludes non-trading demo credits")
    pr["roi_7d"] = pr["roi_mtd"] = None                                      # window returns would mix pre- and post-sync equity; only since-start ROI is meaningful here
    rows.append(pr)
    # Jev Agents (prediction-market rule agents, paper)
    d = os.path.join(HOME, "jev-agents")
    bk = _jf(os.path.join(d, "data/book.json"), {}) or {}
    rows.append(_stats("Jev Agents", "Desks", _eq_series(os.path.join(d, "data/equity.jsonl")) or ([(time.time(), 10000.0)] if bk else []), bk.get("start_bank") or 10000.0))
    # Jev Coin Desk = the FOMO lane, its own agent since 2026-10-22 (realised P/L only)
    try:
        db = sqlite3.connect(f"file:{os.path.join(HOME, 'jev-coin-desk/data/fomo.db')}?mode=ro", uri=True, timeout=5)
        tr = db.execute("SELECT t_out, pnl FROM lane WHERE t_out IS NOT NULL ORDER BY t_out").fetchall()
        first = db.execute("SELECT MIN(t_in) FROM lane").fetchone()[0]
        eq, ser = 1000.0, [(first or time.time(), 1000.0)] if first else [(time.time(), 1000.0)]   # fresh bank (no trades yet) shows $1,000 at 0%
        for t, p in tr:
            eq += p or 0
            ser.append((t, eq))
        if ser and ser[-1][0] < time.time() - 60:
            ser.append((time.time(), eq))
        rows.append(_stats("Jev Coin Desk", "Desks", ser, 1000.0, "FOMO memecoin lane · realised P/L only (open trades not marked)"))
    except Exception:
        rows.append({"name": "Jev Coin Desk", "group": "Desks", "equity": None, "roi": None, "note": "no data yet"})
    # Jev Options IV lanes
    cfg = _jf(os.path.join(OPT, "config.json"), {}) or {}
    lanes = [("condor", "Condor", "condor_equity.jsonl", "condor_book.json", (cfg.get("condor") or {}).get("bank_usd", 10000)),
             ("cmax", "Condor Max", "condormax_equity.jsonl", "condormax_book.json", (cfg.get("condor_max") or {}).get("bank_usd", 10000))]
    for k, nm in (("pcs", "Put Credit"), ("ccs", "Call Credit"), ("bcd", "Bull Call Debit"), ("ern", "Earnings Strangle")):
        lanes.append((k, nm, f"lane_{k}_equity.jsonl", f"lane_{k}_book.json", ((cfg.get("lanes") or {}).get(k) or {}).get("bank_usd", 10000)))
    lanes.append(("leg", "IV-email debit desk", "equity.jsonl", "book.json", cfg.get("paper_bank_usd", 10000)))
    for key, nm, ef, bf, bank in lanes:
        bk = _jf(os.path.join(OPT, "data", bf), {}) or {}
        ser = _eq_series(os.path.join(OPT, "data", ef)) or [(time.time(), bk.get("start_bank") or bank)]      # no marks yet = flat at the starting bank
        rows.append(_stats(nm, "Options IV", ser, bk.get("start_bank") or bank, key=key))
    order = {"Desks": 0, "A/B lanes": 1, "Options IV": 2}
    return sorted(rows, key=lambda r: order.get(r["group"], 9))                  # stable: keeps the within-group order


def write():
    rows = collect()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "roi.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"t": time.time(), "rows": rows}, open(p + ".tmp", "w"))
    os.replace(p + ".tmp", p)
    return rows


if __name__ == "__main__":
    f = lambda x, p=1: "–" if x is None else f"{100 * x:+.{p}f}%"
    print(f"{'book':26s}{'start':>10s}{'equity':>11s}{'ROI':>9s}{'7d':>8s}{'MTD':>8s}{'annual':>9s}{'maxDD':>8s}{'days':>6s}")
    for r in collect():
        if r.get("equity") is None:
            print(f"{r['name']:26s}  {r.get('note')}")
            continue
        print(f"{r['name']:26s}{r['start']:>10,.0f}{r['equity']:>11,.0f}{f(r['roi']):>9s}{f(r.get('roi_7d')):>8s}{f(r.get('roi_mtd')):>8s}{f(r.get('annualised'),0):>9s}{f(r.get('max_dd')):>8s}{r['days']:>6.1f}")
