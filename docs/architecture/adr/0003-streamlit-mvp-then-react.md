# ADR-0003: Streamlit for MVP Web UI, React for Production

**Date:** 2026-03-09  
**Status:** Accepted  
**Deciders:** Solo developer + AI assistant  
**Technical Story:** Need web interface for non-technical users to search neuroimaging data

---

## Context

NeuroPoly DB needs a web user interface to:

1. **Enable Non-Technical Users**: 
   - Lab researchers who don't use CLI
   - Administrators monitoring ingestion jobs
   - External collaborators browsing datasets

2. **Core Features (MVP)**:
   - Search interface (text box, mode selector, results table)
   - Dataset browser (list datasets, view metadata)
   - Ingestion job tracker (start ingestion, monitor progress)
   - Basic visualizations (dataset distributions, search latency)

3. **Constraints**:
   - Solo developer with limited frontend experience
   - Need MVP fast (Week 9-12, 3 weeks)
   - Must work on desktop (1920×1080) and tablet (iPad)
   - Need AI assistance for frontend development
   - Initial users: 5-10 lab members (friendly users)

4. **Future Requirements**:
   - Multi-site deployment (50-100+ users)
   - Advanced features: saved searches, user accounts, API key management
   - Responsive mobile design
   - Real-time updates (WebSocket for job progress)
   - Integration with lab authentication (LDAP/SSO)

---

## Decision

We will build the MVP web UI with **Streamlit**, then migrate to **React + TypeScript** for production scale.

**Timeline:**
- **Phase 1 (Week 9-12)**: Streamlit MVP for internal lab use
- **Phase 2 (Month 4-6)**: React production UI for multi-site deployment

---

## Rationale

### Why Streamlit for MVP?

**Pros:**
1. **Rapid Development**: 
   - Pure Python (no HTML/CSS/JavaScript)
   - 50-100 lines of code for full search interface
   - Built-in components (text input, selectbox, dataframe, charts)
   - Solo developer can build MVP in days, not weeks
2. **AI-Friendly**: 
   - Simple patterns make it easy for AI to generate correct code
   - Vast training data (many Streamlit examples online)
3. **Data-Centric**: 
   - Designed for data apps (perfect for metadata search)
   - Native pandas DataFrame rendering
   - Built-in caching (`@st.cache_data`)
4. **Good Enough UX**: 
   - Professional look with minimal effort
   - Customizable themes (CSS)
   - Responsive layouts (columns, expanders, tabs)
5. **Deployment**: 
   - Single process (no Node.js build pipeline)
   - Docker-friendly (`streamlit run app.py`)
   - Low resource overhead (~100-200MB RAM)
6. **Friendly Users**: 
   - Internal lab users tolerate quirks (full-page reloads)
   - No need for pixel-perfect design

**Cons:**
1. **Not for Production Scale**:
   - Full page refresh on every interaction (not SPA)
   - Limited performance (100+ concurrent users struggle)
   - WebSocket overhead per session
2. **Limited Customization**:
   - Can't build complex UI patterns (drag-and-drop, advanced modals)
   - Component library is opinionated
3. **State Management**: 
   - Session state can be tricky for complex apps
   - No Redux/Zustand equivalents
4. **No Offline Support**: Requires server connection

### Why React for Production?

**Pros:**
1. **Production-Grade**:
   - SPA with instant interactions (no page reloads)
   - Scales to 1000+ concurrent users
   - Can be deployed to CDN (static site)
2. **Rich Ecosystem**:
   - Component libraries (MUI, Ant Design, shadcn/ui)
   - State management (Zustand, React Query)
   - Build tools (Vite, Next.js)
3. **Advanced Features**:
   - WebSocket for real-time updates
   - Offline support (PWA)
   - Complex UI patterns (drag-and-drop, infinite scroll)
4. **Professional UX**:
   - Smooth animations and transitions
   - Mobile-responsive design
   - Accessibility (WCAG compliance)
5. **Team-Friendly**:
   - When lab grows, easier to hire frontend devs
   - TypeScript for type safety
   - Component testing (Vitest, Testing Library)

**Cons:**
1. **Development Time**: 
   - Need to learn React + TypeScript (or rely heavily on AI)
   - 3-4× more code than Streamlit
   - Build pipeline (npm, webpack/vite)
2. **Deployment Complexity**:
   - Separate frontend/backend (CORS, proxying)
   - Frontend build step
   - Static hosting considerations

---

## Migration Path

### Phase 1: Streamlit MVP (Week 9-12)

**File Structure:**
```
src/neuropoly_db/ui/
  streamlit_app.py        # Main entry point
  pages/
    1_🔍_Search.py        # Search interface
    2_📚_Datasets.py      # Dataset browser
    3_⚙️_Ingest.py        # Ingestion jobs
    4_📊_Analytics.py     # Visualizations
  components/
    search_bar.py         # Reusable search widget
    results_table.py      # Results rendering
    progress_bar.py       # Job progress tracker
  config.toml             # Streamlit theme
```

**Example Search Page:**
```python
# pages/1_🔍_Search.py
import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="Search - NeuroPoly DB", page_icon="🔍")

st.title("🔍 Search Neuroimaging Data")

# Search inputs
col1, col2 = st.columns([3, 1])
with col1:
    query = st.text_input(
        "Enter your search query",
        placeholder="e.g., T1w brain scans at 3T"
    )
with col2:
    mode = st.selectbox(
        "Search mode",
        ["hybrid", "semantic", "keyword"]
    )

k = st.slider("Number of results", 5, 100, 10)

# Search button
if st.button("Search", type="primary"):
    with st.spinner("Searching..."):
        response = requests.post(
            "http://localhost:8000/api/v1/search",
            json={"query": query, "mode": mode, "k": k}
        )
        results = response.json()
    
    # Display results
    if results:
        st.success(f"Found {len(results)} results")
        
        # Convert to DataFrame for nice rendering
        df = pd.DataFrame(results)
        st.dataframe(
            df,
            column_config={
                "score": st.column_config.NumberColumn(
                    "Score",
                    format="%.3f"
                ),
                "metadata": st.column_config.TextColumn(
                    "Metadata",
                    width="large"
                )
            },
            hide_index=True
        )
        
        # Download as CSV
        csv = df.to_csv(index=False)
        st.download_button(
            label="Download results as CSV",
            data=csv,
            file_name="search_results.csv",
            mime="text/csv"
        )
    else:
        st.warning("No results found. Try a different query.")
```

**Deployment:**
```bash
# docker-compose.yml addition
  streamlit:
    build:
      context: .
      dockerfile: docker/Streamlit.Dockerfile
    command: streamlit run src/neuropoly_db/ui/streamlit_app.py
    ports:
      - "8501:8501"
    environment:
      - API_URL=http://api:8000
    depends_on:
      - api
```

### Phase 2: React Production (Month 4-6)

**Tech Stack:**
- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite (fast, modern)
- **UI Library**: shadcn/ui (customizable, accessible)
- **State Management**: Zustand (simple) + React Query (API state)
- **Styling**: Tailwind CSS + CSS modules
- **API Client**: Axios or fetch with React Query
- **Testing**: Vitest + React Testing Library

**File Structure:**
```
ui/
  src/
    components/
      SearchBar.tsx
      SearchResults.tsx
      DatasetBrowser.tsx
      IngestionMonitor.tsx
    pages/
      SearchPage.tsx
      DatasetsPage.tsx
      IngestPage.tsx
      AnalyticsPage.tsx
    lib/
      api.ts              # API client
      hooks.ts            # Custom React hooks
      utils.ts
    App.tsx
    main.tsx
  public/
    favicon.ico
  package.json
  tsconfig.json
  vite.config.ts
```

**Example Search Component:**
```tsx
// src/components/SearchBar.tsx
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Button } from '@/components/ui/button';

interface SearchBarProps {
  onResults: (results: SearchResult[]) => void;
}

export function SearchBar({ onResults }: SearchBarProps) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'hybrid' | 'semantic' | 'keyword'>('hybrid');
  const [k, setK] = useState(10);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['search', query, mode, k],
    queryFn: async () => {
      const response = await fetch('/api/v1/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, mode, k })
      });
      return response.json();
    },
    enabled: false  // Manual trigger only
  });

  const handleSearch = () => {
    refetch().then(({ data }) => {
      if (data) onResults(data);
    });
  };

  return (
    <div className="flex gap-4">
      <Input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="e.g., T1w brain scans at 3T"
        className="flex-1"
        onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
      />
      <Select value={mode} onValueChange={setMode}>
        <option value="hybrid">Hybrid</option>
        <option value="semantic">Semantic</option>
        <option value="keyword">Keyword</option>
      </Select>
      <Button onClick={handleSearch} disabled={!query || isLoading}>
        {isLoading ? 'Searching...' : 'Search'}
      </Button>
    </div>
  );
}
```

---

## Example Timeline

### Streamlit MVP (Weeks 9-12)

| Week | Deliverable                        | Hours |
| ---- | ---------------------------------- | ----- |
| 9    | Streamlit setup, search page       | 10    |
| 10   | Dataset browser, ingestion monitor | 10    |
| 11   | Analytics page, polish UI          | 8     |
| 12   | Testing with lab users, bug fixes  | 8     |

**Total: 36 hours (9 hours/week solo dev)**

### React Production (Months 4-6)

| Month | Deliverable                              | Hours |
| ----- | ---------------------------------------- | ----- |
| 4     | React setup, design system, search page  | 40    |
| 5     | Dataset browser, ingestion monitor, auth | 40    |
| 6     | Analytics, testing, deployment           | 40    |

**Total: 120 hours (10 hours/week solo dev)**

**Cost-Benefit:** Streamlit buys us 3 months to validate product-market fit before investing in React.

---

## Consequences

### Positive

1. **Fast Time-to-Value**: Lab users get working UI in 3 weeks
2. **Low Risk**: If Streamlit MVP fails, we save 120 hours of React development
3. **Learning Time**: Solo dev learns NeuroPoly DB domain before tackling frontend
4. **User Feedback**: Streamlit MVP informs React UX decisions
5. **Parallel Work**: Can build Streamlit while React is being designed

### Negative

1. **Throwaway Code**: Streamlit UI is discarded after 3-6 months
2. **User Confusion**: UI paradigm changes (Streamlit → React)
3. **Double Work**: Some features built twice (but simpler in Streamlit)

### Neutral

1. **Maintenance Window**: Keep Streamlit running while React is built (no downtime)
2. **Migration Strategy**: Beta test React with power users before full cutover

---

## When to Migrate to React

Trigger migration when:
- [ ] 20+ active users (Streamlit performance degrading)
- [ ] Users request advanced features (saved searches, complex filters)
- [ ] Multi-site deployment planned (need professional UI)
- [ ] External users (need polished UX, mobile support)
- [ ] Integration with authentication (SSO, LDAP)

**Do NOT migrate if:**
- Only 5-10 lab users
- No complaints about Streamlit UX
- Other priorities (ingestion speed, search quality)

---

## Validation

We will validate this decision by:
- [ ] Streamlit MVP deployed by end of Week 12
- [ ] 5+ lab users actively using Streamlit UI (Week 13)
- [ ] User feedback: "UI is good enough" vs "we need better UX" (Month 4)
- [ ] Decision point: Migrate to React OR keep Streamlit (Month 4)

---

## References

- [Streamlit Documentation](https://docs.streamlit.io/)
- [Streamlit Gallery](https://streamlit.io/gallery) — Real-world examples
- [React Documentation](https://react.dev/)
- [shadcn/ui Components](https://ui.shadcn.com/) — Modern React UI library
- [Vite](https://vitejs.dev/) — Fast React build tool

---

**Supersedes:** N/A  
**Superseded by:** N/A (may be superseded by React-only approach if timeline changes)  
**Related:** 
- ADR-0001 (FastAPI for API Layer) — Both UIs consume the same API
- ADR-0004 (Scaling Strategy) — React required for multi-site deployment
