"""Local Hugging Face helpers shared by the Jev ecosystem (2026-10-21). CPU only, no API, no keys; models download once into ~/.cache/huggingface
and then run offline. Safetensors weights only (use_safetensors=True: no pickle code from the hub is ever executed).
  embed(texts)            -> L2-normalised sentence vectors (BAAI/bge-small-en-v1.5, 384-d, ~130 MB)
  sim(a, b)               -> cosine similarity of two texts
  zeroshot(text, labels)  -> {label: probability} via NLI (MoritzLaurer/deberta-v3-base-zeroshot-v2.0, ~370 MB)
Used by: jev-career (resume pre-rank + duplicate postings + Gmail stage), zillow-agent (listing remarks), jevopt (semantic reuse, audited).
Language tasks only: the 2026-10 jev-shadow scoreboard showed HF price forecasters (Chronos) lose to a no-change baseline."""
import threading

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
ZS_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
_lk = threading.Lock()
_m = {}


def _torch():
    import torch
    torch.set_num_threads(2)                     # stay light: these run next to the trading agents
    return torch


def _embedder():
    with _lk:
        if "emb" not in _m:
            from transformers import AutoModel, AutoTokenizer
            tok = AutoTokenizer.from_pretrained(EMBED_MODEL)
            mod = AutoModel.from_pretrained(EMBED_MODEL, use_safetensors=True).eval()
            _m["emb"] = (tok, mod)
        return _m["emb"]


def embed(texts, batch=16, max_len=512):
    """bge: CLS pooling + L2 normalise. Returns a list of float lists."""
    torch = _torch()
    tok, mod = _embedder()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok([t or "" for t in texts[i:i + batch]], padding=True, truncation=True, max_length=max_len, return_tensors="pt")
        with torch.no_grad():
            v = mod(**enc).last_hidden_state[:, 0]
        v = torch.nn.functional.normalize(v, dim=-1)
        out.extend(v.tolist())
    return out


def cos(a, b):
    return float(sum(x * y for x, y in zip(a, b)))


def sim(a, b):
    va, vb = embed([a, b])
    return cos(va, vb)


def _zs():
    with _lk:
        if "zs" not in _m:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            tok = AutoTokenizer.from_pretrained(ZS_MODEL)
            mod = AutoModelForSequenceClassification.from_pretrained(ZS_MODEL, use_safetensors=True).eval()
            ent = next(i for i, l in mod.config.id2label.items() if l.lower().startswith("entail"))
            _m["zs"] = (tok, mod, ent)
        return _m["zs"]


def zeroshot(text, labels, template="This text is about {}.", multi=False):
    """NLI zero-shot. multi=False: probabilities over the labels sum to 1. multi=True: each label scored independently (0-1)."""
    torch = _torch()
    tok, mod, ent = _zs()
    premise = (text or "")[:2000]
    hyps = [template.format(l) for l in labels]
    enc = tok([premise] * len(hyps), hyps, padding=True, truncation="only_first", max_length=512, return_tensors="pt")
    with torch.no_grad():
        logits = mod(**enc).logits
    if multi:
        p = torch.softmax(logits, dim=-1)[:, ent]
    else:
        p = torch.softmax(logits[:, ent], dim=0)
    return {l: round(float(x), 4) for l, x in zip(labels, p)}


if __name__ == "__main__":
    import time
    t = time.time()
    print("sim", round(sim("Senior ML engineer, LLM agents, Python", "Machine learning engineer building AI agents"), 3), f"{time.time() - t:.1f}s")
    t = time.time()
    print(zeroshot("Thank you for applying. Unfortunately we have decided to move forward with other candidates.", ["a rejection", "an interview invitation", "an application confirmation", "a job offer"]), f"{time.time() - t:.1f}s")
