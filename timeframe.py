"""Dynamic timeframe gauge, shared by the Jev desks (stdlib only). Given recent 1-minute bars and the contract's round-trip cost it scores each candidate chart
(1, 5, 15, 30 and 60 minutes) and says which one to trade on right now, or that nothing is worth trading.

For every timeframe it measures, on the last few days of bars:
  atr          the average bar range (12 bars), in price points
  cost_drag    round-trip cost / (1 ATR stop): the share of a 1R trade that fees and slippage eat. Faster charts pay far more of it.
  gross_R      what a plain momentum rule (the desk's own trigger and exits, one position at a time) earned on that chart, BEFORE costs, in R, and how many trades that was
  net_est      (n * gross_R + K * prior) / (n + K) - cost_drag. The prior is 0R (no edge unless the recent data shows one), so a chart only scores above its cost drag when its recent trades
               really earned something; with few trades the score is just minus its cost drag
  er, ac1      Kaufman efficiency ratio and lag-1 autocorrelation of the bar returns (trendiness), reported for context, not used in the score
The chosen chart is the best net_est; if even that is below `min_score` (default -0.04R) the gauge says to STAND DOWN. A switch needs the new chart to beat the current one by `margin`
(0.02R) or the current dwell to exceed `min_dwell` minutes, so it does not flip every minute. The daily chart is reported (ATR, 200-day trend) but is a bias, not a tradable timeframe for
the intraday engine. Nothing here places orders."""
import math, statistics, time

TFS = (1, 5, 15, 30, 60)
K_PRIOR = 30


def resample(bars, tf, offset_min=0):
    """1-minute bars [t_epoch, o, h, l, c, v] -> tf-minute bars, aligned to the clock (offset_min shifts the grid, e.g. 30 for a 09:30 open)."""
    if tf <= 1:
        return [list(b) for b in bars]
    out, cur, cur_k = [], None, None
    for t, o, h, l, c, v in bars:
        k = int((t // 60 - offset_min) // tf)
        if k != cur_k:
            if cur:
                out.append(cur)
            cur, cur_k = [t, o, h, l, c, v], k
        else:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], l), c, cur[5] + v
    if cur:
        out.append(cur)
    return out


def atr_series(bars, n=12):
    tr = [max(bars[i][2] - bars[i][3], abs(bars[i][2] - bars[i - 1][4]), abs(bars[i][3] - bars[i - 1][4])) for i in range(1, len(bars))]
    return [None] * n + [sum(tr[i - n + 1:i + 1]) / n for i in range(n - 1, len(tr))]


def ema(vals, n):
    k, e, out = 2 / (n + 1), vals[0], []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def simulate(bars, cost_pts, flat_after=None, day_of=None, trig=0.5, stop=1.0, tp=1.5, stall_bars=2.4, hold_bars=8, confirm=True):
    """The desk's momentum rule on tf bars: move over the last 2 bars >= `trig` ATR, with the move, on the right side of the day's VWAP, EMA 9/21 agreeing, not stretched.
    Entry next bar open, stop 1 ATR, target 1.5R, stall (no 0.4R after stall_bars), max hold hold_bars, one position at a time. Returns [(bar_index, gross_R, atr_at_entry)] (costs NOT included)."""
    n = len(bars)
    if n < 40:
        return []
    atr = atr_series(bars)
    closes = [b[4] for b in bars]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    out, pos, entry, risk, ent_i, peak = [], 0, 0.0, 0.0, 0, 0.0
    cum_pv = cum_v = 0.0
    day = None
    for i in range(n - 1):
        t, o, h, l, c, v = bars[i]
        d = day_of(t) if day_of else None
        if d != day:
            day, cum_pv, cum_v = d, 0.0, 0.0
        cum_pv += (h + l + c) / 3 * (v or 1.0)
        cum_v += (v or 1.0)
        if pos:
            r_fav = pos * ((h if pos > 0 else l) - entry) / risk
            r_adv = pos * ((l if pos > 0 else h) - entry) / risk
            done = None
            if r_adv <= -stop:
                done = -stop
            elif r_fav >= tp:
                done = tp
            else:
                peak = max(peak, r_fav)
                held = i - ent_i + 1
                if (held >= stall_bars and peak < 0.4) or held >= hold_bars or (flat_after and flat_after(t)) or (day_of and day_of(bars[i + 1][0]) != d):
                    done = pos * (c - entry) / risk
            if done is not None:
                out.append((i, done, risk))
                pos = 0
        if not pos and atr[i] and i >= 24 and (flat_after is None or not flat_after(t)):
            a = atr[i]
            mv = c - closes[i - 2]
            if abs(mv) >= trig * a:
                side = 1 if mv > 0 else -1
                vwap = cum_pv / cum_v if cum_v else c
                ok = True
                if confirm:
                    ok = side * (c - vwap) > 0 and ((e9[i] > e21[i]) == (side > 0)) and abs(c - vwap) < 2.5 * a
                if ok and (day_of is None or day_of(bars[i + 1][0]) == d):
                    pos, entry, risk, ent_i, peak = side, bars[i + 1][1], a, i + 1, 0.0
    return out


def efficiency(closes, n=20):
    if len(closes) <= n:
        return None
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    return abs(closes[-1] - closes[-n - 1]) / path if path else None


def autocorr(closes):
    r = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    if len(r) < 30:
        return None
    a, b = r[:-1], r[1:]
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else None


def gauge(bars_1m, cost_pts, offset_min=0, day_of=None, flat_after=None, tfs=TFS, daily=None):
    """Score every timeframe from the recent 1-minute bars. Returns {"tfs": {tf: {...}}, "daily": {...} or None}."""
    res = {}
    for tf in tfs:
        bars = resample(bars_1m, tf, offset_min)
        if len(bars) < 60:
            res[tf] = {"ok": False, "why": "not enough bars"}
            continue
        a = atr_series(bars)[-1]
        if not a:
            res[tf] = {"ok": False, "why": "no ATR"}
            continue
        trades = [r for _, r, _a in simulate(bars, cost_pts, flat_after, day_of)]
        n = len(trades)
        gross = statistics.mean(trades) if n else 0.0
        drag = cost_pts / a
        closes = [b[4] for b in bars]
        res[tf] = {"ok": True, "atr": round(a, 3), "cost_drag_R": round(drag, 4), "n": n, "gross_R": round(gross, 4), "net_est": round((n * gross) / (n + K_PRIOR) - drag, 4),
                   "er": None if efficiency(closes) is None else round(efficiency(closes), 3), "ac1": None if autocorr(closes[-200:]) is None else round(autocorr(closes[-200:]), 3)}
    out = {"tfs": res, "daily": None}
    if daily and len(daily) >= 210:
        c = [b[4] for b in daily]
        sma200 = sum(c[-200:]) / 200
        a = atr_series(daily)[-1]
        out["daily"] = {"atr": round(a, 2), "atr_pct": round(a / c[-1], 4), "above_sma200": c[-1] > sma200, "dist_sma200_pct": round(c[-1] / sma200 - 1, 4), "cost_drag_R": round(cost_pts / a, 4)}
    return out


def choose(g, prev=None, now=None, min_score=-0.04, margin=0.02, min_dwell=30):
    """Pick the chart. prev = {"tf": int|None, "since": epoch}. Returns {"tf": int or None (stand down), "since", "scores", "why"}."""
    now = now or time.time()
    scores = {tf: v["net_est"] for tf, v in g["tfs"].items() if v.get("ok")}
    if not scores:
        return {"tf": None, "since": now, "scores": {}, "why": "no usable data"}
    best = max(scores, key=scores.get)
    want = best if scores[best] >= min_score else None
    cur = (prev or {}).get("tf")
    since = (prev or {}).get("since", now)
    if prev and cur is not None and cur in scores and want != cur:
        stay = scores[cur] >= min_score and (want is None or scores[want] - scores[cur] < margin) and now - since < min_dwell * 60 * 3
        if stay or (now - since < min_dwell * 60 and scores[cur] >= min_score):
            want = cur
    if want != cur:
        since = now
    why = (f"{want}m scores {scores[want]:+.3f}R net (cost drag {g['tfs'][want]['cost_drag_R']:.3f}R, {g['tfs'][want]['n']} recent trades, gross {g['tfs'][want]['gross_R']:+.3f}R)" if want
           else f"stand down: the best chart ({best}m) scores {scores[best]:+.3f}R, under the {min_score:+.2f}R floor")
    return {"tf": want, "since": since, "scores": scores, "why": why}
