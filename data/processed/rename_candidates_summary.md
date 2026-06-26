# Tool Naming-Convention Evolution: Supplementary Observation

## Status

This is a **secondary, lower-bound empirical observation** about tool naming-convention
evolution in the corpus. It is distinct from the primary rug-pull drift measurement
(description and schema changes tracked in `tool_diffs.jsonl`).

The primary differ groups snapshot records by `(repo_url, tool_name)` — by design,
it does not capture transitions where a tool is renamed between consecutive commits.
A rename produces two separate tool-name groups in the primary data: the old name ends,
the new name begins. The `is_inplace_mutation` flag in every `drift_event` record
reflects this: it is structurally `True` for all primary diff records because each
record lives within a single name-stable group.

The 688 candidates in `rename_candidates.csv` were found via a supplementary
file-level scan: for every `(repo_url, source_file)` pair, consecutive commits where
the tool-name set changed were tested for similarity (schema Jaccard ≥ 0.5 and
description Jaccard ≥ 0.3). The count is a **lower bound** — it is constrained by
the tools and files the history walker tracked, and by the similarity thresholds chosen.

## Findings

| Confidence tier | Criterion | Candidates |
|---|---|---|
| **Perfect** | schema_jaccard = 1.0 AND desc_jaccard = 1.0 | 326 |
| **High** | schema_jaccard = 1.0, desc_jaccard < 1.0 | 293 |
| **Medium** | schema_jaccard < 1.0 | 69 |
| **Total** | | **688** |

- **Repos affected**: 71 of 276 walked repos (25.7%)
- **High-confidence cases** (schema fully preserved): 619

## Top Repos by Rename Count

| Rename candidates | Repository |
|---|---|
| 117 | feiskyer/mcp-kubernetes-server |
| 66 | mcpdotdirect/evm-mcp-server |
| 66 | krzko/google-cloud-mcp |
| 59 | keboola/keboola-mcp-server |
| 46 | aliyun/alibabacloud-hologres-mcp-server |

## Verified Examples

**Example 1 — perfect rename (schema and description identical):**
```
Repo:  chaindead/telegram-mcp  (serve.go)
Date:  2025-04-02 → 2025-04-03
Old:   'dialogs'
New:   'tg_dialogs'
Schema: []  (unchanged)
Desc:  'Get list of dialogs (chats, channels, groups)'  (identical)
```
Interpretation: tool prefixed with namespace (`tg_`) without any behavior change.

**Example 2 — name rename with minor description update (schema intact):**
```
Repo:  2b3pro/roam-research-mcp  (src/tools/schemas.ts)
Date:  2025-01-13
Old:   'roam_create_output_with_nested_structure'
New:   'roam_create_outline'
Schema: []  (unchanged, desc_jaccard = 0.971)
Old desc: 'Create a structured outline or output with nested structure in Roam...'
New desc: 'Create a structured outline with nested structure in Roam...'
```
Interpretation: tool simplified/shortened in the same commit the description was cleaned up.

## Methodological Note

These cases are **not captured** in `tool_diffs.jsonl` and do not inflate or deflate
the primary drift counts. Tools that were renamed appear as two entries in the
primary data: an "old" tool whose history ends, and a "new" tool whose history begins.
The 326 perfect renames represent a form of drift (naming-convention change) that is
invisible to description-change and schema-change metrics.

Whether to link rename chains in future work (e.g., tracking `dialogs` → `tg_dialogs`
as a single tool lineage) is left as future work. The data to do so is in
`rename_candidates.csv`.

## Files

- `rename_candidates.csv` — all 688 candidates with fields:
  `repo_url`, `source_file`, `commit_from`, `commit_to`, `date_from`, `date_to`,
  `old_tool_name`, `new_tool_name`, `schema_jaccard`, `desc_jaccard`,
  `confidence_tier`, `old_description`, `new_description`,
  `old_schema_props`, `new_schema_props`
