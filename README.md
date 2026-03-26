# NEUROPOLY DATABASE MANAGEMENT

Everything you need to manage databases at the NeuroPoly lab ! Well, not really ... but at least you can get a bird's eye view of everything
available to you, then ask for access to it when needed. This repository doesn't let you **download** or **upload** data inside the databases,
but it gives you a complete view of the **metadata contained in those databases**, and optionally also provides **links to the data** for users
already authenticated with it.

## Databases

The databases available for preview through this service are:

- **NeuroPoly BIDS Database**

  The full set of BIDS datasets available at the NeuroPoly lab is **indexed in a Neurobagel node**. It is globaly
  accessible for perusing, but provides download links - usable by authenticated users only - to the **git-annexed
  storage endpoint**. Basically, you get a full view of `data.neuro.polymtl.ca`, **but you can't access it directly**.

## Installation

### NeuroPoly DB

To get the base **NeuroPoly DB** management stack up and running, you only need to install `uv`, **we handle everything else** :

```bash
curl -L https://astral.sh/uv/install.sh | sh
uv venv .venv
uv sync --activate
```

> [!WARNING]
> The above command **might fail if some virtual environment has already been configured in the provided directory (`.venv`)**. If you experience
> issues, simply **delete the content** under the virtual environment's directory and **re-run the command**.

### Neurobagel

**You need access to a Neurobagel node**. If deploying in production, or for external use outside the **NeuroPoly database use-case**, deploy
your own node, using the [Neurobagel documentation](https://neurobagel.org/user_guide/getting_started/). **Else, we recommend you use the
`devcontainer` provided in this repository**, which is pre-configured to setup a fully capable Neurobagel node for development purposes, tied
to your environment, with UIs directly accessible in your browser. To use the `devcontainer`, simply open this repository in a compatible code editor (like VSCode), and open the `devcontainer` when prompted. It will automatically install all dependencies, and start the Neurobagel node for you. There, all neurobagel services are accessible on a common `gateway` under **localhost**. Inspect the **VSCode forwarded ports** to find the right port to access the Neurobagel UI.

## Annotation Automation

The `npdb` CLI supports **automated phenotype annotation** for BIDS datasets using browser automation (Playwright) and optional AI suggestions (Ollama). This accelerates the process of converting raw phenotypic tabular data into Neurobagel-compatible JSON-LD format.

### Overview

Annotation works in 4 modes:

| Mode | User Input | AI | Browser | Use Case |
|------|---|---|---|---|
| **manual** | Full control | — | ✓ Headed | Interactive annotation with full flexibility |
| **assist** | Final review | Suggestions | ✓ Headed → Headless | Prefilled forms with user confirmation |
| **auto** | None (scripted) | ✓ High confidence (0.7+) | ✓ Headless | Fully automated, stable, bounded AI |
| **full-auto** | None (autonomous) | ✓ Lenient (0.5+) | ✓ Headless | Experimental, requires output review |

### Quick Start

#### Assist Mode (Recommended for Testing)
```bash
# Prerequisite: Activate environment
source .venv/bin/activate

# Run assist mode with UI feedback
npdb gitea2bagel my_dataset /output \
  --mode assist \
  --headed  # See browser automation in action
```

**What happens**:
1. Browser opens to Neurobagel annotation tool
2. Your `participants.tsv` is auto-uploaded
3. Columns are prefilled with AI suggestions
4. You review and finalize in the UI
5. `phenotypes.tsv`, `phenotypes_annotations.json`, and `phenotypes_provenance.json` are saved to `/output`

#### Auto Mode (Production)
```bash
# Fully scripted automation (no browser window)
npdb gitea2bagel my_dataset /output/bids \
  --mode auto \
  --headless \
  --timeout 600  # 10 min for slow networks
```

**Output files**:
- `phenotypes.tsv` — Annotated participants data
- `phenotypes_annotations.json` — Column → Neurobagel variable mappings
- `phenotypes_provenance.json` — Audit trail (confidence scores, rationale, warnings)

### AI Integration

#### Optional: Use Ollama for Enhanced Suggestions

If you have [Ollama](https://ollama.ai) installed locally, enable AI-powered column mapping:

```bash
# Start Ollama (if not already running)
ollama serve  # In another terminal

# Use full-auto mode with AI
npm gitea2bagel my_dataset /output \
  --mode full-auto \
  --ai-provider ollama \
  --ai-model neural-chat  # or your preferred model
```

### Annotation Workflow

The annotation automation follows these steps automatically:

1. **Navigate to annotation tool** – Opens annotate.neurobagel.org
2. **Click "Get Started"** – Proceeds from landing page to upload form
3. **Upload participants.tsv** – Auto-discovers TSV file input (auto-retry with fallbacks if needed)
4. **Upload phenotype dictionary (optional)** – If `--phenotype-dict` provided, uploads JSON dictionary
5. **Resolve columns** – Matches column headers to Neurobagel phenotypes (static dict + fuzzy matching)
6. **Fill forms** – Auto-populates form fields based on mode and confidence thresholds
7. **Export results** – Downloads annotation results and saves to output directory
8. **Save provenance** – Tracks decisions, confidence scores, and warnings

Each step includes retry logic (3 attempts with exponential backoff) and diagnostic output on failure.

**Optional phenotype dictionary**: Provide pre-populated mappings to improve annotation suggestions:

```bash
npdb gitea2bagel my_dataset /output \
  --mode assist \
  --phenotype-dict my_dict.json  # Optional pre-populated mappings
```

**AI confidence thresholds**:
- `auto` mode: AI mappings with ≥ 0.7 confidence
- `full-auto` mode: AI mappings with ≥ 0.5 confidence (lenient, review required)

### Troubleshooting

Common issues and solutions are documented in [`docs/PLAYWRIGHT_TROUBLESHOOTING.md`](docs/PLAYWRIGHT_TROUBLESHOOTING.md). Quick reference:

| Issue | Solution |
|-------|---|
| Timeout waiting for file upload | Increase `--timeout 600` for slow networks |
| Selector not found (browser UI changed) | Check `--artifacts-dir` for screenshots |
| Export file not created | Use manual mode to complete annotation interactively |
| Artifacts directory permission denied | Run `mkdir -p ./debug && chmod 755 ./debug` |

### Architecture

- **Browser Session** (`src/npdb/managers/browser_session.py`): Playwright lifecycle, retries, artifact capture
- **Annotation Orchestration** (`src/npdb/managers/annotation_automation.py`): Mode routing, confidence filtering, provenance tracking
- **Mapping Resolution** (`src/npdb/managers/mapping_resolver.py`): Static dictionary + fuzzy matching
- **Provenance** (`src/npdb/managers/provenance.py`): Audit trail and decision tracking

### Advanced Usage

#### Capture Failure Artifacts for Debugging
```bash
npdb gitea2bagel my_dataset /output \
  --mode assist \
  --artifacts-dir ./debug \
  --timeout 300
# On failure, check ./debug/ for screenshots and Playwright traces
```

#### Use Custom Phenotype Dictionary
```bash
npdb gitea2bagel my_dataset /output \
  --mode auto \
  --phenotype-dict my_dictionary.json  # Pre-populated mappings
```

#### Batch Processing Multiple Datasets
```bash
for dataset in dataset_1 dataset_2 dataset_3; do
  npdb gitea2bagel "$dataset" "./output_$dataset" \
    --mode auto \
    --headless
done
```

### System Requirements

- Python 3.12+
- `uv` package manager
- For automation: Playwright system libraries (auto-installed in devcontainer)
- For AI: Ollama running locally (optional)

**Playwright dependencies** are auto-installed via `uv sync --all-extras`. If running outside devcontainer, install:
```bash
# Install system packages (Debian/Ubuntu)
sudo apt-get install -y libgconf-2-4 libnss3 libxss1

# Install browser binaries
python -m playwright install chromium
```
