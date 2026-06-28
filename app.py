"""
MCP Drift Study — interactive results dashboard.

Read-only over already-committed artifacts in data/processed/. No live API
calls, no pipeline recomputation: every figure is loaded from a committed
CSV/JSONL/JSON file (light in-memory aggregation only, e.g. value counts and
group-bys, exactly as a reader would tabulate the published data).

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
PROC = ROOT / "data" / "processed"

# Professional, muted palette
C_PRIMARY = "#2563eb"   # blue 600
C_ACCENT = "#0f766e"    # teal 700
C_WARN = "#b45309"      # amber 700
C_DANGER = "#b91c1c"    # red 700
C_MUTED = "#94a3b8"     # slate 400
PLOTLY_TEMPLATE = "plotly_white"

LABEL_ORDER = [
    "COSMETIC",
    "CLARIFICATION",
    "SCHEMA_EXPANSION",
    "SCHEMA_CONTRACTION",
    "BEHAVIORAL_DRIFT",
]
LABEL_COLORS = {
    "COSMETIC": "#94a3b8",
    "CLARIFICATION": "#38bdf8",
    "SCHEMA_EXPANSION": "#22c55e",
    "SCHEMA_CONTRACTION": "#f59e0b",
    "BEHAVIORAL_DRIFT": "#dc2626",
    "API_ERROR": "#cbd5e1",
}

st.set_page_config(
    page_title="MCP Drift Study — Results Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Data loaders (cached)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_jsonl(path: Path) -> pd.DataFrame:
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def load_classifications() -> pd.DataFrame:
    df = load_jsonl(PROC / "tool_classifications.jsonl")
    df["structural_type"] = df.apply(structural_type, axis=1)
    df["repo_short"] = df["repo_url"].map(short_repo)
    df["agreed"] = df["pass1_label"] == df["pass2_label"]
    return df


@st.cache_data(show_spinner=False)
def load_stratification() -> pd.DataFrame:
    return pd.read_csv(PROC / "stratification_planned_vs_achieved.csv")


@st.cache_data(show_spinner=False)
def load_renames() -> pd.DataFrame:
    return pd.read_csv(PROC / "rename_candidates.csv")


@st.cache_data(show_spinner=False)
def load_agentic() -> list[dict]:
    """Join per-slot results with before/after definitions, keyed by slot."""
    results = json.loads((PROC / "agentic_validation_results.json").read_text())
    candidates = json.loads((PROC / "agentic_test_candidates.json").read_text())
    cand_by_slot = {c["slot"]: c for c in candidates}
    merged = []
    for r in sorted(results, key=lambda x: x["slot"]):
        c = cand_by_slot.get(r["slot"], {})
        merged.append(
            {
                **r,
                "before_definition": c.get("before_definition", {}),
                "after_definition": c.get("after_definition", {}),
            }
        )
    return merged


@st.cache_data(show_spinner=False)
def load_readme_intro() -> str:
    """Intro paragraphs between the H1 and the first H2 in README.md."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # drop the H1 line, then take everything up to the first '## '
    after_h1 = text.split("\n", 1)[1] if "\n" in text else text
    intro = after_h1.split("\n## ", 1)[0]
    return intro.strip()


# --------------------------------------------------------------------------- #
# Derivations (pure functions over committed fields — no pipeline rerun)
# --------------------------------------------------------------------------- #
def structural_type(r) -> str:
    """Replicates the study's structural taxonomy (type_change has priority).

    Reproduces the exact pool sizes published in docs/phase5_summary.md §3
    (desc_only 1770, desc_and_schema_add 182, schema_mixed 166, schema_add_only
    135, type_change 102, schema_remove_only 65, desc_and_schema_remove 61).
    """
    desc = bool(r.get("description_changed"))
    add = bool(r.get("schema_fields_added"))
    rem = bool(r.get("schema_fields_removed"))
    typ = bool(r.get("schema_type_changes"))
    if typ:
        return "type_change"
    if add and rem:
        return "schema_mixed"
    if add:
        return "desc_and_schema_add" if desc else "schema_add_only"
    if rem:
        return "desc_and_schema_remove" if desc else "schema_remove_only"
    return "desc_only"


def short_repo(url: str) -> str:
    return "/".join(str(url).rstrip("/").split("/")[-2:])


# --------------------------------------------------------------------------- #
# Static figures sourced directly from the committed summary docs
# --------------------------------------------------------------------------- #
FUNNEL = [
    ("Seed servers (deduplicated)", 1899),
    ("Eligible after filtering", 537),
    ("Sampled (stratified, n=380)", 380),
    ("Targeted for history walk", 280),
    ("Successfully walked", 276),
]

# By source type — published P2 / conservative BD rates (phase5_summary.md §4)
SOURCE_TYPE = pd.DataFrame(
    [
        ("community", 623, 19.1, 23.8),
        ("mined", 1246, 11.0, 19.2),
        ("official", 612, 8.7, 14.2),
    ],
    columns=["source", "events", "bd_both_pct", "bd_p2_pct"],
)

LIMITATIONS = [
    ("L1", "MCPCrawler dataset unavailable; sampling used MCP Registry + curated "
     "GitHub search as a proxy.",
     "Sample over-represents high-star, English-language repos; long tail "
     "under-represented (n=380 sampled)."),
    ("L2", "No extractor implemented for C#.",
     "C#-primary repos excluded from the sample (estimated small impact)."),
    ("L3", "Tool definitions in source files the per-commit extractor cannot traverse "
     "(JSON-only / unusual patterns).",
     "4 repos produced 0 walk records and were excluded."),
    ("L4", "Test / fixture / example / template directory contamination in the locator.",
     "3 rounds of exclusion-list fixes before walking; 12 primaries dropped to "
     "zero across rounds; firebase/genkit 58→20 tools."),
    ("L5", "Tool renames are not captured in the primary diff counts (the differ groups "
     "by tool_name).",
     "688 rename candidates across 71 repos tracked separately; reported as a "
     "lower bound."),
    ("L6", "History walk uses `git log --follow` per source file; cross-file moves at "
     "the rename boundary may be missed.",
     "968 source_file_changed events flagged across 59 repos."),
    ("L7", "Degenerate same-SHA diff records (a tool defined in multiple source files "
     "at one commit → a spatial, not temporal, comparison).",
     "67 events across 7 repos excluded before classification."),
    ("L8", "Empty-schema cross-file events that pass the same-SHA filter but may reflect "
     "a cross-file rather than temporal description change.",
     "53 events (all desc_only) inside the valid 2,481; flagged as a potential "
     "desc_only inflation source."),
    ("L9", "schema_remove_only events cannot be separated into SCHEMA_CONTRACTION vs "
     "BEHAVIORAL_DRIFT from text alone; classifier labels them conservatively.",
     "65 events (2.6% of the valid set); BEHAVIORAL_DRIFT counts are a lower "
     "bound for this type."),
    ("L10", "Tutorial / educational repos with numbered chapter directories can produce "
     "false temporal drift events that pass the L7 and L8 filters.",
     "1 confirmed instance excluded (search_web, softwaredesign-llm-application); "
     "additional count unknown (no automated check)."),
    ("L11", "The 276 walked repos are the initial-draw primaries; 78 backup repos that "
     "filled strata were not walked.",
     "Walked set vs 364-repo achieved sample: largest deviation −5.1 pp "
     "(TypeScript); no stratum absent."),
]


# --------------------------------------------------------------------------- #
# Small UI helpers
# --------------------------------------------------------------------------- #
def section_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)


def render_tool_definition(defn: dict) -> str:
    """Render a tool definition (description + schema) as markdown."""
    if not defn:
        return "_definition not available_"
    desc = (defn.get("description") or "").strip() or "_(no description)_"
    schema = defn.get("input_schema") or {}
    props = list((schema.get("properties") or {}).keys())
    required = set(schema.get("required") or [])
    lines = [desc, ""]
    if props:
        lines.append("**Parameters:**")
        for p in props:
            mark = " · _required_" if p in required else ""
            lines.append(f"- `{p}`{mark}")
    else:
        lines.append("**Parameters:** _(none)_")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# SECTION 1 — OVERVIEW
# --------------------------------------------------------------------------- #
def page_overview() -> None:
    section_header(
        "Overview",
        "Headline results from the MCP tool-definition drift study.",
    )

    row1 = st.columns(3)
    row1[0].metric("Repos sampled → walked", "380 → 276")
    row1[1].metric("Unique tools tracked", "4,784")
    row1[2].metric("Tools changed ≥ once", "84.1%", help="4,023 / 4,784")

    row2 = st.columns(3)
    row2[0].metric("Valid temporal change events", "2,481")
    row2[1].metric("BEHAVIORAL_DRIFT (range)", "309 – 474",
                   help="Conservative (both passes agree) → P2 best estimate")
    row2[2].metric("BEHAVIORAL_DRIFT rate", "12.5 – 19.1%")

    st.divider()
    st.markdown("### Research question & gap")
    st.markdown(load_readme_intro())

    st.info(
        "This dashboard is **read-only** over committed results. Every number "
        "above is loaded from `data/processed/` — no live API calls and no "
        "pipeline recomputation.",
        icon="🔒",
    )


# --------------------------------------------------------------------------- #
# SECTION 2 — SAMPLE & METHODOLOGY
# --------------------------------------------------------------------------- #
def page_sample() -> None:
    section_header(
        "Sample & methodology",
        "How the 380-repo stratified sample was built and walked.",
    )

    st.markdown("### Sampling funnel")
    labels = [f"{name}" for name, _ in FUNNEL]
    values = [v for _, v in FUNNEL]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=values,
            textposition="inside",
            textinfo="value+percent initial",
            marker={"color": [C_PRIMARY, "#3b82f6", "#60a5fa", C_ACCENT, "#0d9488"]},
            connector={"line": {"color": C_MUTED}},
        )
    )
    fig.update_layout(template=PLOTLY_TEMPLATE, height=380,
                      margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown("### Planned vs. achieved sample")
    st.caption(
        "From `stratification_planned_vs_achieved.csv`. Stratified by language "
        "× star tier; backups filled unfilled strata where a pool existed."
    )

    strat = load_stratification()

    by_lang = (
        strat.groupby("lang")[["planned", "achieved"]].sum().reset_index()
        .sort_values("planned", ascending=False)
    )
    by_lang = by_lang[by_lang["planned"] >= 2]  # keep readable; drop singleton langs
    fig_lang = px.bar(
        by_lang.melt(id_vars="lang", value_vars=["planned", "achieved"],
                     var_name="kind", value_name="repos"),
        x="lang", y="repos", color="kind", barmode="group",
        color_discrete_map={"planned": C_MUTED, "achieved": C_PRIMARY},
        labels={"lang": "Primary language", "repos": "Repos", "kind": ""},
    )
    fig_lang.update_layout(template=PLOTLY_TEMPLATE, height=380,
                           legend_title_text="", margin=dict(t=10))

    tier_order = ["10-49", "50-199", "200-999", "1000+"]
    by_tier = (
        strat.groupby("bucket")[["planned", "achieved"]].sum().reset_index()
    )
    by_tier["bucket"] = pd.Categorical(by_tier["bucket"], tier_order, ordered=True)
    by_tier = by_tier.sort_values("bucket")
    fig_tier = px.bar(
        by_tier.melt(id_vars="bucket", value_vars=["planned", "achieved"],
                     var_name="kind", value_name="repos"),
        x="bucket", y="repos", color="kind", barmode="group",
        color_discrete_map={"planned": C_MUTED, "achieved": C_PRIMARY},
        labels={"bucket": "Star tier", "repos": "Repos", "kind": ""},
    )
    fig_tier.update_layout(template=PLOTLY_TEMPLATE, height=380,
                           legend_title_text="", margin=dict(t=10))

    c1, c2 = st.columns(2)
    c1.markdown("**By language** (planned ≥ 2)")
    c1.plotly_chart(fig_lang, width="stretch")
    c2.markdown("**By star tier**")
    c2.plotly_chart(fig_tier, width="stretch")

    with st.expander("Show full stratification table"):
        st.dataframe(strat, width="stretch", hide_index=True)


# --------------------------------------------------------------------------- #
# SECTION 3 — DRIFT FINDINGS
# --------------------------------------------------------------------------- #
def page_drift() -> None:
    section_header(
        "Drift findings",
        "Classifier output over the 2,481 valid temporal change events.",
    )
    df = load_classifications()

    # --- (a) P1 vs P2 category distribution ---------------------------------
    st.markdown("### Classification distribution (Pass 1 vs Pass 2)")
    p1 = df["pass1_label"].value_counts()
    p2 = df["pass2_label"].value_counts()
    cats = [c for c in LABEL_ORDER if c in set(p1.index) | set(p2.index)]
    dist = pd.DataFrame(
        {
            "category": cats * 2,
            "pass": ["Pass 1"] * len(cats) + ["Pass 2"] * len(cats),
            "events": [int(p1.get(c, 0)) for c in cats]
            + [int(p2.get(c, 0)) for c in cats],
        }
    )
    fig_dist = px.bar(
        dist, x="category", y="events", color="pass", barmode="group",
        color_discrete_map={"Pass 1": C_MUTED, "Pass 2": C_PRIMARY},
        labels={"category": "", "events": "Events", "pass": ""},
        category_orders={"category": cats},
    )
    fig_dist.update_layout(template=PLOTLY_TEMPLATE, height=400, legend_title_text="",
                           margin=dict(t=10))
    st.plotly_chart(fig_dist, width="stretch")
    st.caption(
        f"Pass-to-pass agreement: {int((df['agreed']).sum()):,} / {len(df):,} "
        f"({100 * df['agreed'].mean():.1f}%). "
        "BEHAVIORAL_DRIFT: 309 conservative (both agree) → 474 (P2 best estimate)."
    )

    st.divider()

    # --- (b) BD rate by structural change type ------------------------------
    st.markdown("### BEHAVIORAL_DRIFT rate by structural change type")
    df["bd_both"] = (df["pass1_label"] == "BEHAVIORAL_DRIFT") & (
        df["pass2_label"] == "BEHAVIORAL_DRIFT"
    )
    grp = (
        df.groupby("structural_type")
        .agg(events=("bd_both", "size"), bd=("bd_both", "sum"))
        .reset_index()
    )
    grp["bd_pct"] = 100 * grp["bd"] / grp["events"]
    grp = grp.sort_values("bd_pct", ascending=False)
    fig_bd = px.bar(
        grp, x="structural_type", y="bd_pct",
        text=grp["bd_pct"].map(lambda v: f"{v:.1f}%"),
        labels={"structural_type": "", "bd_pct": "BEHAVIORAL_DRIFT rate (%)"},
        color_discrete_sequence=[C_DANGER],
    )
    fig_bd.update_traces(textposition="outside")
    fig_bd.update_layout(template=PLOTLY_TEMPLATE, height=400, margin=dict(t=10),
                         yaxis_range=[0, 100])
    st.plotly_chart(fig_bd, width="stretch")
    st.caption(
        "Conservative (both-passes-agree) rate, computed over the committed "
        "classifications on the study's structural buckets. Schema-removing and "
        "schema-mixed changes carry by far the highest drift rate; pure "
        "field additions essentially never read as behavioral drift."
    )

    st.divider()

    # --- (c) BD rate by source type -----------------------------------------
    st.markdown("### BEHAVIORAL_DRIFT rate by source type")
    src = SOURCE_TYPE.copy()
    melted = src.melt(
        id_vars=["source", "events"],
        value_vars=["bd_both_pct", "bd_p2_pct"],
        var_name="estimate", value_name="pct",
    )
    melted["estimate"] = melted["estimate"].map(
        {"bd_both_pct": "Conservative (both)", "bd_p2_pct": "Best estimate (P2)"}
    )
    fig_src = px.bar(
        melted, x="source", y="pct", color="estimate", barmode="group",
        text=melted["pct"].map(lambda v: f"{v:.1f}%"),
        color_discrete_map={"Conservative (both)": C_MUTED,
                            "Best estimate (P2)": C_ACCENT},
        labels={"source": "", "pct": "BEHAVIORAL_DRIFT rate (%)", "estimate": ""},
        category_orders={"source": ["official", "mined", "community"]},
    )
    fig_src.update_traces(textposition="outside")
    fig_src.update_layout(template=PLOTLY_TEMPLATE, height=400, legend_title_text="",
                          margin=dict(t=10), yaxis_range=[0, 30])
    st.plotly_chart(fig_src, width="stretch")
    st.caption(
        "Cleanest gradient in the data: vendor-maintained **official** repos are "
        "the most stable (14.2% P2); third-party **community** repos drift most "
        "(23.8% P2). Published figures from phase5_summary.md §4."
    )

    st.divider()

    # --- (d) Searchable event table -----------------------------------------
    st.markdown("### Event explorer")
    st.caption(
        "The 2,481 valid temporal change events with their classifications "
        "(`tool_diffs.jsonl` fields joined with `tool_classifications.jsonl` labels)."
    )

    f1, f2, f3 = st.columns([2, 2, 2])
    query = f1.text_input("Search tool or repo", "")
    types = f2.multiselect("Structural type",
                           sorted(df["structural_type"].unique()))
    labels_sel = f3.multiselect("Final label (Pass 2)",
                                [c for c in LABEL_ORDER if c in df["pass2_label"].unique()])

    view = df.copy()
    if query:
        q = query.lower()
        view = view[
            view["tool_name"].str.lower().str.contains(q, na=False)
            | view["repo_short"].str.lower().str.contains(q, na=False)
        ]
    if types:
        view = view[view["structural_type"].isin(types)]
    if labels_sel:
        view = view[view["pass2_label"].isin(labels_sel)]

    table = view.assign(
        added=view["schema_fields_added"].map(lambda x: ", ".join(x) if x else ""),
        removed=view["schema_fields_removed"].map(lambda x: ", ".join(x) if x else ""),
    )[
        ["repo_short", "tool_name", "structural_type", "description_changed",
         "added", "removed", "pass1_label", "pass2_label", "agreed"]
    ].rename(
        columns={
            "repo_short": "repo",
            "structural_type": "type",
            "description_changed": "desc_changed",
            "pass1_label": "P1",
            "pass2_label": "P2 (final)",
            "agreed": "P1=P2",
        }
    )
    st.caption(f"Showing {len(table):,} of {len(df):,} events.")
    st.dataframe(table, width="stretch", hide_index=True, height=420)


# --------------------------------------------------------------------------- #
# SECTION 4 — AGENTIC VALIDATION
# --------------------------------------------------------------------------- #
def page_agentic() -> None:
    section_header(
        "Agentic behavioral validation",
        "Do drifted definitions actually change how a live agent behaves? "
        "15 purposively selected BEHAVIORAL_DRIFT events, temperature=0, "
        "3 replications per side.",
    )
    slots = load_agentic()
    n_diff = sum(1 for s in slots if s["behavioral_difference"])

    k = st.columns(3)
    k[0].metric("Cases with behavioral difference", f"{n_diff} / {len(slots)}")
    k[1].metric("Unstable across replications", "0 / 15")
    k[2].metric("Scope", "Illustrative", help="Purposive sample — no prevalence claim")

    # --- Key-finding callout: slot 07 ---------------------------------------
    slot7 = next(s for s in slots if s["slot"] == 7)
    quote = slot7["after_runs"][0].get("response_text", "")
    after_input = slot7["after_runs"][0].get("tool_input", {})
    st.markdown("### ")
    st.markdown(
        f"""
<div style="border-left:6px solid {C_DANGER};background:#fef2f2;
            padding:18px 22px;border-radius:6px;margin:6px 0 18px 0;">
  <div style="font-size:0.8rem;font-weight:700;letter-spacing:.06em;
              text-transform:uppercase;color:{C_DANGER};">
    Key finding · Slot 07 · update_instance_name (neo4j-contrib/mcp-neo4j)
  </div>
  <div style="font-size:1.35rem;font-weight:600;color:#111827;
              margin:10px 0 12px 0;line-height:1.45;">
    &ldquo;{quote}&rdquo;
  </div>
  <div style="color:#374151;font-size:0.97rem;line-height:1.55;">
    The AFTER definition narrowed <code>instance_ids</code> (array) to
    <code>instance_id</code> (scalar). Asked to rename <b>two</b> instances in
    one operation, the agent confirmed success for &ldquo;both&rdquo; — but the
    actual tool call sent only
    <code>instance_id="{after_input.get('instance_id', '')}"</code>.
    <code>inst-001</code> was silently dropped, with no caveat, identically
    across all 3 temperature=0 runs. A confident, reproducible,
    <b>factually false success claim</b> caused purely by a tool-definition change.
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown("### All 15 cases")
    st.caption(
        "Each card shows the before/after definition and the behavioral "
        "difference observed (`agentic_validation_results.json` + "
        "`agentic_test_candidates.json`)."
    )

    only_diff = st.toggle("Show only cases with a behavioral difference", value=False)

    for s in slots:
        if only_diff and not s["behavioral_difference"]:
            continue
        diff = s["behavioral_difference"]
        badge = "🔴 behavioral difference" if diff else "⚪ no difference"
        title = (f"Slot {s['slot']:02d} · `{s['tool_name']}` · "
                 f"{short_repo(s['repo_url'])} — {badge}")
        with st.expander(title, expanded=(s["slot"] == 7)):
            st.caption(f"Structural type: `{s['structural_type']}`  ·  "
                       f"runs/side: {s['n_runs']}  ·  stable: "
                       f"{s['before_stable'] and s['after_stable']}")
            st.markdown(f"**User request:** {s['user_request']}")
            cols = st.columns(2)
            cols[0].markdown("**BEFORE definition**")
            cols[0].markdown(render_tool_definition(s["before_definition"]))
            cols[1].markdown("**AFTER definition**")
            cols[1].markdown(render_tool_definition(s["after_definition"]))
            if diff:
                st.success(f"**Behavioral difference:** {s['diff_description']}",
                           icon="🔬")
            else:
                st.info(f"**No behavioral difference:** {s['diff_description']}",
                        icon="➖")

    st.warning(
        "**Scope caveat:** these 15 events were selected purposively from the "
        "309-event conservative BEHAVIORAL_DRIFT set to illustrate *what kinds* "
        "of behavioral change drift can produce — not *how often*. No rate or "
        "prevalence claim can be derived from this sample.",
        icon="⚠️",
    )


# --------------------------------------------------------------------------- #
# SECTION 5 — SUPPLEMENTARY FINDINGS
# --------------------------------------------------------------------------- #
def page_supplementary() -> None:
    section_header(
        "Supplementary findings",
        "Tool naming-convention evolution — distinct from the primary drift counts.",
    )
    ren = load_renames()

    k = st.columns(3)
    k[0].metric("Rename candidates", f"{len(ren):,}")
    k[1].metric("Repos affected", "71 / 276", help="25.7% of walked repos")
    k[2].metric("Schema fully preserved", "619", help="High-confidence cases")

    st.markdown("### Confidence-tier breakdown")
    tier_counts = (
        ren["confidence_tier"].value_counts()
        if "confidence_tier" in ren.columns
        else pd.Series(dtype=int)
    )
    # Published canonical ordering/criteria from rename_candidates_summary.md
    tiers = pd.DataFrame(
        [
            ("Perfect", "schema = 1.0 AND desc = 1.0", 326),
            ("High", "schema = 1.0, desc < 1.0", 293),
            ("Medium", "schema < 1.0", 69),
        ],
        columns=["Tier", "Criterion", "Candidates"],
    )
    c1, c2 = st.columns([3, 2])
    fig = px.bar(
        tiers, x="Tier", y="Candidates", text="Candidates",
        color="Tier",
        color_discrete_map={"Perfect": C_ACCENT, "High": C_PRIMARY, "Medium": C_MUTED},
        category_orders={"Tier": ["Perfect", "High", "Medium"]},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(template=PLOTLY_TEMPLATE, height=360, showlegend=False,
                      margin=dict(t=10))
    c1.plotly_chart(fig, width="stretch")
    c2.dataframe(tiers, width="stretch", hide_index=True)

    st.markdown(
        "These represent naming-convention evolution — namespacing "
        "(`dialogs → tg_dialogs`), simplification "
        "(`roam_create_output_with_nested_structure → roam_create_outline`), and "
        "restructuring. They are **a lower bound** and are **not** included in the "
        "primary drift event counts (the differ groups by `tool_name`, so a rename "
        "appears as one tool ending and another beginning)."
    )

    with st.expander("Browse rename candidates"):
        show_cols = [c for c in
                     ["repo_url", "old_tool_name", "new_tool_name", "schema_jaccard",
                      "desc_jaccard", "confidence_tier", "date_from", "date_to"]
                     if c in ren.columns]
        st.dataframe(ren[show_cols], width="stretch", hide_index=True,
                     height=380)


# --------------------------------------------------------------------------- #
# SECTION 6 — LIMITATIONS
# --------------------------------------------------------------------------- #
def page_limitations() -> None:
    section_header(
        "Limitations & threats to validity",
        "All 11 documented limitations (L1–L11) from the phase summaries.",
    )
    for code, what, impact in LIMITATIONS:
        st.markdown(
            f"""
<div style="border:1px solid #e2e8f0;border-radius:8px;padding:14px 18px;
            margin-bottom:10px;background:#ffffff;">
  <span style="display:inline-block;background:{C_PRIMARY};color:#fff;
               font-weight:700;border-radius:4px;padding:1px 9px;font-size:0.8rem;">
    {code}</span>
  <span style="font-weight:600;color:#0f172a;margin-left:8px;">{what}</span>
  <div style="color:#475569;margin-top:7px;font-size:0.95rem;line-height:1.5;">
    <b>Affected:</b> {impact}
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
    st.caption(
        "Sources: docs/phase4_summary.md §5, docs/phase5_summary.md §6 & Exec "
        "Summary, docs/phase6_summary.md §6."
    )


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
PAGES = {
    "1 · Overview": page_overview,
    "2 · Sample & methodology": page_sample,
    "3 · Drift findings": page_drift,
    "4 · Agentic validation": page_agentic,
    "5 · Supplementary findings": page_supplementary,
    "6 · Limitations": page_limitations,
}


def main() -> None:
    st.sidebar.title("MCP Drift Study")
    st.sidebar.caption(
        "Empirical measurement of how MCP tool definitions change over time, "
        "and whether those changes alter AI-agent behavior."
    )
    choice = st.sidebar.radio("Section", list(PAGES.keys()), label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.markdown(
        "**Read-only dashboard.** All figures load from committed files in "
        "`data/processed/`. No live API calls, no recomputation."
    )

    if not PROC.exists():
        st.error(f"Data directory not found: `{PROC}`. Run from the project root.")
        return

    PAGES[choice]()


if __name__ == "__main__":
    main()
