---
applyTo: "**/*.ipynb"
---

# Jupyter Notebook Conventions — neuropoly-db

## Cell Structure (in order)
1. **Markdown header**: notebook number, goal statement, prerequisites table, component table
2. **Config cell**: ALL configurable variables — `ES_HOST`, `INDEX_NAME`, model names, hyperparameters
3. **Imports + setup cell**: imports, ES client + `assert client.ping()`, model loading
4. **Logical sections**: each preceded by a markdown cell with a `##` header and explanation
5. **Results/evaluation cell**: metrics table via `display(pd.DataFrame(...))`, summary prints
6. **Summary markdown**: ASCII architecture diagram, findings, link to next notebook

## Code Quality
- Notebooks are numbered 01–07 and **must be run in order** — later notebooks depend on earlier ones
- Put ALL configurable constants in the config cell (cell 2) — never scatter them through the notebook
- Validate external connections early and **fail loudly**: `assert client.ping(), f"Cannot reach ES at {ES_HOST}"`
- Use `tqdm` for any loop over more than 100 items
- Use `display(pd.DataFrame(...))` for tabular results — never `print()` a list of dicts

## ES Connection Pattern
```python
client = Elasticsearch(ES_HOST, request_timeout=120)
assert client.ping(), f"Cannot reach Elasticsearch at {ES_HOST}"
print(f"Connected to ES {client.info()['version']['number']}")
```

## Output Hygiene
- **Clear outputs before committing** (VS Code: "Clear All Outputs")
- Do not store large numpy arrays or model weights in notebook state
- Limit displayed rows to 20–50 — use `.head()` or slicing

## Reproducibility
- Add `os.environ['CUDA_VISIBLE_DEVICES'] = ''` near the top of compute cells (CPU-only project)
- Pin random seeds when benchmarking: `np.random.seed(42)`
- Use `json.dump(..., default=str)` when saving results (handles non-serializable types like numpy floats)

## Naming Consistency
- Results file: `<topic>_results.json` (e.g., `poc1_results.json`)
- Keep variable names consistent across notebooks: `poc1_results`, `GOLD_QUERIES`, `show_hits()`
- Index variable names: `BASE_INDEX = "neuroimaging"`, `POC1_INDEX = "neuroimaging-poc1"`
