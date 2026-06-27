# Phase 4 Complete: History Walk & Diff Engine

**Date completed:** 2026-06-26  
**Working document for the Results section of the MCP drift paper.**

---

## 1. Final Sample

| Stage | Count | Notes |
|---|---|---|
| Repos sampled from MCPCrawler seed | 380 | Stratified by star tier and language |
| Successfully cloned and tool-located | 285 primaries (post-round-1) | After round 1 of test-directory contamination fixes |
| After all 3 L4 fix rounds | **280** | Round 2 −5; round 3 −0 |
| Successfully walked | **276** | 4 L3 repos produced 0 walk records (see §5 L3) |
| Unique (repo, tool_name) pairs captured | **4,784** | Across 276 repos |

Walk success rate: **276 / 280 = 98.6%**.

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

> **Note on the 4,023 count:** This figure (derived from the diff engine's
> `drift_event` grouping) includes 9 tools whose only detected "change" is the L7
> same-SHA artifact (a tool defined in multiple source files at the same commit —
> a spatial, not temporal, difference; see §5 L7). Counting unique commit SHAs per
> tool in the history walk instead yields 4,014 genuinely-changed tools, i.e.
> **83.9% (4,014 / 4,784)**. **84.1% remains the reported headline figure**; the
> 9-tool effect is negligible (0.2 percentage points) and does not affect any
> downstream finding.

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

**Definition note — what counts toward "valid temporal change events":** The "≥1
detected change (raw)" row and the resulting **2,481 valid temporal change events**
count only events with a **content change** — a description change or a schema change
(fields added/removed, type changes). Events whose *only* detected change is
`source_file_changed = True` (a file move/rename with no accompanying change to the
description or schema) are **excluded**, because a pure relocation of an unchanged
definition is not a temporal change to the tool itself. The `source_file_changed`
row (968) is therefore listed in the table for completeness but is **not additive**
to the 2,548 raw / 2,481 valid totals; the 2,548 figure is the union of the first
four rows only, after which 67 degenerate same-SHA records (§5 L7) are removed to
reach 2,481.

| Change type | Events | % of drift_events |
|---|---|---|
| `description_changed = True` | **2,274** | 5.3% |
| `schema_fields_added` non-empty | **520** | 1.2% |
| `schema_fields_removed` non-empty | **326** | 0.8% |
| `schema_type_changes` non-empty | **102** | 0.2% |
| `source_file_changed = True` | **968** | 2.3% |
| **Events with ≥1 detected change (raw)** | **2,548** | **5.9%** |
| — of which: degenerate same-SHA events (see §5 L7) | 67 | — |
| **Valid temporal change events** | **2,481** | **5.8%** |
| Events with no detected change | 40,421 | 94.1% |

The 94.1% zero-change rate is expected: the history walker records a snapshot for
every commit touching a tool's source file, but most such commits do not modify the
tool definition itself (they may touch surrounding code, imports, comments, etc.).
The study's signal is in the **2,481 valid temporal change events** (2,548 minus 67
degenerate same-SHA records — see L7).

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

### L7 — Degenerate same-SHA diff records (67 events, 7 repos)

When a tool name appears in multiple source files in the same commit (e.g., a primary
implementation and a re-export or test fixture that survived contamination filtering),
the history walker may record two snapshots for the same `(repo_url, tool_name, commit_sha)`.
The differ then produces a diff record with `from_sha == to_sha` but a non-zero detected
change — a spatial inconsistency between two concurrent definitions, not a temporal change.

67 such records were found across 7 repos:
`mcp-atlassian` (30), `n8n-workflow-builder` (16), `cloudflare/ai` (11), `llmvm` (3),
`mcp-server-cloudflare` (3), `learn-agentic-ai` (2), `mcp-simple-timeserver` (2).

These 67 events are excluded from the LLM classifier input and from human validation.
The corrected headline count for valid temporal change events is **2,481** (not 2,548).

### L6 — History walk scope
The per-file history walker uses `git log --follow -- <file>` per source file.
Commits that moved tool definitions to a completely new file not tracked by
`--follow` may be missed at the file-rename boundary. File-move events are flagged
by `source_file_changed=True` in drift records (968 events, 59 repos) but the
transition commit itself may be recorded as a no-change pair if the file content
is identical at the point of extraction.

### L11 — Representativeness of the 276-repo walked set vs. the 364-repo achieved sample

The history walker processed only the 276 primary repos (those selected in the
initial stratified draw); 78 backup repos that filled unfilled strata slots were
not targeted by the walker. The table below compares the language and star-tier
distribution of the walked set against the full 364-repo achieved sample.

**Language distribution:**

| Language | 364-repo achieved | | 276-repo walked | | Δ (pp) |
|---|---|---|---|---|---|
| TypeScript | 161 | 44.2% | 108 | 39.1% | −5.1 |
| Python | 130 | 35.7% | 102 | 37.0% | +1.2 |
| JavaScript | 50 | 13.7% | 47 | 17.0% | +3.3 |
| Go | 9 | 2.5% | 8 | 2.9% | +0.4 |
| Rust | 4 | 1.1% | 4 | 1.4% | +0.4 |
| Other | 10 | 2.7% | 7 | 2.5% | −0.2 |
| **Total** | **364** | | **276** | | |

**Star-tier distribution:**

| Tier | 364-repo achieved | | 276-repo walked | | Δ (pp) |
|---|---|---|---|---|---|
| 10–49 | 109 | 29.9% | 85 | 30.8% | +0.9 |
| 50–199 | 107 | 29.4% | 82 | 29.7% | +0.3 |
| 200–999 | 85 | 23.4% | 67 | 24.3% | +0.9 |
| 1000+ | 63 | 17.3% | 42 | 15.2% | −2.1 |
| **Total** | **364** | | **276** | | |

The largest single deviation is −5.1 pp for TypeScript (39.1% walked vs. 44.2% achieved) and −2.1 pp for the 1000+ star tier. All other cells differ by ≤3.3 pp. No language or tier stratum is absent from the walked set. The 276-repo walked set is materially close to the 364-repo achieved sample in both dimensions; the backup-repo exclusion does not introduce a representativeness gap large enough to qualify the drift findings by stratum.

---

---

## 6. LLM Classifier — Phase 5 Status

### Design

Two-pass classifier using `claude-haiku-4-5-20251001` on the 2,481 valid temporal
change events. Each event is classified into one of five categories:

| Label | Definition |
|---|---|
| `COSMETIC` | Formatting/typo/wording with no meaning change |
| `CLARIFICATION` | Description adds detail about existing behavior |
| `SCHEMA_EXPANSION` | New input fields added |
| `SCHEMA_CONTRACTION` | Input fields removed |
| `BEHAVIORAL_DRIFT` | Description implies changed purpose, scope, or side-effects |

**Pass 1:** before→after, neutral framing, categories listed COSMETIC→BEHAVIORAL_DRIFT  
**Pass 2:** before→after (same direction), neutral framing, categories listed
CLARIFICATION→SCHEMA_EXPANSION→BEHAVIORAL_DRIFT→COSMETIC→SCHEMA_CONTRACTION
(different ordering and inline definitions to test label stability without a severity bias).

An earlier Pass 2 design used an "auditor" persona and severity-first ordering. After
a 20-event test showed systematic over-escalation to `BEHAVIORAL_DRIFT` (3/4
ground-truth keboola cases wrong), Pass 2 was redesigned to be neutrally framed. The
revised 20-event test resolved the clear-cut disagreement (keboola schema+desc →
SCHEMA_EXPANSION on both passes); the 4 remaining disagreements are genuinely borderline
cases (optionality changes in text, large schema restructures) with defensible labels
on both sides.

### Pilot results

20-event ground-truth test (fixed Pass 2):

| Case | Expected | Pass 1 | Pass 2 | Agree |
|---|---|---|---|---|
| keboola `create_sql_transformation` (schema+desc) | SCHEMA_EXPANSION | ✓ | ✓ | ✓ |
| keboola `create_sql_transformation` (desc-only) | CLARIFICATION | ✓ P1 | BEHAVIORAL_DRIFT P2 | ✗ |
| things-mcp `get_inbox` (limit+offset) | SCHEMA_EXPANSION | ✓ | ✓ | ✓ |
| linear-mcp-go `linear_add_comment` (desc-only) | CLARIFICATION | ✓ | ✓ | ✓ |

Agreement on 20-event test: **16/20 = 80.0%**  
Agreement on 75-event validation sample (clean): **64/75 = 85.3%**

### Human validation sample

75 events sampled from 2,481 valid temporal change events, stratified by structural
change type:

| Structural type | Events in pool | In sample |
|---|---|---|
| desc_only | 1,770 (71.3%) | 27 |
| desc_and_schema_add | 182 (7.3%) | 11 |
| schema_mixed | 166 (6.7%) | 6 |
| schema_add_only | 135 (5.4%) | 12 |
| type_change | 102 (4.1%) | 5 |
| schema_remove_only | 65 (2.6%) | 8 |
| desc_and_schema_remove | 61 (2.5%) | 6 |

Files:
- `data/processed/human_validation_sample.csv` — for hand-labeling (no machine labels)
- `data/processed/human_validation_machine.jsonl` — machine labels (kept separate until Kappa computation)
- `data/processed/human_validation_seeds.json` — event identifiers for reproducibility

After hand-labeling, run `python scripts/11_human_validation.py --compute-kappa` to
compute pairwise Cohen's Kappa (human vs P1, human vs P2, P1 vs P2) and three-rater
Fleiss' Kappa. Requires `scikit-learn`.

### Status

- [x] Pass 2 bias fix implemented and validated
- [x] 75-event human validation sample generated and machine-classified
- [x] Human labels complete (74 hand-labeled; E074 excluded as L7 cross-file artifact)
- [x] Kappa computation complete (Human/P1 κ=0.605, Human/P2 κ=0.638, P1/P2 κ=0.826, Fleiss κ=0.687)
- [x] Full 2,481-event classification run complete (0 errors, 87.7% pass agreement)

> Phase 5 and Phase 6 are both complete. See [`docs/phase5_summary.md`](phase5_summary.md)
> and [`docs/phase6_summary.md`](phase6_summary.md) for final results.

---

## 7. Artifact Index

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
| `scripts/10_llm_classifier.py` | Two-pass LLM classifier (Haiku); use `--batch N --seed-events PATH` for test mode |
| `scripts/11_human_validation.py` | Human validation sample generator and Kappa computation |
| `data/processed/human_validation_sample.csv` | 75-event stratified sample for hand-labeling |
| `data/processed/human_validation_machine.jsonl` | Machine labels for the 75-event sample (P1 + P2) |
| `data/processed/human_validation_seeds.json` | Event identifiers for the 75-event sample (reproducible) |
