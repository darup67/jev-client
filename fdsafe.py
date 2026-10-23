"""In-process file-descriptor safety for any dashboard (2026-10-23, after the Coin Desk dashboard ran out of open files and could not even serve its page).
One line in a dashboard:     import fdsafe; fdsafe.protect(Handler, "coindesk")     (after the handler class, before serve_forever)
  * raises the process soft limit to 4096 even if its launchd plist does not;
  * closes every connection made through fdsafe.connect() at the end of each request (a thread-local tracker), so a handler that forgets .close() cannot leak;
  * warns in the log when one request leaves more fds open than it found; and exits the process (launchd KeepAlive restarts it clean) above 90 % of the limit, as a last resort.
Use fdsafe.connect(...) instead of sqlite3.connect(...) inside request handlers."""
import gc, os, resource, sqlite3, sys, threading, time

_TL = threading.local()
EXIT_PCT = 0.90


def raise_limit(want=4096):
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        new = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft < new:
            resource.setrlimit(resource.RLIMIT_NOFILE, (new, hard))
    except (ValueError, OSError):
        pass


def fd_count():
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return -1


def limit():
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def connect(*a, **k):
    """sqlite3.connect that is closed automatically at the end of the current request (only inside protect()ed handlers; elsewhere it behaves like sqlite3.connect)."""
    c = sqlite3.connect(*a, **k)
    if getattr(_TL, "on", False):
        _TL.conns.append(c)
    return c


def _close_all():
    for c in getattr(_TL, "conns", []):
        try:
            c.close()
        except Exception:
            pass
    _TL.conns = []


_ORIG_CONNECT = sqlite3.connect


def _tracked_connect(*a, **k):
    c = _ORIG_CONNECT(*a, **k)
    if getattr(_TL, "on", False):
        _TL.conns.append(c)
    return c


def protect(handler_cls, name="dashboard"):
    """Also patches sqlite3.connect (once) so connections opened by plain sqlite3.connect() INSIDE a request are closed when that request ends. Safe for these dashboards: none keeps a long-lived
    connection created during a request (audited 2026-10-23); connections made outside requests (startup, background threads) are never tracked."""
    raise_limit()
    if sqlite3.connect is not _tracked_connect:
        sqlite3.connect = _tracked_connect
    for meth in ("do_GET", "do_POST", "do_PUT", "do_DELETE"):
        orig = getattr(handler_cls, meth, None)
        if orig is None or getattr(orig, "_fdsafe", False):
            continue

        def wrapped(self, _o=orig):
            _TL.on, _TL.conns = True, []
            before = fd_count()
            try:
                if before > 0 and before >= EXIT_PCT * limit():
                    print(time.strftime("%H:%M:%S"), f"[fdsafe:{name}] {before}/{limit()} open files: exiting so launchd restarts a clean process", file=sys.stderr, flush=True)
                    os._exit(75)
                return _o(self)
            finally:
                _close_all()
                _TL.on = False
                if before >= 0 and fd_count() >= 0.4 * limit():
                    gc.collect()                                  # universal safety net: connections kept alive only by reference cycles are closed by a collection
                after = fd_count()
                if before >= 0 and after - before >= 3:
                    print(time.strftime("%H:%M:%S"), f"[fdsafe:{name}] request left {after - before} extra fds open ({before} -> {after}) on {getattr(self, 'path', '?')}: a handler is leaking", file=sys.stderr, flush=True)
        wrapped._fdsafe = True
        setattr(handler_cls, meth, wrapped)
    return handler_cls
