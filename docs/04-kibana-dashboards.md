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
   - Pie chart: document count by `dataset`
   - Stacked bar: scans per dataset, broken down by `suffix`
   - Histogram: distribution of `MagneticFieldStrength` (1.5 T vs 3 T)
   - Data table: per-dataset overview (scan count, subjects, suffixes)
4. **A dashboard** combining all four visualizations with linked cross-filtering.

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

Kibana 9.x uses a **solution-based sidebar** instead of the older flat menu.
When you open `http://localhost:5601` you'll see five top-level sections:

| Sidebar Section | What's Inside |
| --- | --- |
| **Analytics** | Discover, Dashboard, Visualize Library |
| **Elasticsearch** | Indices, Connectors, Playground |
| **Observability** | Logs, APM, Uptime (not used in this course) |
| **Security** | SIEM, Endpoints (not used in this course) |
| **Management** | Dev Tools (Console), Stack Management (Data Views, Index Management) |

Key locations you'll use:

1. **Analytics → Discover** — Browse and filter documents in your index.
2. **Analytics → Dashboard** — Build and view dashboards. Creating a
   visualization from a dashboard opens **Lens** directly.
3. **Management → Dev Tools** — Interactive REST console. Left pane for
   queries, right pane for results. Execute with `Ctrl+Enter`.
4. **Management → Stack Management → Data Views** — Connect Kibana to an
   ES index (you can also create a Data View inline from Discover or Lens).

---

**Next:** Proceed to **[Notebook 4: Kibana Exploration](../notebooks/04-kibana-exploration.ipynb)** after completing Notebooks 1–3.
