# Sample Stratification: Planned vs. Achieved

**Sampling frame**: GitHub MCP server repositories, stratified by primary programming language × GitHub star count bucket (10–49, 50–199, 200–999, 1 000+).  
**Target**: 380 repositories.  **Achieved**: 364 repositories with at least one extractable tool definition (95.8%).

---

## Summary table

| Language | Stars | Planned | Achieved | Δ | Primary OK | Backup (same stratum) | Backup (cross-bucket) | Unfilled |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| C++ | 200–999 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Go | 10–49 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Go | 50–199 | 3 | 3 | 0 | 3 | 0 | 0 | 0 |
| Go | 200–999 | 2 | 2 | 0 | 2 | 0 | 0 | 0 |
| Go | 1 000+ | 3 | 3 | 0 | 3 | 0 | 0 | 0 |
| HTML | 10–49 | 2 | 2 | 0 | 1 | 1 | 0 | 0 |
| **HTML** | **200–999** | **1** | **0** | **−1** | 0 | 0 | 0 | 1 |
| JavaScript | 10–49 | 20 | 20 | 0 | 20 | 0 | 0 | 0 |
| JavaScript | 50–199 | 16 | 14 | **−2** | 13 | 1 | 0 | 2 |
| JavaScript | 200–999 | 12 | 12 | 0 | 11 | 1 | 0 | 0 |
| JavaScript | 1 000+ | 4 | 4 | 0 | 3 | 0 | 1 | 0 |
| Jupyter Notebook | 10–49 | 2 | 2 | 0 | 1 | 1 | 0 | 0 |
| Jupyter Notebook | 200–999 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Jupyter Notebook | 1 000+ | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Python | 10–49 | 40 | 40 | 0 | 34 | 6 | 0 | 0 |
| Python | 50–199 | 42 | 42 | 0 | 36 | 6 | 0 | 0 |
| Python | 200–999 | 29 | 29 | 0 | 22 | 7 | 0 | 0 |
| Python | 1 000+ | 19 | 19 | 0 | 14 | 5 | 0 | 0 |
| Ruby | 200–999 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Rust | 50–199 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| Rust | 200–999 | 2 | 2 | 0 | 2 | 0 | 0 | 0 |
| **Rust** | **1 000+** | **2** | **1** | **−1** | 1 | 0 | 0 | 1 |
| Scala | 10–49 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| TypeScript | 10–49 | 42 | 42 | 0 | 29 | 7 | 6 | 0 |
| TypeScript | 50–199 | 50 | 47 | **−3** | 32 | 14 | 1 | 3 |
| TypeScript | 200–999 | 37 | 37 | 0 | 29 | 7 | 1 | 0 |
| TypeScript | 1 000+ | 35 | 35 | 0 | 28 | 7 | 0 | 0 |
| VBScript | 10–49 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| *(undetected)* | 10–49 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| *(undetected)* | 50–199 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| *(undetected)* | 200–999 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| *(undetected)* | 1 000+ | 2 | 0 | **−2** | 0 | 0 | 0 | 2 |
| C# | 1 000+ | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| Astro | 50–199 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| Dockerfile | 10–49 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| Haskell | 50–199 | 1 | 0 | **−1** | 0 | 0 | 0 | 1 |
| **Total** | | **380** | **364** | **−16** | 292 | 63 | 9 | 16 |

*"Backup (same stratum)" = backup repo drawn from the identical language × star-bucket cell. "Backup (cross-bucket)" = backup drawn from a different star-bucket within the same language (see §Cross-bucket promotions below). "Unfilled" = no backup with ≥1 extractable tool was available for that slot.*

---

## Strata where achieved < planned

### 1. Niche single-repo strata — no backups available (−9 slots)

Eight planned slots each occupied a unique language stratum that contained only one primary candidate and had no backup pool entries. All eight primaries were either misclassified (not a server, not a MCP implementation) or structurally inextractable, leaving the slot permanently unfilled.

| Stratum | Primary repo | Reason |
|---|---|---|
| C# / 1 000+ | justinpbarnett/unity-mcp | `zero_tools_found`; C# extractor not implemented; 0 C# backups existed |
| Astro / 50–199 | jiangtao/blog | `not_a_server`; tutorial blog misclassified; no Astro backups |
| Dockerfile / 10–49 | explorium-ai/mcp-explorium | `not_a_server`; remote-hosted service; no Dockerfile backups |
| Haskell / 50–199 | hungryrobot1/MCP-PIF | `not_a_server`; no Haskell backups |
| HTML / 200–999 | AI-QL/chat-mcp | `not_a_server`; no HTML/200–999 backups |
| *(undetected)* / 10–49 | mcp-server-raygun | `not_a_server`; remote-hosted; GitHub language undetected |
| *(undetected)* / 50–199 | IBM/wxflows | `not_a_server`; dynamic proxy; GitHub language undetected |
| *(undetected)* / 200–999 | apappascs/mcp-servers-hub | `not_a_server`; curation list; GitHub language undetected |
| *(undetected)* / 1 000+ | awesome-mcp-servers ×2 | `not_a_server`; two curation-list repos; GitHub language undetected |

The five "undetected" language entries correspond to GitHub repositories whose primary language was not identified by GitHub's linguist (typically README-only or polyglot repos with no dominant language file). These slots were structurally unreplaceable because the backup pool was built on detected language.

### 2. JavaScript / 50–199★ — backup pool consumed (−2 slots)

Two slots unfilled: `seekrays/seekchat` (`not_a_server`) and `ragieai/ragie-mcp-server` (`zero_tools_found`). Backups were attempted for both but the accepted candidates (`dkmaker/mcp-rest-api` and `wong2/mcp-cli`) themselves yielded 0 extractable tools after cloning and locating. The JavaScript/50–199 backup pool was exhausted at this point.

### 3. Rust / 1 000+★ — clone failure, pool exhausted (−1 slot)

`block/goose` failed to clone. Only one Rust/1 000+ backup existed in the pool and had been consumed by an earlier replacement; no second-level fallback was available.

### 4. TypeScript / 50–199★ — backup pool partially exhausted (−3 slots)

Three slots unfilled: `evalstate/mcp-webcam` (`zero_tools_found`), `flatironinstitute/neurosift` (`not_a_server`), and `apify/tester-mcp-client` (`not_a_server`). The TypeScript/50–199 pool started with 21 entries and absorbed 14 same-stratum replacements plus one cross-bucket promotion, leaving no valid backup for these three slots.

---

## Cross-bucket backup promotions (9 slots)

Nine slots were filled by a backup from a *different* star-bucket within the **same language**. All nine remain in the target language; only the star-count tier of the filling repo diverges from the planned slot.

| Primary (planned stratum) | Backup stratum used |
|---|---|
| TypeScript / 10–49 (×6: sensei-mcp, mcp-ollama-agent, aws-s3-mcp, ragie-typescript, novu-ts, serveMyAPI) | TypeScript / 50–199 or 200–999 or 1 000+ |
| TypeScript / 200–999 (claude-debugs-for-you) | TypeScript / 50–199 |
| TypeScript / 50–199 (vercel/sdk) | TypeScript / 1 000+ |
| JavaScript / 1 000+ (benborla/mcp-server-mysql) | JavaScript / 50–199 |

The six TypeScript/10–49 cross-bucket promotions reflect exhaustion of the TypeScript/10–49 backup pool (17 entries at start, all consumed by the time these six primaries failed). The star-bucket shift is noted; sensitivity analyses that stratify on star count should exclude or flag these nine repos.

---

## Achieved composition (364 repos)

Of the 364 repos with extractable tool data:

- **292 (80.2%)** filled by the original planned primary, tool extraction succeeded on first attempt or after a parser fix (`parser_fixed`).
- **63 (17.3%)** filled by a backup from the **same** language × star-bucket stratum as planned.
- **9 (2.5%)** filled by a backup from a different star-bucket within the same language (cross-bucket promotion).

The final dataset therefore faithfully represents the planned language distribution. The star-bucket distribution is preserved in 362/364 slots (99.5%); the nine cross-bucket promotions introduce a minor upward bias in star counts within TypeScript/10–49 and JavaScript/1 000+.
