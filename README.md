# jev-client

One shared client for **Jev** (TypeSafe's System One model), used by the local
agents. Zero dependencies: `jev.py` (stdlib) for Python agents, `jev.js`
(Node core) for Node agents. Same contract in both.

- `ask(state, questions, caller)` returns `{model, answers, usage}` or **None/null
  on any failure**, and never throws. No key, a timeout or an outage all mean
  the agent carries on without Jev.
- Retries 429/529/5xx with backoff (3 tries, 10s timeout each).
- Model pinned to `jev-1.13.0`, not `jev-latest`, so a silent alias move can't
  mix two models into one shadow trial. Bump it deliberately.
- Every call is logged to `calls.jsonl`: caller, model, latency, tokens, error.
  The state is **not** logged here; each agent keeps its own record.

## Key

Kept in the Keychain, never on disk:

```bash
security add-generic-password -a "$USER" -s typesafe-jev -w '<key from console.typesafe.ai/keys>'
```

`$TYPESAFE_API_KEY` overrides it. The Python client looks again every 5 min
while the key is missing, so the always-on coin watcher picks it up without a restart.

## Commands

```bash
python3 ~/jev-client/jev.py --ping    # one tiny call: key + endpoint check
python3 ~/jev-client/jev.py --usage   # calls / failures / tokens / cost per caller per day
```

## Who uses it (all shadow mode since 2026-09-24)

| Agent | What Jev reads | Where answers go | Review |
|---|---|---|---|
| `~/zillow-agent` | listing remarks (fetched per listing) | tags in `--dry-run` preview; `jev-tags.jsonl` | eyeball tags vs remarks; set `jev.showInEmail` true when they hold up |
| `~/coin-launch-agent` | coin name, ticker, description, links | `data/jev.jsonl` + 60-min outcomes | `python jev_shadow.py` report |

Not used, on purpose: kalshi-btc-agent, market-lab, flip-notifier, pine-alerts,
asset-agents, and anything touching Robinhood orders. Jev judges text; those
are numeric or rule-based. See the rejection note in `~/kalshi-btc-agent/README.md`.

Jev's own caveats (jev-1.13 jaggedness doc): it reads literally, it's poor at
numbers, dates and counting (keep those in code), and it can be steered by
adversarial text in the state. Coin descriptions are exactly that kind of text,
so treat coin answers as a feature to measure, not a verdict.

## Token optimizer (2026-10-14)
`jevopt.py` + `policy.json` (hot-reloaded; `optimizer.enabled=false` restores the old behavior). Shared sqlite answer cache across all processes/lanes, quantized reuse keys (noisy fields ignored/bucketed per caller), single-flight (no double paying for the same state), audited reuse (a few % of hits re-asked fresh; divergence logged to audit.jsonl and auto-tunes TTL per caller), spend governor (projects month-end from the last 72 h; over plan -> low-priority callers cache-only, then high-priority TTL x2). `python3 jev.py --report` shows spend by caller, hit rate, divergence, TTL scales, projection.
