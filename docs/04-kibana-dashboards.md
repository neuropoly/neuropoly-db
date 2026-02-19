# Chapter 4 — Kibana Dashboards

*Reading time: ~5 minutes — the hands-on work is in Notebook 4.*

---

## What Is Kibana?

Kibana is the visualization and management UI for the Elastic Stack. It connects
to ElasticSearch on port 5601 and provides:

- **Discover** — Explore and filter documents interactively, view individual
  records, save searches.
- **Dashboard** — Combine multiple visualizations into a single interactive
  view.
- **Lens** — Drag-and-drop visualization builder (bar, line, pie, heatmap,
  data tables, etc.).
- **Dev Tools Console** — Interactive REST API console for running ES queries
  directly. Essential for learning and debugging.

**Kibana and ES must be the same version** (both 9.3.0 in our setup).

---

## What We'll Build

In Notebook 4, you'll create:

1. **A Data View** — Tells Kibana which ES index to explore (`neuroimaging`).
2. **Discover exploration** — Browse indexed scans, add columns, apply filters.
3. **Four visualizations using Lens:**
   - Pie chart: document count by scan type (`suffix`)
   - Horizontal bar: count by `Manufacturer`
   - Histogram: distribution of `MagneticFieldStrength` values
   - Data table: subjects ranked by number of scans
4. **A dashboard** combining all four visualizations with a summary text panel.

---

## Kibana and Vector Search

Kibana's Dev Tools Console lets you run any ES query, including `knn` vector
searches. However, the visual Discover and Dashboard tools are built around
structured/keyword queries and aggregations — they don't have a native
"vector similarity" widget.

In practice, the workflow is:

- Use **Python** (or the Dev Tools Console) for vector and hybrid search queries.
- Use **Kibana dashboards** for aggregate exploration and data quality overview.
- Use **Discover** for drilling into individual documents returned by search.

This division maps naturally to the neuroimaging use case: dashboards give you a
bird's-eye view of your dataset (how many scans per site? per modality? what
field strengths?), while Python scripts handle the sophisticated query logic.

---

## Quick Kibana Orientation

When you open `http://localhost:5601`:

1. **Left sidebar** — Navigation menu. Key items:
   - **Discover** (magnifying glass icon)
   - **Dashboard** (grid icon)
   - **Dev Tools** (wrench icon)
   - **Stack Management** (gear at the bottom)
2. **Stack Management → Data Views** — Where you connect Kibana to your ES
   index.
3. **Dev Tools → Console** — Left pane is for queries, right pane shows results.
   Use `Ctrl+Enter` to execute.

---

**Next:** Proceed to **[Notebook 4: Kibana Exploration](../notebooks/04-kibana-exploration.ipynb)** after completing Notebooks 1–3.
