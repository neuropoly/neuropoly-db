---
description: Template for implementing Streamlit UI pages with proper state management, API integration, and user-friendly displays.
applyTo:
  - src/neuropoly_db/ui/**/*.py
---

# Streamlit Page Implementation

You are implementing a Streamlit page for the NeuroPoly DB neuroimaging search engine web UI.

## Context

- **Framework**: Streamlit for rapid data app development
- **API Client**: Centralized client from `neuropoly_db.core.api_client`
- **State Management**: Streamlit session state for UI state
- **Visualizations**: Plotly/Matplotlib for charts, pandas DataFrames for tables
- **User Experience**: Friendly error messages, clear feedback, progress indicators

## Code Structure

```python
# src/neuropoly_db/ui/pages/1_📄_Page_Name.py
import streamlit as st
import pandas as pd
import requests
from typing import Optional

# Page configuration (must be first Streamlit command)
st.set_page_config(
    page_title="Page Name - NeuroPoly DB",
    page_icon="📄",
    layout="wide",  # or "centered"
    initial_sidebar_state="expanded"
)

# Initialize session state
if "key" not in st.session_state:
    st.session_state.key = default_value

# Page header
st.title("📄 Page Title")
st.markdown("Brief description of this page's purpose.")

# Main content
# ... UI components here ...

# Helper functions at bottom
def helper_function():
    """Helper logic."""
    pass
```

## Guidelines

### Page Structure
- Use `st.set_page_config()` first (before other st commands)
- Start with clear title and description
- Use emoji in page titles for visual navigation
- Organize content with columns (`st.columns()`)
- Use expanders for optional/advanced features
- Put helper functions at bottom of file

### State Management
- Initialize session state early: `if "key" not in st.session_state:`
- Use session state for: search history, selected items, user preferences
- Don't store large data in session state (use `@st.cache_data` instead)
- Clear state when appropriate (form submissions, page changes)

### API Integration
- Use `try/except` blocks for all API calls
- Show `st.spinner()` during API calls
- Handle errors gracefully with `st.error()` / `st.warning()`
- Cache API results with `@st.cache_data(ttl=...)`

### User Feedback
- Use `st.success()` for successful operations
- Use `st.error()` for errors
- Use `st.warning()` for warnings/notices
- Use `st.info()` for informational messages
- Show progress with `st.progress()` / `st.spinner()`

### Data Display
- Use `st.dataframe()` for interactive tables
- Use `st.table()` for static tables (smaller data)
- Use `st.column_config` for custom column formatting
- Use `st.download_button()` for CSV/JSON export
- Use Plotly for interactive charts, Matplotlib for static

### Forms and Inputs
- Group related inputs in `st.form()` to prevent reruns
- Use clear labels and help text
- Set sensible defaults
- Add input validation before API calls

## Example: Search Page

```python
# src/neuropoly_db/ui/pages/1_🔍_Search.py
import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime
import time

# Page configuration
st.set_page_config(
    page_title="Search - NeuroPoly DB",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# API configuration
API_URL = "http://localhost:8000/api/v1"

# Initialize session state
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "last_results" not in st.session_state:
    st.session_state.last_results = None

# Page header
st.title("🔍 Search Neuroimaging Data")
st.markdown(
    "Search BIDS neuroimaging metadata using natural language queries. "
    "Supports keyword, semantic, and hybrid search modes."
)

# Sidebar: Search history
with st.sidebar:
    st.header("Search History")
    
    if st.session_state.search_history:
        for i, entry in enumerate(reversed(st.session_state.search_history[-10:])):
            if st.button(
                f"{entry['query'][:30]}... ({entry['mode']})",
                key=f"history_{i}",
                help=f"{entry['timestamp']} - {entry['results_count']} results"
            ):
                # Restore previous search
                st.session_state.query = entry['query']
                st.session_state.mode = entry['mode']
                st.rerun()
    else:
        st.info("No search history yet")
    
    if st.button("Clear History"):
        st.session_state.search_history = []
        st.rerun()

# Main content
# Search form
with st.form("search_form", clear_on_submit=False):
    # Query input
    query = st.text_input(
        "Search Query",
        value=st.session_state.get("query", ""),
        placeholder="e.g., T1w brain scans at 3 Tesla",
        help="Enter a natural language query describing the scans you're looking for"
    )
    
    # Search options
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        mode = st.selectbox(
            "Search Mode",
            options=["hybrid", "semantic", "keyword"],
            index=0,
            help=(
                "• Hybrid: Best overall (BM25 + neural embeddings)\n"
                "• Semantic: Meaning-based (neural embeddings only)\n"
                "• Keyword: Traditional full-text (BM25 only)"
            )
        )
    
    with col2:
        k = st.number_input(
            "Number of Results",
            min_value=1,
            max_value=100,
            value=10,
            step=5
        )
    
    with col3:
        dataset_filter = st.text_input(
            "Dataset Filter (optional)",
            placeholder="e.g., ds000001",
            help="Filter results to a specific dataset"
        )
    
    # Submit button
    submitted = st.form_submit_button("Search", type="primary", use_container_width=True)

# Execute search
if submitted:
    if not query:
        st.warning("Please enter a search query")
    else:
        # Show search parameters
        with st.expander("Search Parameters"):
            st.json({
                "query": query,
                "mode": mode,
                "k": k,
                "dataset_filter": dataset_filter or None
            })
        
        # Execute search with progress indicator
        with st.spinner(f"Searching with {mode} mode..."):
            try:
                start_time = time.time()
                
                response = requests.post(
                    f"{API_URL}/search",
                    json={
                        "query": query,
                        "mode": mode,
                        "k": k,
                        "dataset_filter": dataset_filter or None
                    },
                    timeout=30
                )
                
                response.raise_for_status()
                data = response.json()
                
                elapsed = time.time() - start_time
                
                # Store results
                st.session_state.last_results = data
                
                # Add to history
                st.session_state.search_history.append({
                    "query": query,
                    "mode": mode,
                    "results_count": data.get("total", 0),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
                })
                
                # Success message
                st.success(
                    f"✓ Found {data['total']} results in {data.get('query_time_ms', elapsed*1000):.0f}ms"
                )
            
            except requests.exceptions.Timeout:
                st.error("⚠ Search timed out. Try a simpler query or reduce the number of results.")
            
            except requests.exceptions.ConnectionError:
                st.error(
                    "⚠ Cannot connect to API server. "
                    "Make sure the API is running at http://localhost:8000"
                )
            
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    st.error(f"⚠ Invalid search parameters: {e.response.json().get('detail', str(e))}")
                elif e.response.status_code == 503:
                    st.error("⚠ Search service temporarily unavailable. Try again in a moment.")
                else:
                    st.error(f"⚠ API error ({e.response.status_code}): {str(e)}")
            
            except Exception as e:
                st.error(f"⚠ Unexpected error: {str(e)}")

# Display results
if st.session_state.last_results:
    results = st.session_state.last_results
    
    if results.get("results"):
        # Results summary
        st.subheader(f"Results ({results['total']})")
        
        # Convert to DataFrame
        df = pd.DataFrame(results["results"])
        
        # Tabs for different views
        tab1, tab2, tab3 = st.tabs(["📋 Table View", "📊 Visualizations", "💾 Export"])
        
        with tab1:
            # Interactive table
            st.dataframe(
                df,
                column_config={
                    "dataset": st.column_config.TextColumn(
                        "Dataset",
                        help="BIDS dataset identifier",
                        width="medium"
                    ),
                    "subject": st.column_config.TextColumn("Subject", width="small"),
                    "suffix": st.column_config.TextColumn("Modality", width="small"),
                    "score": st.column_config.NumberColumn(
                        "Score",
                        help="Relevance score (higher = better match)",
                        format="%.3f",
                        width="small"
                    ),
                    "metadata": st.column_config.TextColumn(
                        "Metadata",
                        help="Scanner and sequence parameters",
                        width="large"
                    )
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Detailed view for selected result
            with st.expander("View Detailed Metadata"):
                result_idx = st.selectbox(
                    "Select result",
                    options=range(len(df)),
                    format_func=lambda i: f"{df.iloc[i]['dataset']}/{df.iloc[i]['subject']} - {df.iloc[i]['suffix']}"
                )
                
                st.json(results["results"][result_idx])
        
        with tab2:
            # Score distribution
            fig_scores = px.histogram(
                df,
                x="score",
                nbins=20,
                title="Score Distribution",
                labels={"score": "Relevance Score", "count": "Number of Scans"}
            )
            st.plotly_chart(fig_scores, use_container_width=True)
            
            # Dataset breakdown
            dataset_counts = df["dataset"].value_counts()
            fig_datasets = px.pie(
                values=dataset_counts.values,
                names=dataset_counts.index,
                title="Results by Dataset"
            )
            st.plotly_chart(fig_datasets, use_container_width=True)
            
            # Modality breakdown
            if "suffix" in df.columns:
                modality_counts = df["suffix"].value_counts()
                fig_modality = px.bar(
                    x=modality_counts.index,
                    y=modality_counts.values,
                    title="Results by Modality",
                    labels={"x": "Modality", "y": "Count"}
                )
                st.plotly_chart(fig_modality, use_container_width=True)
        
        with tab3:
            # Export options
            st.markdown("### Download Results")
            
            col1, col2 = st.columns(2)
            
            with col1:
                # CSV export
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            with col2:
                # JSON export
                import json
                json_str = json.dumps(results, indent=2)
                st.download_button(
                    label="📥 Download as JSON",
                    data=json_str,
                    file_name=f"search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                    use_container_width=True
                )
    else:
        st.info("No results found. Try a different query or search mode.")
```

## Example: Ingestion Monitor Page

```python
# src/neuropoly_db/ui/pages/3_⚙️_Ingestion.py
import streamlit as st
import requests
from pathlib import Path
import time

st.set_page_config(
    page_title="Ingestion - NeuroPoly DB",
    page_icon="⚙️",
    layout="wide"
)

API_URL = "http://localhost:8000/api/v1"

st.title("⚙️ Dataset Ingestion")
st.markdown("Ingest BIDS datasets into Elasticsearch for searching.")

# Tabs for start vs monitor
tab1, tab2 = st.tabs(["🚀 Start Ingestion", "📊 Monitor Tasks"])

with tab1:
    st.subheader("Start New Ingestion")
    
    with st.form("ingest_form"):
        dataset_path = st.text_input(
            "Dataset Path",
            placeholder="/data/ds000001",
            help="Absolute path to BIDS dataset directory"
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            index_name = st.text_input(
                "Index Name (optional)",
                placeholder="neuroimaging-ds000001",
                help="Leave blank to auto-generate from dataset ID"
            )
        
        with col2:
            overwrite = st.checkbox(
                "Overwrite existing data",
                help="Delete existing index if it exists"
            )
        
        submitted = st.form_submit_button("Start Ingestion", type="primary")
    
    if submitted:
        if not dataset_path:
            st.error("Please provide a dataset path")
        else:
            try:
                with st.spinner("Starting ingestion..."):
                    response = requests.post(
                        f"{API_URL}/ingest/datasets",
                        json={
                            "dataset_path": dataset_path,
                            "index_name": index_name or None,
                            "overwrite": overwrite
                        },
                        timeout=10
                    )
                    response.raise_for_status()
                    data = response.json()
                
                st.success(f"✓ Ingestion started: {data['task_id']}")
                st.info(f"Monitor progress in the 'Monitor Tasks' tab")
                
                # Store task ID in session state
                if "active_tasks" not in st.session_state:
                    st.session_state.active_tasks = []
                st.session_state.active_tasks.append(data["task_id"])
            
            except Exception as e:
                st.error(f"Failed to start ingestion: {str(e)}")

with tab2:
    st.subheader("Active Ingestion Tasks")
    
    # Auto-refresh toggle
    auto_refresh = st.checkbox("Auto-refresh every 2 seconds", value=False)
    
    if "active_tasks" in st.session_state and st.session_state.active_tasks:
        for task_id in st.session_state.active_tasks:
            with st.container():
                try:
                    response = requests.get(f"{API_URL}/ingest/tasks/{task_id}", timeout=5)
                    response.raise_for_status()
                    task = response.json()
                    
                    # Task header
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.markdown(f"**Task:** `{task_id}`")
                    with col2:
                        state = task["state"]
                        if state == "SUCCESS":
                            st.success(state)
                        elif state == "FAILURE":
                            st.error(state)
                        elif state == "PROGRESS":
                            st.info(state)
                        else:
                            st.warning(state)
                    with col3:
                        if st.button("Remove", key=f"remove_{task_id}"):
                            st.session_state.active_tasks.remove(task_id)
                            st.rerun()
                    
                    # Progress bar
                    if task["state"] == "PROGRESS":
                        progress = task["progress"]
                        st.progress(
                            progress["percent"] / 100,
                            text=f"Processing {progress['current']}/{progress['total']} scans ({progress['percent']}%)"
                        )
                    
                    # Result
                    elif task["state"] == "SUCCESS":
                        result = task["result"]
                        st.success(
                            f"✓ Indexed {result['scans_indexed']} scans "
                            f"in {result['duration_seconds']:.1f}s"
                        )
                    
                    elif task["state"] == "FAILURE":
                        st.error(f"Error: {task.get('error', 'Unknown error')}")
                    
                    st.divider()
                
                except Exception as e:
                    st.error(f"Failed to get task status: {str(e)}")
    else:
        st.info("No active tasks")
    
    # Auto-refresh
    if auto_refresh:
        time.sleep(2)
        st.rerun()
```

## Caching Best Practices

```python
# Cache expensive computations
@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_datasets():
    """Fetch list of datasets from API."""
    response = requests.get(f"{API_URL}/datasets")
    return response.json()

# Cache with parameters
@st.cache_data(ttl=60)
def fetch_dataset_stats(dataset_id: str):
    """Fetch statistics for a specific dataset."""
    response = requests.get(f"{API_URL}/datasets/{dataset_id}/stats")
    return response.json()

# Clear cache button
if st.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()
```

## Checklist

Before submitting your Streamlit page implementation, verify:

- [ ] `st.set_page_config()` is first command
- [ ] Page has clear title and description
- [ ] Session state initialized for UI state
- [ ] API calls wrapped in `try/except` blocks
- [ ] Loading indicators (`st.spinner()`) for slow operations
- [ ] User-friendly error messages (not raw exceptions)
- [ ] Tables use `st.dataframe()` with column config
- [ ] Export buttons for CSV/JSON download
- [ ] Forms group related inputs (prevent reruns)
- [ ] Expensive operations cached with `@st.cache_data`

## Related

- [ADR-0003: Streamlit for MVP UI](../docs/architecture/adr/0003-streamlit-mvp-then-react.md)
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Streamlit Gallery](https://streamlit.io/gallery)
