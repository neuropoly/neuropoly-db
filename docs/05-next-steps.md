# Chapter 5 — Next Steps

*Reading time: ~5 minutes*

---

## What You've Learned

You've built a complete pipeline:

```
BIDS dataset → pybids metadata extraction → embedding generation
    → ElasticSearch bulk indexing → keyword/vector/hybrid search
    → Kibana dashboards
```

Specifically:

1. **Vector database concepts** — Embeddings, HNSW, cosine similarity,
   quantization, hybrid search.
2. **ElasticSearch 9.x** — Index creation with explicit mappings, the
   `dense_vector` field type, approximate and exact kNN, filtered kNN, hybrid
   query+knn search, aggregations, the Python client.
3. **BIDS metadata** — Directory structure, filename entities, JSON sidecar
   fields, the inheritance principle, `pybids` for programmatic access.
4. **Kibana** — Data Views, Discover, Lens visualizations, Dashboards, Dev
   Tools Console.

---

## Scaling to Your Real Project

The neuroimaging metadata scraping system you're envisioning will likely differ
from this tutorial in several ways. Here's how to bridge the gaps:

### Larger Datasets

- **Index Lifecycle Management (ILM):** For datasets that grow over time,
  configure rollover policies to create new indices automatically.
- **Multi-shard indices:** Increase shard count for parallel query execution
  across nodes.
- **Bulk ingestion tuning:** Increase `refresh_interval` to `30s` or `-1`
  during bulk indexing, then reset. Use `helpers.parallel_bulk()` for
  multi-threaded ingestion.

### Ingesting Raw DICOM Headers

If your data isn't yet BIDSified, you can index DICOM headers directly:

```python
from pydicom import dcmread

ds = dcmread("scan.dcm")
doc = {
    "PatientID": str(ds.PatientID),  # de-identify in production!
    "Manufacturer": str(ds.Manufacturer),
    "MagneticFieldStrength": float(ds.MagneticFieldStrength),
    "RepetitionTime": float(ds.RepetitionTime) / 1000,  # ms → seconds
    "EchoTime": float(ds.EchoTime) / 1000,
    "SeriesDescription": str(ds.SeriesDescription),
    # ... add more tags as needed
}
```

Remember: DICOM times are in **milliseconds**, BIDS uses **seconds**.
De-identify patient data before indexing.

### ES-Native Embedding Models

Instead of generating embeddings in Python, you can deploy models inside ES:

- **`semantic_text` field type** — ES 9.x can auto-embed text at index time
  using a configured inference endpoint. Abstracts away embedding management.
- **Eland + ML nodes** — Deploy sentence-transformer models directly into ES
  using the [Eland Python library](https://www.elastic.co/guide/en/elasticsearch/client/eland/current/).
  Then use `query_vector_builder` to generate query embeddings server-side.

### Security for Production

Our local setup disables security. For any deployment beyond your laptop:

- Enable `xpack.security.enabled: true`
- Configure TLS/SSL for transport and HTTP
- Use API keys or basic auth for client connections
- Set up Role-Based Access Control (RBAC) — read-only users for search,
  write access only for ingestion pipelines

### Custom Search UI

Kibana is great for exploration, but for end users you may want:

- **[Elastic Search UI](https://github.com/elastic/search-ui)** — React
  component library by Elastic for building search interfaces.
- **Streamlit / Gradio** — Quick Python-based web apps. Display search results
  in a table, add sliders for numeric filters, text input for vector queries.
- **FastAPI + HTMX/React** — Full-stack option for a production web app.

### Multi-Dataset Federation

If you manage multiple BIDS datasets (or a mix of BIDS and raw DICOM):

- Use **index aliases** to query across multiple indices transparently.
- Add a `dataset` keyword field to each document for filtering.
- Consider **cross-cluster search** if datasets live on different ES clusters.

---

## Key Reference Links

### ElasticSearch & Kibana
- [ES Docker setup](https://www.elastic.co/guide/en/elasticsearch/reference/current/docker.html)
- [Dense vector field reference](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/dense-vector)
- [kNN search guide](https://www.elastic.co/docs/solutions/search/vector/knn)
- [Hybrid search](https://www.elastic.co/docs/solutions/search/hybrid-search)
- [Kibana introduction](https://www.elastic.co/guide/en/kibana/current/introduction.html)
- [Python client](https://elasticsearch-py.readthedocs.io/)
- [Approximate kNN tuning](https://www.elastic.co/docs/deploy-manage/production-guidance/optimize-performance/approximate-knn-search)

### Neuroimaging
- [BIDS Specification (v1.11.0)](https://bids-specification.readthedocs.io/en/stable/)
- [BIDS Common Principles](https://bids-specification.readthedocs.io/en/stable/common-principles.html)
- [MRI metadata fields](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/magnetic-resonance-imaging-data.html)
- [bids-examples](https://github.com/bids-standard/bids-examples)
- [OpenNeuro](https://openneuro.org/) — 1,000+ public BIDS datasets
- [pybids](https://bids-standard.github.io/pybids/)
- [dcm2niix](https://github.com/rordenlab/dcm2niix)
- [pydicom](https://pydicom.github.io/pydicom/stable/)

### Embedding Models
- [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) — The model we used
- [sentence-transformers](https://www.sbert.net/) — Library documentation
- [MTEB Leaderboard](https://huggingface.co/spaces/mteb/leaderboard) — Compare embedding models

---

**Congratulations — you've completed the course!**

You now have the foundational knowledge to build a neuroimaging metadata search
engine with ElasticSearch, vector search, and Kibana. The pieces are in place;
the next step is adapting this pipeline to your specific datasets and query
patterns.
