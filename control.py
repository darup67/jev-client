"""Pause / flatten / resume flags for the Jev desks (stdlib only). One small file per desk: <desk>/data/control.json.
  {"paused": {"since": epoch, "until": epoch|null, "by": "phone"} | null, "flatten": {"at": epoch, "by": "phone"} | null}
The desk reads it (cached 3 s) and: pauses = opens NOTHING new (exits and position management carry on); flatten = closes every open paper position once, then stays paused.
A timed pause lifts itself when `until` passes. Everything is paper trading: nothing here can touch real money. Every change is appended to ~/jev-overview/data/audit.jsonl."""
import json, os, time

AUDIT = os.path.expanduser("~/jev-overview/data/audit.jsonl")
_cache = {}


def _path(desk_dir):
    return os.path.join(desk_dir, "data", "control.json")


def _read(desk_dir):
    try:
        return json.load(open(_path(desk_dir)))
    except (OSError, ValueError):
        return {"paused": None, "flatten": None}


def _write(desk_dir, d):
    p = _path(desk_dir)
    json.dump(d, open(p + ".tmp", "w"))
    os.replace(p + ".tmp", p)
    _cache.pop(desk_dir, None)


def audit(desk, action, detail=""):
    try:
        os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
        with open(AUDIT, "a") as f:
            f.write(json.dumps({"t": time.time(), "desk": desk, "action": action, "detail": detail}) + "\n")
    except OSError:
        pass


def state(desk_dir):
    """Current control state; a timed pause that has expired is cleared here."""
    hit = _cache.get(desk_dir)
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    d = _read(desk_dir)
    p = d.get("paused")
    if p and p.get("until") and p["until"] <= time.time():
        d["paused"] = None
        _write(desk_dir, d)
        audit(os.path.basename(desk_dir), "auto-resume", "the timed pause ended")
    _cache[desk_dir] = (time.time(), d)
    return d


def is_paused(desk_dir):
    p = state(desk_dir).get("paused")
    return p


def pause(desk_dir, hours=None, by="phone"):
    d = _read(desk_dir)
    now = time.time()
    d["paused"] = {"since": now, "until": now + hours * 3600 if hours else None, "by": by}
    _write(desk_dir, d)
    audit(os.path.basename(desk_dir), "pause", f"{hours} h" if hours else "until resumed")


def resume(desk_dir, by="phone"):
    d = _read(desk_dir)
    d["paused"], d["flatten"] = None, None
    _write(desk_dir, d)
    audit(os.path.basename(desk_dir), "resume", "")


def flatten(desk_dir, by="phone"):
    """Pause (until resumed) and ask the desk to close every open paper position once."""
    d = _read(desk_dir)
    now = time.time()
    d["paused"] = {"since": now, "until": None, "by": by}
    d["flatten"] = {"at": now, "by": by}
    _write(desk_dir, d)
    audit(os.path.basename(desk_dir), "flatten+pause", "")


def flatten_requested(desk_dir):
    return state(desk_dir).get("flatten")


def flatten_done(desk_dir, closed):
    d = _read(desk_dir)
    d["flatten"] = None
    _write(desk_dir, d)
    audit(os.path.basename(desk_dir), "flatten-done", f"{closed} position(s) closed")


def describe(desk_dir):
    d = state(desk_dir)
    p, f = d.get("paused"), d.get("flatten")
    if not p:
        return None
    t = time.strftime("%-I:%M %p", time.localtime(p["since"]))
    s = f"PAUSED by you since {t}" + (f", resumes {time.strftime('%-I:%M %p', time.localtime(p['until']))}" if p.get("until") else ", until you resume")
    return s + (" (flatten pending)" if f else "")
