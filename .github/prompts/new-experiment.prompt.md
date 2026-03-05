---
agent: ask
description: >
  Scaffold a new Jupyter experiment notebook for the neuropoly-db project,
  following the project's notebook structure and ES/encoder conventions.
---

Create a new Jupyter experiment notebook for the **neuropoly-db** project.

**Experiment goal:** ${input:goal}
**Notebook number:** ${input:number} (e.g., `08`)
**New index name (if any):** ${input:indexName|neuroimaging-poc1}

---

## Required Cell Structure (in order)

### Cell 1 — Markdown header
```markdown
# PoC <N> — <Title>
**Goal:** <one-sentence description>
| Component | Implementation | License |
|-----------|---------------|---------|
| ...       | ...           | ...     |
**Prerequisite:** Notebook 0N has been run → `<previous-index>` index exists.
```

### Cell 2 — Configuration
```python
import os
ES_HOST    = os.environ.get("ES_HOST", "http://localhost:9200")
BASE_INDEX = "neuroimaging"
NEW_INDEX  = "<indexName>"
ENCODER_MODEL = "all-mpnet-base-v2"
EMBEDDING_DIMS = 768
TOP_K = 5
RESULTS_PATH = "poc<N>_results.json"
```

### Cell 3 — Imports + setup
```python
import json, time, warnings
import numpy as np
import pandas as pd
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from elasticsearch import Elasticsearch, helpers
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=UserWarning)

client = Elasticsearch(ES_HOST, request_timeout=120)
assert client.ping(), f"Cannot reach Elasticsearch at {ES_HOST}"
print(f"Connected to ES {client.info()['version']['number']}")

model = SentenceTransformer(ENCODER_MODEL, device='cpu')
print(f"Encoder ready. dims={model.get_sentence_embedding_dimension()}")
```

### Subsequent cells — one section per logical step (markdown header + code)

### Penultimate cell — Benchmark
Use `GOLD_QUERIES` from notebook 06 with `precision_at_k` and `mean_reciprocal_rank`.
Save results to `RESULTS_PATH` via `json.dump(..., default=str)`.

### Final cell — Markdown summary
ASCII architecture diagram + findings + "Next step: Open Notebook 0N+1 to..."

## Conventions
- Always `assert client.ping()` — fail loudly if ES is unreachable
- Use `display(pd.DataFrame(...))` for tabular outputs, not `print()`
- Use `tqdm` for any loop over more than 100 items
- Keep variable names consistent with previous notebooks: `GOLD_QUERIES`, `show_hits()`, `rrf_fuse()`
