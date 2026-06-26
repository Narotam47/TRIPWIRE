# Phase 4 Complete: History Walk & Diff Engine

**Date completed:** 2026-06-26  
**Working document for the Results section of the MCP drift paper.**

---

## 1. Final Sample

| Stage | Count | Notes |
|---|---|---|
| Repos sampled from MCPCrawler seed | 380 | Stratified by star tier and language |
| Successfully cloned and tool-located | 285 primaries (pre-final-fix) | After 3 rounds of contamination fixes |
| Walkable primaries (final) | **279** | After 4 exclusions (see §5) |
| Successfully walked | **276** | 3 `generic-inputSchema-search` repos produced 0 walk records |
| Unique (repo, tool_name) pairs captured | **4,784** | Across 276 repos |

Walk success rate: **276 / 279 = 98.9%**.

---

## 2. Tool-Level Drift Findings

### Snapshot summary

| Metric | Count | Rate |
|---|---|---|
| Tools with ≥1 snapshot (unique tools) | 4,784 | — |
| Tools with exactly 1 snapshot (no change observed) | 761 | 15.9% |
| Tools with ≥2 snapshots (at least one change captured) | **4,023** | **84.1%** |

> **Tool-level change rate: 84.1% (4,023 / 4,784)**  
> 84.1% of tracked tool definitions changed at least once during their observable history.

### Repo-level change rate

- 266 of 276 walked repos had at least one tool definition change: **96.4%**.
- 10 repos had every tool static across their full history.

---

## 3. Diff Engine Output (`tool_diffs.jsonl`)

### Record counts

| Record type | Count |
|---|---|
| `drift_event` (consecutive-version diffs) | **42,969** |
| `no_drift_observed` (single-snapshot tools) | 761 |
| **Total records** | **43,730** |

The 42,969 drift events span 4,023 tools across 276 repos. Events are consecutive-commit
pairs within a `(repo_url, tool_name)` group, sorted chronologically.

### Change breakdown

Of the 42,969 drift events:

| Change type | Events | % of drift_events |
|---|---|---|
| `description_changed = True` | **2,274** | 5.3% |
| `schema_fields_added` non-empty | **520** | 1.2% |
| `schema_fields_removed` non-empty | **326** | 0.8% |
| `schema_type_changes` non-empty | **102** | 0.2% |
| `source_file_changed = True` | **968** | 2.3% |
| **Events with ≥1 detected change** | **2,548** | **5.9%** |
| Events with no detected change | 40,421 | 94.1% |

The 94.1% zero-change rate is expected: the history walker records a snapshot for
every commit touching a tool's source file, but most such commits do not modify the
tool definition itself (they may touch surrounding code, imports, comments, etc.).
The study's signal is in the 2,548 events where the tool contract changed.

### Field-level semantics

- **`description_changed`**: unified text diff across the tool's natural-language description — the primary "rug-pull" surface.
- **`schema_fields_added` / `schema_fields_removed`**: field names added or dropped from `inputSchema.properties` — breaking changes for callers relying on those fields.
- **`schema_type_changes`**: fields whose JSON type changed (e.g., `string` → `integer`) — also breaking for strict callers.
- **`source_file_changed`**: the implementation file was moved/renamed at this transition while the tool name remained stable. 59 repos had at least one such event. Security-relevant: a file move can coincide with behavior change (confirmed in cloudflare/mcp-server-cloudflare data).
- **`is_inplace_mutation`**: structural invariant, always `True` for `drift_event` records. Confirms every diff is a mutation of a name-stable tool, not a replacement. See §4 for the complementary rename analysis.

---

## 4. Supplementary Finding: Naming-Convention Evolution

A secondary file-level analysis detected **688 rename candidates** where a tool
name changed between consecutive commits within the same source file, while schema
and/or description content was preserved (schema Jaccard ≥ 0.5, description
Jaccard ≥ 0.3).

| Confidence tier | Criterion | Candidates |
|---|---|---|
| Perfect | schema = 1.0 AND desc = 1.0 | 326 |
| High | schema = 1.0, desc < 1.0 | 293 |
| Medium | schema < 1.0 | 69 |
| **Total** | | **688** |

- **Repos affected**: 71 of 276 walked repos (**25.7%**).
- **High-confidence cases** (schema fully preserved across rename): 619.

**Framing:** These 688 candidates represent naming-convention evolution — tool names
being namespaced (`dialogs` → `tg_dialogs`), simplified
(`roam_create_output_with_nested_structure` → `roam_create_outline`), or restructured
(`listChannelTopics` → `topicsList`). This is **distinct from and not included in**
the primary drift counts above. The primary differ groups by `tool_name`; renames
produce two separate entries (old name ends, new name begins) and are not surfaced as
`drift_event` records.

The 688 figure is a lower bound: it is constrained by the tools tracked in the
history walker and the similarity thresholds applied.

**Files:** `data/processed/rename_candidates.csv`, `data/processed/rename_candidates_summary.md`.

---

## 5. Known Limitations

### L1 — MCPCrawler dataset unavailability
The original seed dataset (MCPCrawler, ~5,000 repos) was unavailable at collection
time. Sampling used the MCP Registry and curated GitHub search as a proxy, yielding
380 repos. The final sample over-represents highly-starred English-language servers
and under-represents newly published or lightly-starred repositories. This limits
generalisability to the long tail of the MCP ecosystem.

### L2 — Language exclusions
No extractor was implemented for **C#**. Repos identified as C# primary language were
excluded from the sample. Estimated impact: small (C# is a minor language in the
current MCP server ecosystem as of mid-2026).

### L3 — JSON source file unsupported by history walker (4 repos excluded)
Four repos had all tool definitions in source files not traversable by the per-commit
extractor:

| Repo | Reason |
|---|---|
| `latitude-dev/latitude-llm` | All 43 tools in `apps/api/mcp.json`; `.json` not in `_EXT_MAP` |
| `seanchatmangpt/dslmodel` | Tools found only via `generic-inputSchema-search` extractor |
| `cloud-apim/otoroshi-llm-extension` | Same: Scala repo, tool in JS resource file |
| `suhail-ak-s/mcp-typesense-server` | Same: TypeScript, unusual pattern not matched by TS extractor |

All four are marked `replacement_needed=True` with `replacement_reason` in
`batch_locate_results.jsonl`.

### L4 — Test/fixture/example directory contamination (three rounds of fixes)

The tool locator initially matched tool definitions inside test, fixture, and example
directories, inflating head-commit tool counts. Three rounds of fixes were applied
before history walking began:

**Round 1** (test infrastructure):
Added `test`, `tests`, `__tests__`, `__mocks__`, `_test.go` basename suffix.
Impact: 7 primaries dropped to zero (confirmed false-positive-only); 29 primaries
gained tools (early-exit suppression removed); 285 primaries confirmed.

**Round 2** (example/demonstration code):
Added `examples`, `example`, `fixtures`, `__fixtures__`.
Impact: 5 primaries dropped to zero; 285 → 280 primaries.

**Round 3** (framework sample/template code):
Added `samples`, `sample`, `testapps`, `templates`, `template`.
Impact: 0 primaries dropped to zero (all affected repos retained ≥1 real tool);
major false-positive correction in `firebase/genkit` (58 → 20 tools).

**Final** `_TEST_DIR_NAMES` exclusion set after all rounds:
`test`, `tests`, `__tests__`, `__mocks__`, `examples`, `example`, `samples`,
`sample`, `testapps`, `templates`, `template`, `fixtures`, `__fixtures__`.
`demos`/`demo` deliberately **not** excluded: `cloudflare/ai` ships production
deployed MCP servers inside a top-level `demos/` directory.

### L5 — Rename events not in primary drift counts
As detailed in §4, tool renames (688 candidates, 71 repos) are not captured in the
primary `tool_diffs.jsonl`. Rename chains linking old and new names are available in
`rename_candidates.csv` but are not integrated into the drift event counts.
Reported rename counts are a lower bound.

### L6 — History walk scope
The per-file history walker uses `git log --follow -- <file>` per source file.
Commits that moved tool definitions to a completely new file not tracked by
`--follow` may be missed at the file-rename boundary. File-move events are flagged
by `source_file_changed=True` in drift records (968 events, 59 repos) but the
transition commit itself may be recorded as a no-change pair if the file content
is identical at the point of extraction.

---

## 6. Artifact Index

| File | Description |
|---|---|
| `data/processed/batch_locate_results.jsonl` | 503 records: tool locations at HEAD for all sampled repos |
| `data/processed/tool_history_full.jsonl` | 47,753 snapshot records across 276 repos |
| `data/processed/tool_diffs.jsonl` | 43,730 diff records (42,969 drift_events + 761 no_drift_observed) |
| `data/processed/rename_candidates.csv` | 688 rename candidates from file-level secondary analysis |
| `data/processed/rename_candidates_summary.md` | Methods framing for rename supplementary finding |
| `src/tool_locator.py` | Final extractor with all contamination fixes applied |
| `scripts/07_history_walk.py` | Per-file and whole-repo history walk engine |
| `scripts/08_rerun_locate.py` | Re-evaluation script (ran 3× during contamination fix cycles) |
| `scripts/09_diff_tools.py` | Consecutive-version diff engine |
