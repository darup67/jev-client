#!/usr/bin/env python3
"""Jev error-log monitor (2026-10-29), called by supervisor.py every 60 s. Reads the NEW lines of every agent log (jev-*/*.err.log, market-lab/ops, zillow-agent), finds Python tracebacks and
logged ERROR / CRITICAL lines, groups them into signatures (agent + exception + normalised message + the failing file:function) and keeps data/errmon.json.

REPORT   a new signature that repeats (>= 3 in 10 min) -> one email naming agent, count, failing line, the traceback and a ready-to-paste fix request for Claude. Daily digest 17:30 of what is still open.
RAPID FIX  known-safe remedies the supervisor can apply by itself, rate-limited by supervisor.kick() (3 per hour per agent):
             * 'Too many open files' / Errno 24    -> restart the agent (a leak wedges it until restarted; fdguard also watches the trend)
             * a traceback repeating >= 5 in 10 min inside a KeepAlive agent -> one restart (clears a wedged connection / stale state); if the SAME signature is back within 15 min of the restart
               it is a code bug: the email says 'needs a code fix', not 'restarted'.
           External noise (HTTP 5xx, timeouts, connection resets) is counted but never restarts or emails unless it floods (>= 40 in 10 min).
Code fixes are never auto-applied: the email carries the file:line and a fix request, and data/errmon.json lists every open signature so a Claude session can pick them up.
  errmon.py --scan   report what the last 300 KB of each log contains (no state change, no actions)"""
import glob, json, os, re, sys, time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "errmon.json")
GLOBS = [os.path.join(HOME, "jev-*", "*.err.log"), os.path.join(HOME, "jev-client", "*.err.log"), os.path.join(HOME, "market-lab", "ops", "*.err.log"), os.path.join(HOME, "zillow-agent", "*.err.log")]
PREFIX = {"jev-coin-desk": "jevcoindesk", "jev-markets": "jevmarkets", "jev-markets-invert": "jevmarketsinvert", "jev-majors": "jevmajors", "jev-predict": "jevpredict", "jev-options-iv": "jevoptionsiv",
          "jev-agents": "jevagents", "jev-career": "jevcareer", "jev-home": "jevhome", "jev-shop": "jevshop"}
EXTERNAL = re.compile(r"BrokenPipe|Errno 32|Errno 54|HTTP 5\d\d|HTTPError: 5\d\d|ReadTimeout|ConnectTimeout|ConnectionReset|RemoteDisconnected|Max retries exceeded|Read timed out|Temporary failure|timed out|SSLError|ConnectionError|520|502|503|504|429", re.I)
FDLEAK = re.compile(r"Too many open files|Errno 24")
LOGERR = re.compile(r"\b(ERROR|CRITICAL)\b")
WINDOW = 600


def norm(msg):
    msg = re.sub(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{16,}", "H", msg)
    msg = re.sub(r"\d+(\.\d+)?", "N", msg)
    return re.sub(r"\s+", " ", msg)[:140]


def agent_of(path):
    d = os.path.basename(os.path.dirname(path))
    return d, os.path.basename(path).replace(".err.log", ""), PREFIX.get(d)


def parse(chunk):
    """Yield (kind, where, type, message, text) for each traceback / logged error in a block of log lines."""
    lines = chunk.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("Traceback (most recent call last)"):
            j, where = i + 1, "?"
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or lines[j].startswith("Traceback")):
                m = re.match(r'\s+File "([^"]+)", line (\d+), in (\S+)', lines[j])
                if m:
                    where = f"{os.path.basename(m.group(1))}:{m.group(2)} {m.group(3)}()"
                j += 1
            exc = lines[j] if j < len(lines) else lines[-1]
            t, _, msg = exc.partition(":")
            yield ("traceback", where, t.strip(), msg.strip(), "\n".join(lines[i:j + 1])[-1800:])
            i = j + 1
            continue
        if LOGERR.search(ln) and "Traceback" not in ln:
            yield ("log", "log", "ERROR", ln.strip()[:200], ln.strip()[:400])
        i += 1


def scan(st, kick=None, job=None, notify=None, labels=(), act=True, backfill=False):
    """One pass. `st` is the supervisor's persistent dict (we use st['errmon']). Returns a short status dict."""
    e = st.setdefault("errmon", {"pos": {}, "sigs": {}, "restarts": {}, "last_digest": ""})
    now, new_sigs = time.time(), 0
    for g in GLOBS:
        for path in glob.glob(g):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            pos = e["pos"].get(path)
            if pos is None:
                pos = max(0, size - 300000) if backfill else size                     # first sight: start at the end (no backfill flood)
            if size < pos:
                pos = 0
            if size == pos:
                e["pos"][path] = pos
                continue
            with open(path, "rb") as fh:
                fh.seek(pos)
                chunk = fh.read(2_000_000).decode("utf-8", "ignore")
            e["pos"][path] = pos + len(chunk.encode("utf-8", "ignore")) if size - pos <= 2_000_000 else size
            repo, jobname, pfx = agent_of(path)
            label = f"com.dhruv.{pfx}.{jobname}" if pfx else None
            for kind, where, typ, msg, text in parse(chunk):
                key = f"{repo}/{jobname}|{typ}|{norm(msg)}|{where}"
                s = e["sigs"].get(key)
                if not s:
                    s = e["sigs"][key] = {"agent": f"{repo}/{jobname}", "where": where, "type": typ, "msg": msg[:200], "sample": text, "first": now, "n": 0, "ts": [], "label": label,
                                          "external": bool(EXTERNAL.search(typ + " " + msg)), "emailed": 0}
                    new_sigs += 1
                s["n"] += 1
                s["last"] = now
                s["ts"] = [t for t in s["ts"] if now - t < WINDOW] + [now]
    # ---- decide: report / rapid fix ----
    for key, s in list(e["sigs"].items()):
        if now - s.get("last", 0) > 24 * 3600:
            del e["sigs"][key]
            continue
        recent = len([t for t in s["ts"] if now - t < WINDOW])
        label = s.get("label")
        if backfill or not act:
            continue
        fd = bool(FDLEAK.search(s["type"] + " " + s["msg"]))
        if s["external"] and not fd:
            if recent >= 40 and now - s.get("emailed", 0) > 6 * 3600:
                s["emailed"] = now
                notify and notify("errmon-ext-" + s["agent"], f"{s['agent']}: external errors flooding ({recent} in 10 min)", f"{s['type']}: {s['msg']}\n\nUsually a provider outage; nothing was restarted.", 6)
            continue
        fixed = ""
        if label and label in labels and kick and recent >= (1 if fd else 5):
            last_r = e["restarts"].get(key, 0)
            if now - last_r > 900 or fd and now - last_r > 300:
                if kick(label, "errmon: " + ("file descriptors exhausted" if fd else f"traceback repeating {recent}x")):
                    e["restarts"][key] = now
                    s["restarted"] = now
                    fixed = "restarted the agent"
            elif now - last_r <= 900:
                s["needs_code_fix"] = True                                           # same signature again after the restart: a real bug
        if recent >= 3 and (now - s.get("emailed", 0) > 6 * 3600 or (s.get("needs_code_fix") and not s.get("fix_emailed"))):
            s["emailed"] = now
            s["fix_emailed"] = bool(s.get("needs_code_fix"))
            verdict = "NEEDS A CODE FIX (it came back after a restart)" if s.get("needs_code_fix") else (fixed or "no automatic fix applied")
            ask = f"Claude: fix the {s['type']} in {s['agent']} at {s['where']} (seen {s['n']}x). Traceback is in ~/jev-client/data/errmon.json under this signature."
            notify and notify("errmon-" + key[:90] + ("-fix" if s.get("needs_code_fix") else ""), f"{s['agent']}: {s['type']} x{recent} in 10 min ({verdict.split(' (')[0]})",
                              f"{s['where']}\n{s['type']}: {s['msg']}\n\nSupervisor: {verdict}\n\nFix request to paste:\n{ask}\n\n{s['sample']}", 6)
    open_ = [(k, s) for k, s in e["sigs"].items() if not s["external"] and now - s.get("last", 0) < 3600]
    lt = time.localtime()
    if act and notify and (lt.tm_hour, lt.tm_min) >= (17, 30) and e.get("last_digest") != time.strftime("%Y-%m-%d"):
        e["last_digest"] = time.strftime("%Y-%m-%d")
        rows = sorted(((s["n"], s) for s in e["sigs"].values() if not s["external"]), key=lambda x: -x[0])[:12]
        if rows:
            notify("errmon-digest-" + time.strftime("%Y-%m-%d"), f"Error digest: {len(rows)} open signature(s)", "\n".join(f"{n:5d}x  {s['agent']}  {s['type']}: {s['msg'][:80]}  @ {s['where']}" for n, s in rows), 20)
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT + ".tmp", "w") as f:
            json.dump({"t": now, "open": [{"agent": s["agent"], "where": s["where"], "type": s["type"], "msg": s["msg"], "n": s["n"], "last": s.get("last"), "external": s["external"],
                                            "restarted": s.get("restarted"), "needs_code_fix": s.get("needs_code_fix", False), "sample": s["sample"]} for s in e["sigs"].values()]}, f)
        os.replace(OUT + ".tmp", OUT)
    except OSError:
        pass
    return {"signatures": len(e["sigs"]), "new": new_sigs, "active_1h": len(open_)}


if __name__ == "__main__" and "--scan" in sys.argv:
    st = {}
    r = scan(st, backfill=True, act=False)
    print(r)
    for s in sorted(st["errmon"]["sigs"].values(), key=lambda s: -s["n"])[:20]:
        print(f"{s['n']:5d}x {'EXT ' if s['external'] else '    '}{s['agent']:32s} {s['type']}: {s['msg'][:70]} @ {s['where']}")
