'use strict';
/**
 * Shared Jev client (TypeSafe System One) for the local Node agents. Node core only.
 * Same contract as jev.py next to it: ask() never throws, returns null on any
 * failure, and logs every call (not the state) to calls.jsonl.
 *
 *   const jev = require(require('os').homedir() + '/jev-client/jev.js');
 *   const res = await jev.ask(state, questions, { caller: 'zillow' });  // object or null
 *
 * Key: $TYPESAFE_API_KEY, else Keychain service "typesafe-jev".
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const LOG = path.join(__dirname, 'calls.jsonl');
const URL = 'https://api.typesafe.ai/v1/systemone';
const MODEL = 'jev-1.13.0';   // pinned; see jev.py
const RETRY = new Set([429, 500, 502, 503, 504, 529]);

let key = null, warned = false;

function apiKey() {
  if (key === null) {
    key = process.env.TYPESAFE_API_KEY || '';
    if (!key) {
      try {
        key = execFileSync('security', ['find-generic-password', '-s', 'typesafe-jev', '-w'],
          { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10000 }).trim();
      } catch { key = ''; }
    }
  }
  return key;
}

function logCall(rec) {
  try { fs.appendFileSync(LOG, JSON.stringify(rec) + '\n'); } catch {}
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function ask(state, questions, { caller = '?', model = MODEL, timeoutMs = 10000, tries = 3 } = {}) {
  const k = apiKey();
  if (!k) {
    if (!warned) { console.error('jev: no API key (Keychain service typesafe-jev); skipping'); warned = true; }
    return null;
  }
  const body = JSON.stringify({ state, model, questions });
  const t0 = Date.now();
  let err = null;
  for (let i = 0; i < tries; i++) {
    let wait = 1000 * 2 ** i;
    try {
      const r = await fetch(URL, {
        method: 'POST', body, signal: AbortSignal.timeout(timeoutMs),
        headers: { Authorization: `Bearer ${k}`, 'Content-Type': 'application/json' },
      });
      if (r.ok) {
        const res = await r.json();
        const u = res.usage || {};
        logCall({ t: Date.now() / 1000, caller, model: res.model, ok: true, ms: Date.now() - t0,
                  in: u.input_tokens, out: u.output_tokens, q: Object.keys(questions).sort() });
        return res;
      }
      err = `HTTP ${r.status} ${(await r.text()).slice(0, 300)}`;
      if (!RETRY.has(r.status)) break;
      const ra = parseFloat(r.headers.get('retry-after'));
      if (Number.isFinite(ra)) wait = ra * 1000;
    } catch (e) {
      err = String(e && e.message || e);
    }
    if (i < tries - 1) await sleep(Math.min(wait, 10000));
  }
  logCall({ t: Date.now() / 1000, caller, model, ok: false, ms: Date.now() - t0, err, q: Object.keys(questions).sort() });
  console.error(`jev: call failed (${caller}): ${err}`);
  return null;
}

module.exports = { ask, apiKey, MODEL };
