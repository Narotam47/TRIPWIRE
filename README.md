# MCP Drift Study

Empirical measurement of how MCP (Model Context Protocol) server tool definitions
change over time, and whether those changes measurably alter AI agent behavior.

This project fills a gap identified in the literature: while "rug pull" attacks
(tools that change silently after user approval) have been named as a threat
(arXiv:2506.01333), no published study has measured how often this actually
happens in real, deployed MCP servers.

## Research design

1. **Git-history mining** — reconstruct per-tool definition changes from the
   full commit history of public MCP server repositories.
2. **Live-polling arm** — weekly snapshots of hosted MCP servers to catch
   behavioral drift that never surfaces in public repos.
3. **Agentic behavioral validation** — run real before/after definition pairs
   through a live AI agent (Anthropic API) and measure decision change.

Related papers used as sample-frame sources:
- arXiv:2509.25292 — MCPCrawler dataset
- arXiv:2506.13538 — MCP-at-First-Glance replication package

## Project layout

```
mcp-drift-study/
  data/
    raw/          # Unmodified inputs: seed repo lists, raw API responses
    processed/    # Parsed, normalized data ready for analysis
    snapshots/    # Point-in-time live-poll snapshots
  src/            # Importable library modules
  scripts/        # Runnable pipeline scripts (one task each)
  notebooks/      # Exploratory analysis and figures
  tests/          # pytest unit + integration tests
```

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url> mcp-drift-study
cd mcp-drift-study
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in GITHUB_TOKEN and ANTHROPIC_API_KEY
```

The `.env` file is git-ignored. Never commit real credentials.

## Running the pipeline

Scripts in `scripts/` are designed to be run in order and are independently
checkpointed so they can be safely interrupted and resumed. All intermediate
outputs are committed to `data/processed/`, so a reviewer can start at any
stage without re-running earlier steps.

> **Note on script 06:** There is no `06_*.py`. Cloning of target repositories
> was integrated directly into `07_history_walk.py` (via `src/cloner.py`) to
> keep the clone and walk atomic. The numbering gap is intentional, not an
> omission.

### Phase 1–3: Sample construction (requires `GITHUB_TOKEN` in `.env`)

| Script | What it does | Key inputs → outputs |
|---|---|---|
| `01_load_seed_data.py` | Downloads the MCPCrawler and MCP-at-First-Glance seed server lists from their replication packages | network → `data/raw/seed_servers.csv` |
| `02_enrich_github_metadata.py` | Fetches star count, language, pushed-at date, and archived status for each seed repo via the GitHub API | `seed_servers.csv` → `data/raw/github_repo_metadata.jsonl` |
| `03_sample_repos.py` | Stratified sample (star tier × language) of 380 primary repos + a backup pool; fixes random seed 42 | `github_repo_metadata.jsonl` → `data/processed/sample_primary_380.csv`, `sample_backup_pool.csv` |
| `04_test_batch.py` | Dry-run tool-locator on a small subset to validate extractors before the full batch | `sample_primary_380.csv` → console report |
| `05_full_batch_locate.py` | Runs the tool locator across all 380 repos; checkpoints after every repo so it can be safely interrupted | `sample_primary_380.csv` → `data/processed/batch_locate_results.jsonl` |

### Phase 4: History walk and diff (no API keys needed)

| Script | What it does | Key inputs → outputs |
|---|---|---|
| `07_history_walk.py` | Clones each primary repo (shallow → unshallow as needed) and extracts per-commit tool definition snapshots using `git log --follow` | `batch_locate_results.jsonl` → `data/processed/tool_history_full.jsonl` |
| `08_rerun_locate.py` | Re-runs the locator against already-cloned repos after updating the exclusion list (used 3× during contamination-fix cycles; not needed for a clean reproduce) | `data/raw/clones/` → updated `batch_locate_results.jsonl` |
| `09_diff_tools.py` | Produces consecutive-version diff records for every (repo, tool) pair | `tool_history_full.jsonl` → `data/processed/tool_diffs.jsonl` |

### Phase 5: LLM classification and human validation (requires `ANTHROPIC_API_KEY` in `.env`)

| Script | What it does | Key inputs → outputs |
|---|---|---|
| `10_llm_classifier.py` | Two-pass Haiku classifier; assigns one of five labels to each of the 2,481 valid temporal change events | `tool_diffs.jsonl` + `tool_history_full.jsonl` → `data/processed/tool_classifications.jsonl` |
| `11_human_validation.py` | Generates the 75-event stratified human-validation sample; `--compute-kappa` computes pairwise Cohen's κ and Fleiss' κ once human labels are present in the CSV | `tool_classifications.jsonl` → `data/processed/human_validation_sample.csv` (for labeling); `--compute-kappa` reads `human_validation_sample.csv` + `human_validation_machine.jsonl` |
| `12_label_helper.py` | Interactive terminal helper for reviewing individual classification decisions (optional; not required for any headline result) | `tool_classifications.jsonl` → console |

### Phase 6: Agentic behavioral validation (requires `ANTHROPIC_API_KEY` in `.env`)

| Script | What it does | Key inputs → outputs |
|---|---|---|
| `13_agentic_validation.py` | Runs 15 purposively selected BEHAVIORAL_DRIFT events through a live agent at temperature=0 (3 replications × 2 sides); `--dry-run` prints scenarios without API calls; `--slot N` runs a single slot | `data/processed/agentic_test_candidates.json` → `data/processed/agentic_validation_results.json` |

### Detailed methodology

- Phase 4 design, limitations (L1–L11), and artifact index: [`docs/phase4_summary.md`](docs/phase4_summary.md)
- Phase 5 classifier design, kappa results, and limitations (L7–L10): [`docs/phase5_summary.md`](docs/phase5_summary.md)
- Phase 6 candidate selection, scenario design, and behavioral consequence taxonomy: [`docs/phase6_summary.md`](docs/phase6_summary.md)

## Reproducing results

All random seeds are fixed and logged. Pipeline state is checkpointed in
`data/processed/`. To reproduce from scratch, delete `data/processed/` and
re-run scripts in order.

## Testing

```bash
pytest tests/ -v
```

## Limitations and threats to validity

- Git-history mining cannot observe changes to hosted (non-open-source) MCP servers.
- Live-polling arm only covers servers reachable without authentication.
- LLM-jury classification is validated against a human-labeled sample (Cohen's κ
  reported); ground truth for "security-relevant" is inherently judgment-dependent.
