---
agent: vector-search
description: >
  Generate a retrieval benchmark evaluation for the neuropoly-db search system,
  measuring P@K, MRR, and latency against the GOLD_QUERIES set.
---

Generate a retrieval benchmark evaluation for the **neuropoly-db** search system.

**What to benchmark:** ${input:description}
**Search function name:** ${input:searchFn|poc1_search}
**Index:** ${input:index|neuroimaging-poc1}

---

## Required Metrics
- **P@3**, **P@5**, **P@10** — Precision at K
- **MRR** — Mean Reciprocal Rank (max_rank=10)
- **Median latency** (ms) — use median, not mean (latency distributions are skewed)
- Optional: `score_spread` (std dev of top-k scores — signals result diversity)

## Gold Queries
Extend or use the standard `GOLD_QUERIES` from notebook 06:
```python
GOLD_QUERIES = [
    {
        "query": "functional BOLD brain activation",
        "label": "BOLD suffix",
        "check": lambda s: s.get("suffix") == "bold"
    },
    # ... add domain-specific queries for the new experiment
]
```
Each `check` function takes an ES `_source` dict and returns `bool`.

## Metric Functions
```python
def precision_at_k(hits: list[dict], check_fn, k: int) -> float:
    top_k = hits[:k]
    return sum(1 for h in top_k if check_fn(h["_source"])) / k if top_k else 0.0

def mean_reciprocal_rank(hits: list[dict], check_fn, max_rank: int = 10) -> float:
    for rank, hit in enumerate(hits[:max_rank], start=1):
        if check_fn(hit["_source"]):
            return 1.0 / rank
    return 0.0
```

## Benchmark Loop + Output
```python
import json, numpy as np
import pandas as pd

results = []
for gq in GOLD_QUERIES:
    hits, meta = search_fn(gq["query"], k=10)
    results.append({
        "label":      gq["label"],
        "query":      gq["query"],
        "latency_ms": meta["latency_ms"],
        "p@3":  precision_at_k(hits, gq["check"], 3),
        "p@5":  precision_at_k(hits, gq["check"], 5),
        "p@10": precision_at_k(hits, gq["check"], 10),
        "mrr":  mean_reciprocal_rank(hits, gq["check"]),
    })

with open(RESULTS_PATH, "w") as f:
    json.dump(results, f, indent=2, default=str)

df = pd.DataFrame([{k: v for k, v in r.items() if k != "hits"} for r in results])
display(df)
print(f"\nMean P@5 : {df['p@5'].mean():.3f}")
print(f"Mean MRR : {df['mrr'].mean():.3f}")
print(f"Median latency: {df['latency_ms'].median():.0f} ms")
```

## Rules
- Always save raw `hits` per query to the results JSON for offline analysis
- Use `default=str` in `json.dump` to handle numpy float32 values
- Compare against `poc1_results.json` in notebook 07 for head-to-head comparisons
