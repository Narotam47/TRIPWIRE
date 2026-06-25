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
checkpointed so they can be safely interrupted and resumed:

```bash
# (scripts will be documented here as each phase is built)
python scripts/01_sample_repos.py
```

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
