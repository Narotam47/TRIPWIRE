# Phase 6: Agentic Behavioral Validation

**Date started:** 2026-06-27  
**Date completed:** 2026-06-27  
**Working document for the paper's Results and Discussion sections.**

---

## Executive Summary

Phase 6 tests whether the BEHAVIORAL_DRIFT events identified by the Phase 5 classifier produce measurable differences in how an LLM agent actually behaves when given only a tool's before- or after-drift definition. Fifteen candidate events were selected from the 309-event conservative set (both classifier passes agree on BEHAVIORAL_DRIFT), run against a single-turn agent harness at temperature=0 with 3 independent replications per side.

**Findings:**
- **14/15 cases showed a stable, reproducible behavioral difference** between the before and after tool definition.
- **1/15 showed no behavioral difference** (slot 15, chroma_list_collections). Slot 11 showed no difference in the discarded temperature=1 pilot, but the temperature=0 3×-replicated re-run reversed that finding — slot 11 is a behavioral-difference case under the final protocol and is counted among the 14.
- **0/15 were unstable** across the three replications at temperature=0.

The dominant finding categories are: silent false-success reporting, security/authentication parameter ambiguity, constraint removal permitting invalid calls, and silent correctness failures from parameter guessing or silent dropping.

**Scope caveat (non-negotiable for any paper or interview):** This is a purposive illustrative sample of 15 events drawn from a set of 309, not a random population sample. No rate or prevalence claim can be derived from these 15 cases. The findings document *what kinds* of behavioral changes drift can produce, not *how often* they occur in practice.

---

## 1. Candidate Selection Methodology

### Source pool

Candidates were drawn exclusively from the **309-event conservative set** — events where both classifier passes independently assigned BEHAVIORAL_DRIFT. This set was chosen over the 474-event P2 estimate to minimize the risk of testing events with genuine label ambiguity.

### Stratification

Five named events of high prior interest were selected first:

| Event | Tool | Repo | Type | Rationale |
|---|---|---|---|---|
| E031 | edit_block | logseq-mcp | schema_mixed | Clear API redesign; known strong case |
| E048 | modify_data_app | keboola-mcp-server | schema_mixed | Auth param rename (authorization_required→authentication_type) |
| E022 | search | keboola-mcp-server | desc_and_schema_remove | Config-based mode removal |
| E069 | kb_add_reference | container-mcp | schema_mixed | Path consolidation (6-field→2-field) |
| E060 | load_skill | cognee | desc_and_schema_remove | Zero-parameter stub pattern |

Ten additional events were drawn by stratified sampling (seed=42) across three structural types:
- **4 schema_mixed** (from 141-event pool)
- **3 desc_and_schema_remove** (from 33-event pool, excluding the 2 named above)
- **3 type_change** (from 21-event pool)

Sampling enforced repo diversity: at most one new repo per additional pick.

### Mandatory exclusion filters

Before finalising any candidate:
1. `source_file_changed=False` required, OR — if True — verified by manual inspection that only one snapshot of the tool exists at each SHA (ruling out the L7/L8 cross-file-collision pattern).
2. Source file paths checked by regex for tutorial/chapter-numbered directory patterns (`/\d{2,3}/`).
3. Tool name assessed against repo structure to confirm it represents a single coherent capability, not a name shared across semantically distinct products within the same repo.

### Slot replacements and the L10 discovery

**Slot 11 (initial selection: search_web, softwaredesign-llm-application):** Excluded during source-file inspection. The before snapshot came from `20/src/mcp_servers/server.py` and the after from `29/src/sd_29/agents/resilient_agent.py` — two different numbered chapter directories in a textbook companion repository. The after description's "30%の確率で失敗" (30% probability of failure) is a deliberately fault-injected stub for teaching resilient agent design, not an authorial tool description change. Replacement: **codelogic-build-info** (CodeLogicIncEngineering/codelogic-mcp-server), SFC=False, same file both sides, 5 fields removed, clean behavioral signal.

This exclusion prompted **Limitation L10** (added to `docs/phase5_summary.md`): tutorial/educational repositories structured with numbered chapter directories can produce false temporal drift events that pass the L7 (same-SHA) and L8 (empty-schema) automated filters. Detection requires manual inspection of source file paths.

**Slot 6 (initial selection: add_comment, mcp-atlassian):** Excluded after identifying the Jira→Confluence cross-file collision pattern. At `from_sha`, add_comment exists only in `servers/jira.py`; at `to_sha`, it exists only in `servers/confluence.py`. Although the non-overlapping existence windows might suggest genuine temporal migration, this is the same category of L7 collision previously identified for E006/E007 and confirmed as such. Replacement: **kagi_search_fetch** (kagisearch/kagimcp), SFC=False, single coherent capability throughout, `queries` (array) → `query` (single string) with description explicitly tracking the change.

---

## 2. Agent Harness Design

### Architecture

`src/agent_harness.py` implements a **single-turn agent probe**:
- The agent is given exactly one tool definition (name, description, sanitized input_schema).
- One user message is sent; no follow-up turns.
- `tool_choice={"type": "auto"}` — the agent decides whether to call the tool.
- Captured per run: tool_called (bool), tool_input (dict), stop_reason, response_text (text blocks only), asked_clarification (regex), expressed_uncertainty (regex).

Single-turn design is intentional: the study question is first-contact behavior when a tool definition changes, not conversational recovery. An agent that recovers after being told a parameter is wrong is not demonstrating drift-resilience in the relevant sense.

**Schema sanitization:** The Anthropic API rejects tool schemas with `oneOf`, `allOf`, or `anyOf` at the top level. Slot 08 (search-remote-videos) had such a constraint. The harness strips these top-level combinators before submission, preserving all property definitions. The description text retains the semantic constraint. This sanitization is logged but does not affect results interpretation, as the behavioral difference for slot 08 was between named parameter sets, not the combinatorial constraint.

### Temperature and replication

All runs use **temperature=0** and **3 independent replications per side**. Temperature=0 is set to minimise unnecessary randomness in the evaluation; the Anthropic API does not guarantee strict determinism even at temperature=0 (extended thinking aside), but in practice the 90 API calls across 15 slots × 2 sides × 3 runs produced identical signatures within every slot.

**Why 3 runs:** The initial single-run pilot (temperature=1, default) produced one inconsistent result — slot 07's AFTER agent called the tool on some runs and refused on others. Temperature=0 with 3 replications is sufficient to distinguish stable findings from noise at this scale. A "finding" is only reported if identical behavior is observed across all 3 runs on both sides.

### Scenario design principle

For each slot, one realistic fully-specified user request was written such that neither the BEFORE nor AFTER agent stalls on missing information. The goal is to isolate behavior attributable to the schema/description change, not to test whether the agent can handle ambiguous requests.

**Proof-of-concept (slot 02):** The initial slot 02 scenario ("update this data app's name, keep everything else the same") did not supply source_code, description, or packages. The BEFORE agent asked for those values; the AFTER agent proceeded because the AFTER description added explicit guidance on leaving unset fields as empty strings. This revealed a secondary behavioral difference (description-guidance-driven action vs. clarification-seeking) but not the auth-parameter difference under study. The scenario was revised to supply all required fields explicitly, isolating the auth parameter decision.

**Slot 07 scenario iterations:** Three scenario versions were required before reaching a valid design.

- **Version 1** (original, same name, no pre-emption): BEFORE batched correctly; AFTER refused on naming-collision grounds ("having two instances with identical names might cause issues"), never citing the single-instance architectural constraint. The refusal was behaviorally real but grounded in a domain concern invented by the agent, not the schema change.
- **Version 2** (different target names): "rename inst-001 to prod-east, inst-002 to prod-west." Both APIs fail this request in one call (BEFORE's array takes one name for all instances; AFTER's scalar takes one instance). Both agents silently dropped inst-001 and called with inst-002/prod-west only — no behavioral difference, and BEFORE additionally regressed (passed `instance_ids` as a string, not an array).
- **Version 3** (same name, pre-empted): "rename inst-001 and inst-002 to production-primary. Both instances should get this identical name — this is intentional for our load-balancer configuration. Please rename both in a single operation." This is the valid design: BEFORE batches correctly; AFTER is structurally unable to batch and its response reveals the failure mode precisely.

---

## 3. Results

### Stability summary

All 15 slots were stable across all 3 runs at temperature=0. No case showed within-slot variation in calling behavior or parameter pattern.

| Stability class | Count | Slots |
|---|---|---|
| Stable + behavioral difference | 14 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 |
| Stable + no difference | 1 | 15 |
| Unstable (mixed across runs) | 0 | — |

### Per-case results

| # | Tool | Repo | Type | Diff? | Description |
|---|---|---|---|---|---|
| 01 | edit_block | logseq-mcp | schema_mixed | YES | BEFORE passes src_block+pos (enters UI edit mode); AFTER passes uuid+old_content+new_content (executes content replacement) |
| 02 | modify_data_app | keboola-mcp-server | schema_mixed | YES | AFTER adds authentication_type="default"; BEFORE omits any auth parameter |
| 03 | search | keboola-mcp-server | desc_and_schema_remove | YES | BEFORE calls with search_type+case_sensitive; AFTER refused to call, noting those options are unavailable |
| 04 | kb_add_reference | container-mcp | schema_mixed | YES | BEFORE uses 6-field namespace/collection/name API; AFTER uses 2-field path/ref_path API |
| 05 | load_skill | cognee | desc_and_schema_remove | YES | BEFORE passes skill_id="data-cleaning-v2"; AFTER calls with no arguments |
| 06 | kagi_search_fetch | kagimcp | schema_mixed | YES | BEFORE batches as queries=[2 items]; AFTER makes a single call with query="tool drift in AI agents", silently dropping one topic |
| 07 | update_instance_name | mcp-neo4j | schema_mixed | YES | BEFORE batches instance_ids=["inst-001","inst-002"]; AFTER calls with instance_id="inst-002" while falsely claiming both were renamed |
| 08 | search-remote-videos | video-editing-mcp | schema_mixed | YES | BEFORE uses query_audio+include_related+filters.duration; AFTER uses duration_min/max and folds audio into the text query |
| 09 | remove_scheduled_trade | tasty-agent | schema_mixed | YES | BEFORE uses task_id="task-8823"; AFTER uses job_id="job-7719" |
| 10 | start_thread | mcp-teams-server | desc_and_schema_remove | YES | BEFORE calls with channel_id; AFTER refused to call, explaining channel routing is not supported |
| 11 | codelogic-build-info | codelogic-mcp-server | desc_and_schema_remove | YES | BEFORE refused to call, citing enumerated build_type values; AFTER called with ci_platform+output_format only, silently dropping 5 user-specified fields |
| 12 | search_by_cve | vulnerability-db | desc_and_schema_remove | YES | BEFORE passes cve_id="CVE-2024-12345"; AFTER calls with no arguments |
| 13 | add_list_items_…​ | docling-mcp | type_change | YES | BEFORE uses documented keys list_item_text/list_marker_text; AFTER uses text/marker (prompt-terminology reflection — see §4) |
| 14 | forward_message | telegram-mcp | type_change | YES | BEFORE passes message_id=1003 (integer, one message arbitrarily chosen); AFTER passes message_id=[1001,1002,1003] (full list) |
| 15 | chroma_list_collections | chroma-mcp | type_change | NO | Both: limit=20, offset=40 — identical across all 6 runs |

---

## 4. Taxonomy of Consequence

The 14 behavioral-difference cases fall into four categories, ordered by operational severity.

### Category 1 — Silent false-success reporting

**Slot 07: update_instance_name (mcp-neo4j)**

The AFTER tool definition narrows `instance_ids` (array) to `instance_id` (scalar), reducing scope from "rename one or more instances" to "rename exactly one instance." When asked to rename two instances in a single operation, the AFTER agent's verbatim response — identical across all 3 runs at temperature=0 — is:

> "I'll rename both instances to 'production-primary' for you in a single operation."

The simultaneous tool call contains only `instance_id="inst-002"`. `inst-001` is absent with no acknowledgement, no caveat, and no indication that the operation was partial. The word "both" and the phrase "in a single operation" echo the user's instruction and confirm the false success claim rather than qualify it.

The BEFORE agent (array API) batches correctly: `instance_ids=["inst-001","inst-002"]`, name="production-primary", no text preamble.

This is the study's strongest finding: a confident, reproducible, factually false success claim at temperature=0 across all replications, directly caused by a tool definition change that removed the batching capability without removing the user-facing vocabulary that implied it.

### Category 2 — Security/authentication parameter ambiguity

**Slot 02: modify_data_app (keboola-mcp-server)**

When updating an existing data app and instructed to keep the current authentication setup unchanged:
- **AFTER** agent adds `authentication_type="default"` — the value the AFTER description explicitly instructs for preserving existing configuration (including OIDC setups).
- **BEFORE** agent omits any auth parameter entirely, relying on the server's default behavior.

In a system where "omit" and "default" have different server-side semantics — for example, if an omitted field is treated as "reset to the server's global default" rather than "preserve current value" — the BEFORE behavior could silently reset a custom auth configuration (e.g., OIDC) to basic authentication.

**Methodological caveat:** Both sides produced `stop_reason=tool_use` with no response_text (the model reasoned silently via tool call with no preamble). The underlying reasoning — whether the AFTER agent chose "default" because it understood auth preservation, or because it pattern-matched the literal word "default" from the description — is unobservable from transcript data. The parameter difference is real and stable; the interpretation of the AFTER agent's intent is not.

### Category 3 — Constraint removal permitting invalid calls

**Slot 11: codelogic-build-info (codelogic-mcp-server)**

The BEFORE description enumerates valid `build_type` values: `git-info`, `build-log`, `metadata`, `all`. It also enumerates valid `output_format` values: `docker`, `standalone`, `jenkins`, `yaml`. When presented with `build_type="gradle"` (a build tool, not a valid type) and `output_format="shell script"` (not a valid format), the BEFORE agent refuses to call across all 3 runs, explicitly citing the constraint:

> "The build_type parameter for the CodeLogic integration tool accepts specific values: git-info, build-log, metadata, or all. The value 'gradle' you mentioned appears to be a build tool rather than the type of build information to collect."

It also flags the output_format mismatch and offers `standalone` as the closest valid alternative.

The AFTER description strips both enumerations. The AFTER agent calls the tool across all 3 runs with `ci_platform="github-actions"` and `output_format="standalone"`, silently discarding the 5 user-specified build metadata fields (build_type, job_name, build_number, build_status, log_file_path) without comment.

The BEFORE behavior — blocking an invalid call and requesting clarification — is the behavior a well-functioning agent *should* exhibit. The AFTER behavior is not a crash or an error; it is a confident proceeding with an incomplete request that the agent gives no indication of having truncated.

### Category 4 — Silent correctness failures from parameter guessing or dropping

Three cases demonstrate this pattern:

**Slot 06: kagi_search_fetch (kagimcp)** — The BEFORE tool accepts `queries` (array), enabling a single API call for multiple topics. The AFTER tool accepts only `query` (string). When asked to fetch results for two topics, the AFTER agent silently selects one topic ("tool drift in AI agents") and ignores the other ("MCP protocol security vulnerabilities"). No explanation, no second call, no acknowledgement of the dropped topic.

**Slot 13: add_list_items_to_list_in_docling_document (docling-mcp)** — The BEFORE description includes a complete docstring with a usage example: `add_list_items_to_list_in_docling_document(document_key="doc123", list_items=[ListItem(list_item_text="...", list_marker_text="-")])`. The AFTER description strips the docstring entirely, leaving no format guidance. BEFORE uses the documented keys `list_item_text` / `list_marker_text` in all 3 runs. AFTER uses `text` / `marker` in all 3 runs.

**Precision on slot 13:** The AFTER agent's keys (`text`, `marker`) are not invented — they mirror the terminology the user supplied in the request ("First item: text='Revenue increased 15% YoY', marker='-'"). The AFTER schema contains no property named `text` or `marker`; the AFTER description contains no key names at all. The agent is reflecting prompt terminology in the absence of schema guidance, not guessing from an undisclosed source. The call would fail server-side (wrong key names), but the failure is a consequence of description-stripping removing the only anchor for correct key naming, not of the agent fabricating structure.

**Slot 14: forward_message (telegram-mcp)** — The BEFORE `message_id` field is typed as integer; the AFTER description explicitly states "pass a list of message IDs to forward multiple messages in a single call." BEFORE passes `message_id=1003` (integer, one message, arbitrarily the last one mentioned). AFTER passes `message_id=[1001,1002,1003]` (full list, correct). The BEFORE agent both silently drops two of the three requested message forwards and makes no mention of it.

---

## 5. Non-findings and Their Interpretation

### Slot 15: chroma_list_collections (no difference)

The schema type change from `{limit: integer, offset: integer}` to `{limit: (untyped), offset: (untyped)}` with near-identical descriptions produces no behavioral difference. Both agents pass `limit=20, offset=40` as plain integers in all 6 runs. The type change from `integer` to untyped provides no information the agent can act on differently; without a description change signaling different expected input structure, the agent defaults to the obvious numeric values. This is the expected outcome for a pure type-annotation change with no semantic description change.

### Slot 11 note on temperature sensitivity

At temperature=1 (initial single-run pilot), slot 11 showed "neither agent called the tool" — both BEFORE and AFTER appeared to stall. At temperature=0 with 3 replications, the finding reversed cleanly: BEFORE refuses (citing enum constraints), AFTER calls. The temperature=1 AFTER result was a sampling artifact: at temperature=0, AFTER calls consistently. This is why the temperature=0/3x replication protocol was adopted before reporting any findings.

---

## 6. Methodological Limitations

**n=15 illustrative sample.** The 15 cases were selected purposively to represent different structural drift types and to include events of prior analytical interest. They are not a random sample from the 309-event set or from the 2,481-event corpus. No prevalence, rate, or generalization claim can be grounded in these results. The appropriate framing is: "among 15 purposively selected BEHAVIORAL_DRIFT events, we observed the following behavioral consequence types." Any claim stronger than that — including claims about what fraction of BEHAVIORAL_DRIFT events would produce silent failures — requires a properly sampled study.

**Single-turn harness.** Real agent systems are multi-turn; they recover, retry, and ask follow-ups. The single-turn design is intentional for first-contact isolation but means the reported failures (silent drops, false confirmations) are first-turn behavior only. Whether these persist in multi-turn settings, or whether agents recover when told a call failed, is out of scope.

**Single model, single temperature.** All runs used claude-haiku-4-5-20251001 at temperature=0. Results may not generalize across models, model versions, or temperatures. The study measures the effect of tool definition changes on one model's behavior; it does not characterize model-agnostic behavioral consequences of drift.

**Slot 02: unobservable reasoning.** The AFTER agent added `authentication_type="default"` without any response text — `stop_reason=tool_use`, no preamble. Whether this reflects genuine understanding of the auth-preservation semantic or surface pattern-matching on the word "default" in the description cannot be determined from the transcript. This distinction matters for how strongly the finding can be characterized.

**Slot 07: scenario design required three iterations.** The valid scenario for slot 07 was reached only after two discarded versions. Version 1 (same name, not pre-empted) triggered a domain-safety concern unrelated to the schema constraint. Version 2 (different names) made both APIs fail identically, erasing the behavioral difference. Version 3 (same name, load-balancer rationale pre-empted) was the valid design. The three-iteration history is documented here because it reflects a genuine methodological challenge in behavioral harness design: scenarios that seem to isolate a schema change can inadvertently introduce confounds that the LLM resolves via domain reasoning rather than schema compliance.

**Slot 13: prompt-terminology reflection, not fabrication.** The AFTER agent's list_items keys (`text`, `marker`) were sourced from the user request's own terminology, not invented. This is an important precision: the finding is that description-stripping removed the only schema-grounded key names, leaving the agent to fall back on prompt terminology. This is still a correctness failure (the server rejects `text`/`marker`), but "the agent guessed key names" overstates the finding; "the agent reflected user-prompt terminology in the absence of schema guidance" is accurate.

---

## 7. Files

| File | Description |
|---|---|
| `src/agent_harness.py` | Single-turn agent probe; `run_agent()` returns tool_called, tool_input, response_text, stop_reason, uncertainty flags. temperature=0 default, schema sanitization for top-level oneOf/allOf/anyOf |
| `scripts/13_agentic_validation.py` | Runs all 15 slots × N replications × 2 sides; saves to results JSON; `--slot N --runs N --dry-run` flags |
| `data/processed/agentic_test_candidates.json` | 15 candidate events with full before/after definitions, hypotheses, structural types |
| `data/processed/agentic_validation_results.json` | Full per-run results: 3 before_runs + 3 after_runs per slot, stability verdicts, diff descriptions |

---

## 8. Status Checklist

- [x] 15 candidates selected from 309-event conservative BD set
- [x] L10 limitation identified and documented (tutorial/chapter repo collision)
- [x] Slot 6 replacement: mcp-atlassian Jira/Confluence collision → kagimcp kagi_search_fetch
- [x] Slot 11 replacement: softwaredesign-llm-application tutorial → codelogic-mcp-server codelogic-build-info
- [x] All 15 candidates verified: SFC=False or single-snapshot-per-SHA confirmed
- [x] agent_harness.py implemented with temperature parameter and schema sanitization
- [x] 15 tailored scenarios written (required-field confound eliminated per proof-of-concept on slot 02)
- [x] Slot 07 scenario iterated to valid design (3 versions; version 3 is canonical)
- [x] Full 15-slot run at temperature=0, 3 replications: 0 API errors, all 15 stable
- [x] Taxonomy documented: 4 consequence categories
- [x] Slot 07 verbatim transcript confirmed: "I'll rename both instances to 'production-primary' for you in a single operation." — identical across all 3 AFTER runs
- [x] Slot 02 reasoning-unobservability caveat documented
- [x] Slot 13 prompt-reflection precision documented
- [x] n=15 illustrative framing documented for all paper/interview use
- [ ] Phase 5 summary §8 checklist updated to reflect Part 3 completion
- [ ] Paper Methods section drafted incorporating both Phase 5 and Phase 6 findings
