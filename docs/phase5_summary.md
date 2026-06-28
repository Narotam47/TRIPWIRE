# Phase 5: LLM Classifier — Design, Validation, and Full-Scale Run

**Date started:** 2026-06-26  
**Date completed:** 2026-06-27  
**Working document for the paper's Methods and Limitations sections.**

---

## Executive Summary

This study tracked MCP (Model Context Protocol) tool definitions across their full git histories for a stratified sample of 380 GitHub repositories. Of the 380 sampled repos, 280 were targeted by the history walker after three rounds of test-directory contamination fixes (the remainder excluded due to language gaps, JSON-only sources, or zero-tool results); 276 were successfully mined (4 produced zero walk records due to unsupported source-file formats — see Phase 4 §5 L3). Across those 276 repos, **4,784 unique (repo, tool-name) pairs** were identified and tracked. **84.1% of tools changed at least once** over their observable git history; 96.4% of repos had at least one tool definition change.

The diff engine produced 42,969 consecutive-version diff records. Of these, 2,548 showed at least one detected change; 67 were excluded as degenerate same-SHA records (a tool defined in multiple source files within the same commit, producing a spatial comparison rather than a temporal one). The final analysis corpus is **2,481 valid temporal change events** across 276 repos.

A two-pass LLM classifier (`claude-haiku-4-5-20251001`) assigned each event one of five labels — COSMETIC, CLARIFICATION, SCHEMA_EXPANSION, SCHEMA_CONTRACTION, BEHAVIORAL_DRIFT — independently twice, using surface-varied prompts to test label stability. A 75-event stratified human validation sample (74 events labeled; 1 excluded as a cross-file artifact) yielded Fleiss' κ = 0.687 (substantial) across three raters. Pass-to-pass machine agreement was κ = 0.826 (almost perfect).

**Primary finding — BEHAVIORAL_DRIFT rate:**

- **Conservative estimate (both passes agree): 309 events, 12.5%** of all valid change events
- **Best estimate (P2, better calibrated on description-only changes per follow-up review): 474 events, 19.1%**
- Defensible range for paper reporting: **309–474 (12.5–19.1%)**

BEHAVIORAL_DRIFT is concentrated: the top 10 repos account for 55.9% of all 474 BD(P2) events. The distribution is not uniform — a subset of rapidly-evolving repos drives the ecosystem-level rate.

**Cross-tabulation findings:**

- **By source type:** Official repos (maintained by the tool's own vendor) have the lowest drift rate at **14.2% (P2)**; community-submitted repos have the highest at **23.8% (P2)**. This is the most structurally interpretable finding and the cleanest gradient in the data.
- **By star tier:** No monotone relationship between popularity and stability. The 50–199 star tier has the lowest drift rate (14.1%); the 1000+ tier reads highest (26.4%) but is driven almost entirely by mcp-atlassian.
- **By language:** Python (20.5%) runs above TypeScript (17.7%) and JavaScript (12.7%), but the Python elevation is largely a single-repo effect: removing mcp-atlassian alone brings Python to 16.7%, within 1 point of TypeScript.

**Supplementary finding:** 688 tool-rename candidates documented across 71 repos (25.7% of walked repos), tracked separately in `rename_candidates.csv`. These represent naming-convention evolution (namespacing, simplification, restructuring) and are not included in the primary drift event counts.

**Limitations documented (L1–L10):**

- L1: Sample over-represents high-star English repos; long tail under-represented
- L2: C# repos excluded (no extractor)
- L3: 4 repos excluded (JSON-only or unusual source file patterns)
- L4: Three rounds of test/fixture directory contamination fixes applied
- L5: Rename events (688) not in primary drift counts; lower bound
- L6: History walk scope limited by `git log --follow`; cross-file moves at boundary may be missed
- L7: 67 degenerate same-SHA records excluded (tool in multiple source files at same commit); 7 repos affected
- L8: 53 events in valid 2,481 have empty schemas on both sides and `source_file_changed=True`; all are desc_only type; potential cross-file description comparison artifacts
- L9: `schema_remove_only` events (65 in pool) cannot be distinguished as BEHAVIORAL_DRIFT vs SCHEMA_CONTRACTION by text alone; classifier conservatively labels these SCHEMA_CONTRACTION; BEHAVIORAL_DRIFT counts are a lower bound for this structural type
- L10: Tutorial/educational repos structured with numbered chapter directories can produce false temporal drift events (same tool name, different chapters); caught only by manual source_file path inspection; automated check does not exist

---

---

## 1. Classifier Design

### Taxonomy

Five mutually exclusive labels covering all observable change types in MCP tool definitions:

| Label | Definition |
|---|---|
| `COSMETIC` | Formatting, typo fix, or rewording that preserves identical meaning |
| `CLARIFICATION` | Description adds detail, examples, or guidance about existing behavior — no change to what the tool does or how it is called |
| `SCHEMA_EXPANSION` | One or more new input fields added to `inputSchema.properties` |
| `SCHEMA_CONTRACTION` | One or more existing input fields removed from `inputSchema.properties` |
| `BEHAVIORAL_DRIFT` | Description implies the tool's purpose, target system, scope, or side-effects changed; or fields are removed AND the description simultaneously signals a scope/purpose change |

`BEHAVIORAL_DRIFT` is the primary construct of interest: it captures changes where the tool's actual behavior (not just its documentation quality or interface footprint) shifted — the "rug-pull" scenario.

### Two-pass LLM jury

Each of the 2,481 valid temporal change events is classified independently twice using the same model (`claude-haiku-4-5-20251001`). Passes differ only in surface presentation — prompt wording, sentence structure, and category ordering — to test label stability. Systematic disagreement reveals genuine ambiguity; high pass-to-pass agreement (κ ≥ 0.80) validates classifier consistency before inter-rater comparisons with a human annotator.

**Input to each classification call:**

- Tool name, repo URL
- Summary of changes detected by the diff engine (fields added/removed, description changed flag)
- Before/after description text
- Before/after input field list (names only)
- Before/after required field list

**Output:** a single label plus a one-sentence justification. Both passes run sequentially per event; no cross-contamination between calls.

### Pass 1 prompt

System: neutral classifier framing — "Your job is to identify what category of change occurred based only on the evidence in the before and after states provided."

User: before→after presentation, categories listed in fixed order (COSMETIC, CLARIFICATION, SCHEMA_EXPANSION, SCHEMA_CONTRACTION, BEHAVIORAL_DRIFT) with brief inline definitions.

### Pass 2 prompt

System: identical neutral framing to Pass 1 (no auditor persona, no severity-first language).

User: before→after presentation (same direction as Pass 1), categories listed in rotated order (CLARIFICATION, SCHEMA_EXPANSION, BEHAVIORAL_DRIFT, COSMETIC, SCHEMA_CONTRACTION) with fuller inline definitions. Rotation and expanded definitions constitute the only substantive difference from Pass 1.

---

## 2. Pass 2 Severity Bias — Discovery and Fix

### Original design flaw

The initial Pass 2 prompt used an "auditor" persona ("You are a meticulous auditor..."), listed categories in severity-first order (BEHAVIORAL_DRIFT first), and presented change evidence after→before (reversed from Pass 1). A 20-event ground-truth test on known-label seed cases revealed systematic over-escalation: Pass 2 assigned `BEHAVIORAL_DRIFT` to 3 of 4 borderline cases that Pass 1 correctly labeled as `SCHEMA_EXPANSION` or `CLARIFICATION`. This was a prompt-design artifact, not a genuine label difference.

### Fix

Pass 2 redesigned to neutral framing, non-severity-ordered category listing, and before→after direction identical to Pass 1. The three surface differences retained to test stability: slightly different phrasing of the category definitions, rotated order, and expanded inline explanations.

### Validation (20-event re-test)

After the fix, the previously wrong keboola `create_sql_transformation` (schema + description expansion) correctly resolved to `SCHEMA_EXPANSION` on both passes. The 4 remaining disagreements on the 20-event set were confirmed as genuinely borderline cases (optionality language changes, large schema restructures) with defensible labels on both sides.

---

## 3. Human Validation

### Stratified sample design

75 events sampled from the 2,481 valid temporal change events using stratified random sampling (seed = 42). Stratification used structural change type as a proxy for diversity before classification was available.

| Structural type | Pool size | Floor | In sample |
|---|---|---|---|
| `desc_only` | 1,770 (71.3%) | 20 | 27 |
| `schema_add_only` | 135 (5.4%) | 12 | 12 |
| `desc_and_schema_add` | 182 (7.3%) | 10 | 11 |
| `schema_remove_only` | 65 (2.6%) | 8 | 8 |
| `desc_and_schema_remove` | 61 (2.5%) | 6 | 6 |
| `schema_mixed` | 166 (6.7%) | 6 | 6 |
| `type_change` | 102 (4.1%) | 5 | 5 |

Machine labels (P1 + P2) were kept in a separate file (`human_validation_machine.jsonl`) until after hand-labeling was complete, to prevent anchoring.

### Labeling process

Hand-labeling performed using `scripts/12_label_helper.py`, an interactive terminal tool displaying full before/after description text (with inline diff markers) and the full field list for each event. A printed reference legend was shown at session start and on demand (`?`).

Legend used during labeling:
- `COSMETIC` — wording/formatting only, meaning unchanged
- `CLARIFICATION` — explains EXISTING behavior more clearly; no new outcome possible
- `SCHEMA_EXPANSION` — new input fields added, same core purpose
- `SCHEMA_CONTRACTION` — input fields removed
- `BEHAVIORAL_DRIFT` — tool's actual scope/capability changed; a new kind of outcome is now possible that wasn't before

Tie-breaker test applied during labeling: *"Could the exact same tool-call, same parameters, have produced this result BEFORE the change?"* Yes → CLARIFICATION. No → BEHAVIORAL_DRIFT.

### E074 exclusion

Event E074 (`makafeli/n8n-workflow-builder`, `deactivate_workflow`, sampled as `desc_only`, 2025-07-29 → 2025-07-29) was confirmed as a cross-file collision artifact (L7 limitation). Both before and after snapshots were drawn from different source files (`index.ts` vs `server.ts`) at the same commit, and both files had empty schemas. The detected "description change" was a cross-file spatial difference, not a temporal change. E074 was intentionally excluded from labeling and from all Kappa computations. **All Kappa statistics are computed on n = 74 events.**

### Label distribution (n = 74)

| Label | Human | Pass 1 | Pass 2 |
|---|---|---|---|
| COSMETIC | 13 | 7 | 6 |
| CLARIFICATION | 13 | 17 | 14 |
| SCHEMA_EXPANSION | 24 | 24 | 23 |
| SCHEMA_CONTRACTION | 7 | 16 | 15 |
| BEHAVIORAL_DRIFT | 17 | 10 | 16 |

The most notable divergence: machine passes over-call `SCHEMA_CONTRACTION` (≈15–16 events) relative to the human (7 events), and under-call `BEHAVIORAL_DRIFT` (10–16) relative to the human (17). `SCHEMA_EXPANSION` is nearly perfectly calibrated across all three raters.

---

## 4. Inter-Rater Reliability (Kappa)

### Pairwise Cohen's Kappa and Fleiss' Kappa

| Comparison | κ | Landis & Koch band |
|---|---|---|
| Human vs Pass 1 | **0.605** | Substantial |
| Human vs Pass 2 | **0.638** | Substantial |
| Pass 1 vs Pass 2 | **0.826** | Almost perfect |
| Fleiss' κ (3-rater) | **0.687** | Substantial |

The P1 vs P2 value (0.826) confirms the Pass 2 bias fix succeeded: the two passes are nearly perfectly internally consistent, establishing classifier stability before comparing against the human ground truth.

### Confusion matrices

**Human (rows) vs Pass 1 (columns):**

```
              COS   CLA   EXP   CON   DRI
  COSMETIC      6     4     0     0     3
  CLARIF.       1    10     0     1     1
  EXPANSION     0     0    23     0     1
  CONTRACTION   0     0     0     7     0
  DRIFT         0     3     1     8     5
```

**Human (rows) vs Pass 2 (columns):**

```
              COS   CLA   EXP   CON   DRI
  COSMETIC      5     3     1     0     4
  CLARIF.       1    10     0     1     1
  EXPANSION     0     0    22     0     2
  CONTRACTION   0     0     0     7     0
  DRIFT         0     1     0     7     9
```

### Disagreement structure (n = 74)

| Agreement pattern | n | % |
|---|---|---|
| All three raters agree | 48 | 64.9% |
| Human = P1, P2 differs | 3 | 4.1% |
| Human = P2, P1 differs | 5 | 6.8% |
| P1 = P2, human differs (machine consensus) | 16 | 21.6% |
| Genuine 3-way split (all different) | 2 | 2.7% |

### Machine-consensus-wrong breakdown (16 events)

The dominant confusion pattern among the 16 events where both machine passes agreed but the human differed:

| Human → Machine confusion | n | Event structural types |
|---|---|---|
| BEHAVIORAL_DRIFT → SCHEMA_CONTRACTION | **7** | `desc_and_schema_remove`, `schema_remove_only` |
| COSMETIC → CLARIFICATION | 3 | `desc_only` |
| COSMETIC → BEHAVIORAL_DRIFT | 1 | `type_change` |
| CLARIFICATION → BEHAVIORAL_DRIFT | 1 | `type_change` |
| CLARIFICATION → COSMETIC | 1 | `desc_only` |
| CLARIFICATION → SCHEMA_CONTRACTION | 1 | `schema_remove_only` |
| SCHEMA_EXPANSION → BEHAVIORAL_DRIFT | 1 | `schema_mixed` |
| BEHAVIORAL_DRIFT → CLARIFICATION | 1 | `desc_only` |

The 7-case BEHAVIORAL_DRIFT → SCHEMA_CONTRACTION pattern is systematic: in all 7 cases, input fields were removed AND the description changed in a way that signals purpose shift. The machine pattern-matched on the structural removal signal and stopped at SCHEMA_CONTRACTION; the human applied the tie-breaker test and correctly identified the combined removal + description change as a scope change.

### Full-scale disagreement analysis and BEHAVIORAL_DRIFT headline

After the full 2,481-event run, the two dominant pass-level disagreement patterns (305 total, 12.3%) were:

| P1 → P2 | n | Description |
|---|---|---|
| CLARIFICATION → BEHAVIORAL_DRIFT | 88 | P2 escalates description-only changes P1 reads as explanatory |
| COSMETIC → BEHAVIORAL_DRIFT | 37 | P2 escalates description-only changes P1 reads as wording-only |

To assess which pass is better calibrated on these boundaries, 9 events were sampled from these two groups (5 from CLARIFICATION→BEHAVIORAL_DRIFT, 4 from COSMETIC→BEHAVIORAL_DRIFT) and reviewed against the raw before/after description text:

- **G2 #09 (`archive_gist`)** excluded as a confirmed data extraction artifact: the after-state description was truncated mid-sentence ("Archive a gist by"), not a real authorial change.
- **6 of the remaining 8 cases** supported P2's BEHAVIORAL_DRIFT call over P1's milder label. Representative examples: `modify_python_js_data_app` (SSH→HTTPS credential mechanism fully rewritten), `search_files` (algorithm changed from pattern-match to substring), `get_traffic_anomalies` (scope expanded to include outages), `attach_image_data_to_card` (detailed behavioral description collapsed to single use-case).
- **2 cases** were judged more consistent with P1's label: `search_cloudflare_documentation` (AutoRAG→AI Search is a product rename, not a scope change) and `get_token_info` ("and other metadata" removal is a vague phrase dropped, not a capability change).

**BEHAVIORAL_DRIFT headline (full 2,481-event run):**

| Estimate | Events | % | Basis |
|---|---|---|---|
| Conservative (both passes agree) | **309** | **12.5%** | Machine-consensus lower bound |
| Best estimate (P2) | **474** | **19.1%** | P2 better calibrated on desc-only boundary per follow-up review |
| Range | **309–474** | **12.5–19.1%** | Defensible bounds for paper reporting |

The 474 / 19.1% figure is the recommended point estimate for the paper. The 309 / 12.5% figure is the conservative lower bound and can be reported as such. The 6/8 qualitative support rate from the follow-up review provides the explicit justification for preferring P2 on description-only events.

### Cross-tabulation by repo dimension

**By star-count tier:**

| Star tier | Events | Repos | BD% (both) | BD% (P2) | Repos w/ ≥1 BD |
|---|---|---|---|---|---|
| 10–49 | 393 | 48 | 11.7% | 19.6% | 58.3% |
| 50–199 | 907 | 48 | 8.2% | 14.1% | 41.7% |
| 200–999 | 545 | 41 | 10.6% | 18.5% | 53.7% |
| 1000+ | 636 | 23 | 20.6% | 26.4% | 73.9% |

The 1000+ tier reads as highest-drift but is concentration-driven: mcp-atlassian (5,462 stars, 46.4% rate, 84 BD events) accounts for 50% of the tier's BD(P2) count alone. The very highest-star repos (langflow 150K at 6.2%, cline 64K at 26.9%, blender-mcp 23K at 3.1%) show rates at or below the overall average. There is no monotone relationship between star count and drift rate once repo identity is controlled for. The 50–199 tier has the lowest drift rate (14.1%) of any tier.

**By primary language:**

| Language | Events | Repos | BD% (both) | BD% (P2) | Repos w/ ≥1 BD |
|---|---|---|---|---|---|
| Python | 1,447 | 71 | 15.5% | 20.5% | 56.3% |
| TypeScript | 728 | 54 | 7.8% | 17.7% | 53.7% |
| JavaScript | 236 | 26 | 5.9% | 12.7% | 57.7% |
| Go | 43 | 4 | 30.2% | 41.9% | 50.0% |
| Rust | 11 | 1 | 0.0% | 0.0% | 0.0% |

Python's elevated rate (20.5%) is largely explained by mcp-atlassian (46.4% drift rate, 181 events); excluding it alone brings Python to 16.7%, within 1 point of TypeScript (17.7%). Three further high-drift Python repos (tasty-agent 26.3%, neo4j-contrib/mcp-neo4j 26.2%, container-mcp 39.1%) are notable outliers but are embedded within the already-reduced 16.7% base, not additive to closing the gap. The full cumulative effect of removing all four high-drift Python repos is shown below.

**Python BD(P2) rate — cumulative repo-exclusion decomposition (verified):**

| Exclusion set | Events | BD(P2) | BD(P2)% |
|---|---|---|---|
| All Python | 1,447 | 296 | 20.46% |
| − mcp-atlassian (181 events, 84 BD) | 1,266 | 212 | 16.75% |
| − also tasty-agent (114 events, 30 BD) | 1,152 | 182 | 15.80% |
| − also neo4j-contrib/mcp-neo4j (61 events, 16 BD) | 1,091 | 166 | 15.22% |
| − also 54rt1n/container-mcp (46 events, 18 BD) | 1,045 | 148 | 14.16% |

JavaScript's notably lower rate (12.7%) likely reflects that JavaScript MCP servers in this sample tend toward thinner wrappers with simpler, more stable description text. Go's 41.9% rate (4 repos, 43 events) is too small to interpret as a language-level finding.

**By source type (how repo was discovered):**

| Source | Events | Repos | BD% (both) | BD% (P2) | Repos w/ ≥1 BD |
|---|---|---|---|---|---|
| community | 623 | 46 | 19.1% | 23.8% | 41.3% |
| mined | 1,246 | 94 | 11.0% | 19.2% | 60.6% |
| official | 612 | 20 | 8.7% | 14.2% | 55.0% |

*"Official" = maintained by the tool's own vendor; "community" = third-party developer submission; "mined" = crawled from MCP registries. No thematic domain classification (finance/dev-tools/etc.) was available in the metadata.*

Official repos have the lowest drift rate (14.2% P2), community repos the highest (23.8%). The gradient is clean in both conservative and P2 estimates and represents the most structurally interpretable cross-tabulation finding: official repos, maintained by the tool's own vendor with more deliberate versioning processes, show markedly more stable MCP tool definitions than community-submitted repos.

**By structural change type:**

Computed from `tool_classifications.jsonl` under the conservative (both-passes-agree)
BEHAVIORAL_DRIFT definition used throughout this section, bucketed by the same
`structural_type` field the dashboard (`app.py`) uses (`type_change` takes priority;
pool sizes match the §3 stratification table). BD count is the number of events both
passes independently labeled BEHAVIORAL_DRIFT; rates are exactly what `app.py` displays.

| Structural type | Events (pool) | BD (both) | BD% (both) |
|---|---|---|---|
| `schema_mixed` | 166 | 137 | **82.5%** |
| `desc_and_schema_remove` | 61 | 33 | 54.1% |
| `type_change` | 102 | 25 | 24.5% |
| `desc_only` | 1,770 | 110 | 6.2% |
| `desc_and_schema_add` | 182 | 4 | 2.2% |
| `schema_add_only` | 135 | 0 | 0.0% |
| `schema_remove_only` | 65 | 0 | **0.0%** |
| **Total** | **2,481** | **309** | **12.5%** |

`schema_mixed` (simultaneous field additions and removals) shows the highest drift
rate by a wide margin; pure additive changes (`schema_add_only`, `desc_and_schema_add`)
show the lowest, consistent with L9's earlier finding that `schema_remove_only` events
show 0% BEHAVIORAL_DRIFT under the conservative definition. The 309 BD events sum
exactly to the conservative headline (12.5% of 2,481).

**Concentration:** The top 10 repos by BD(P2) count account for 55.9% of all 474 BD events (mcp-atlassian 84, keboola 36, tasty-agent 30, mcp-server-trello 22, container-mcp 18, docling-mcp 18, mcp-neo4j 16, kubernetes-mcp-server 16, cloudflare/ai 14, alibabacloud-hologres 11). BEHAVIORAL_DRIFT is concentrated in a subset of rapidly-evolving repos rather than distributed uniformly across the ecosystem.

---

## 5. Tiebreaker Fix for BEHAVIORAL_DRIFT vs SCHEMA_CONTRACTION

### Intervention

Following the Kappa analysis, both Pass 1 and Pass 2 BEHAVIORAL_DRIFT definitions were extended with an explicit tiebreaker sentence:

> *When input fields are removed AND the description simultaneously signals a change in the tool's scope or purpose, prefer BEHAVIORAL_DRIFT over SCHEMA_CONTRACTION.*

Pass 1 addition (inline continuation of the existing single-line definition):

```
- BEHAVIORAL_DRIFT: description signals the tool's actual function, scope, target 
  system, or side-effects changed — distinct from documentation quality. When input 
  fields are removed AND the description simultaneously signals a change in the tool's 
  scope or purpose, prefer BEHAVIORAL_DRIFT over SCHEMA_CONTRACTION.
```

Pass 2 addition (wrapped continuation matching Pass 2's multiline format):

```
  BEHAVIORAL_DRIFT  — description implies the tool's purpose, target system,
                      scope, or side-effects changed (not just documentation quality);
                      when input fields are removed AND the description simultaneously
                      signals a scope or purpose change, prefer this over SCHEMA_CONTRACTION
```

### Validation (13-event targeted test)

Re-classified all 7 systematic BEHAVIORAL_DRIFT misses plus 6 correctly-labeled SCHEMA_CONTRACTION guards using the updated prompts.

**Flip targets — expected BEHAVIORAL_DRIFT on both passes:**

| Event | Tool | Structural type | P1 | P2 | Result |
|---|---|---|---|---|---|
| E007 | add_comment | `schema_mixed` | BEHAVIORAL_DRIFT | BEHAVIORAL_DRIFT | ✓ corrected |
| E022 | search | `desc_and_schema_remove` | BEHAVIORAL_DRIFT | BEHAVIORAL_DRIFT | ✓ corrected |
| E026 | search_by_any | `desc_and_schema_remove` | BEHAVIORAL_DRIFT | BEHAVIORAL_DRIFT | ✓ corrected |
| E028 | update_sql_transformation | `desc_and_schema_remove` | **BEHAVIORAL_DRIFT** | SCHEMA_CONTRACTION | ✗ P2 persists |
| E047 | hubspot_search_data | `schema_remove_only` | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✗ irreducible |
| E053 | list_metrics | `schema_remove_only` | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✗ irreducible |
| E060 | load_skill | `desc_and_schema_remove` | BEHAVIORAL_DRIFT | BEHAVIORAL_DRIFT | ✓ corrected |

**Regression guards — expected SCHEMA_CONTRACTION on both passes:**

| Event | Tool | P1 | P2 | Result |
|---|---|---|---|---|
| E003 | update_config | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |
| E015 | create_sql_transformation | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |
| E033 | ListBuckets | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |
| E036 | create_email_draft | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |
| E039 | create_powerpoint_presentation | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |
| E062 | get_record | SCHEMA_CONTRACTION | SCHEMA_CONTRACTION | ✓ no regression |

**Summary: 4/7 flip targets corrected, 0/6 regressions.**

### Analysis of the 3 residual failures

**E047 (hubspot_search_data) and E053 (list_metrics)** — both are `schema_remove_only` with no description change. The tiebreaker rule requires "the description simultaneously signals a scope or purpose change" — without that signal, the rule cannot fire. Both passes correctly note (in their justifications) that there is no textual indication of scope change. The human's BEHAVIORAL_DRIFT judgment on these events was grounded in domain knowledge (removing `query`/`limit` eliminates the tool's search capability) rather than the description text. This is an irreducible limitation of text-only classification; no prompt change can resolve it without external knowledge.

**E028 (update_sql_transformation)** — this is a `desc_and_schema_remove` event where the description explicitly removed the "Deleting a transformation (delete=True)" usage instruction alongside the corresponding field removal. Pass 1 correctly applied the tiebreaker (BEHAVIORAL_DRIFT). Pass 2's justification framed the event as "reduction in capabilities → SCHEMA_CONTRACTION" — treating a feature removal as contraction rather than drift. This represents a single-pass borderline resist on a genuinely hard case; it is acceptable given P1 agreement with the human.

### Net effect on the SCHEMA_CONTRACTION / BEHAVIORAL_DRIFT boundary

The tiebreaker fix resolves the majority of the 7-case systematic miss, leaving 3 documented residual cases with clear explanations. The BEHAVIORAL_DRIFT undercount in the full 2,481-event output is now expected to be smaller; the residual downward bias in BEHAVIORAL_DRIFT counts (and corresponding upward bias in SCHEMA_CONTRACTION) applies only to events that are `schema_remove_only` with no accompanying description signal — a bounded and documentable subset.

---

## 6. Known Limitations (Phase 5)

### L7 — Degenerate same-SHA diff records (carried forward from Phase 4)

67 events with `from_sha == to_sha` were excluded before classification. These arise when a tool name appears in multiple source files at the same commit; the differ compares two simultaneous file-level definitions rather than a temporal change. See Phase 4 summary §5 L7.

### L8 — Empty-schema cross-file events in the valid 2,481

**53 events** in the 2,481 valid temporal change events have `source_file_changed=True` AND both before/after snapshot schemas empty (`inputSchema.properties = {}`). All 53 are `desc_only` structural type (no schema delta detected).

Unlike the L7 degenerate records, these have distinct `from_sha` and `to_sha` values and therefore passed the same-SHA filter. However, they share the same risk profile as E074: when a tool is defined in multiple source files and the snapshot index selects different source files for the before and after snapshots, the detected "description change" may reflect a cross-file spatial difference rather than a temporal authorial change within a single file.

The `_pick_snap()` field-matching heuristic (which selects the correct snapshot when multiple exist for the same `(repo_url, tool_name, sha8)`) cannot resolve these cases because both candidate snapshots have empty schemas — there are no field names to use as a tiebreaker.

These 53 events will be classified in the full run and assigned labels predominantly in `COSMETIC` or `CLARIFICATION`. They should be reported as a potential inflation source in the `desc_only` category counts; their BEHAVIORAL_DRIFT contribution is expected to be low (no schema signal, classifier tends to COSMETIC/CLARIFICATION for description-only changes).

Distribution across repos:

| Repo | Count |
|---|---|
| cline | 86 total* |
| cloudflare/ai | 41 total* |
| kubernetes-mcp-server | 36 total* |
| n8n-workflow-builder | ~31 |
| mcp-server-cloudflare | ~26 |
| learn-agentic-ai | ~21 |
| (others) | < 15 each |

*Total includes records not in the valid 2,481 (i.e., non-drift_event or same-SHA records).

**Count in valid 2,481: 53.**

### L9 — Residual SCHEMA_CONTRACTION / BEHAVIORAL_DRIFT boundary error

For events that are `schema_remove_only` (no description change), the classifier cannot distinguish a benign interface contraction from a capability-removal behavioral change without domain knowledge not present in the tool text. These events will be labeled `SCHEMA_CONTRACTION` by both passes regardless of actual behavioral impact. The magnitude of this bias is bounded by the proportion of `schema_remove_only` events in the 2,481-event set (65 events, 2.6% of the valid changed set). BEHAVIORAL_DRIFT counts in the full output should be treated as a conservative lower bound.

### L10 — Tutorial/educational repository chapter-collision

Tutorial and educational repositories (e.g., `mahm/softwaredesign-llm-application`) organize example code into numbered chapter directories within a single git repository (e.g., `20/src/`, `29/src/`). A tool with the same name defined differently across two chapters can be misread by the differ as one tool's temporal evolution — the differ sees two distinct `from_sha` and `to_sha` values and a genuine description or schema change, so it passes both the same-SHA filter (L7) and the empty-schema filter (L8). This collision type is detectable only by manual inspection of the `source_file` path for a chapter-numbering pattern; no automated check currently catches it. The one confirmed instance (`search_web`, `softwaredesign-llm-application`, chapter 20 vs. chapter 29) was identified during Part 3 case selection and excluded. The number of additional tutorial-repo collisions in the full 2,481-event set is unknown. **Recommended future work:** flag any repo whose tool source files include paths matching a `/<two-or-three-digit-number>/` directory component as a risk factor for this collision type, and exclude or manually review those events.

---

## 7. Files

| File | Description |
|---|---|
| `scripts/10_llm_classifier.py` | Two-pass LLM classifier; `--batch N --seed-events PATH` for test mode |
| `scripts/11_human_validation.py` | Stratified sample generator and Kappa computation (`--compute-kappa`) |
| `scripts/12_label_helper.py` | Interactive terminal labeling helper |
| `data/processed/human_validation_sample.csv` | 75-event stratified sample; 74 hand-labeled, E074 blank |
| `data/processed/human_validation_machine.jsonl` | Machine labels (P1 + P2) for the 75-event sample |
| `data/processed/human_validation_seeds.json` | Event coordinates for the 75-event sample (reproducible, seed=42) |
| `data/processed/human_validation_merged.csv` | Human + machine labels merged; generated by `--compute-kappa` |
| `data/processed/tool_classifications.jsonl` | Full 2,481-event classification output (2,481 events, 0 errors, 87.7% pass agreement) |

---

## 8. Status Checklist

- [x] Two-pass classifier implemented (`scripts/10_llm_classifier.py`)
- [x] Pass 2 severity bias discovered (20-event test), redesigned, and re-validated
- [x] 75-event stratified human validation sample generated (seed=42)
- [x] Machine labels generated for all 75 events (P1 + P2)
- [x] 74 events hand-labeled; E074 excluded as L7 cross-file artifact
- [x] Kappa computed: Human/P1 κ=0.605, Human/P2 κ=0.638, P1/P2 κ=0.826, Fleiss κ=0.687
- [x] BEHAVIORAL_DRIFT / SCHEMA_CONTRACTION tiebreaker added to both prompts
- [x] Tiebreaker validated: 4/7 corrected, 0/6 regressions, 3 residual cases documented
- [x] L8 limitation identified: 53 empty-schema cross-file events in valid 2,481
- [x] Full 2,481-event classification run complete (2,481 events, 0 errors, 87.7% pass agreement)
- [x] BEHAVIORAL_DRIFT headline decided: 309 conservative / 474 best estimate (range 309–474, P2=474 recommended)
- [x] 9-event follow-up review: 6/8 support P2 on desc-only boundary; archive_gist excluded as truncation artifact
- [x] Cross-tabulation by repo star tier, language, and repo type (see §4)
- [x] Phase 4 summary §6 updated to reflect final classifier status
