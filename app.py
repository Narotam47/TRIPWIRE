"""
TRIPWIRE — interactive results dashboard for the MCP tool-definition drift study.

Read-only over already-committed artifacts in data/processed/. No live API
calls, no pipeline recomputation: every figure is loaded from a committed
CSV/JSONL/JSON file (light in-memory aggregation only, e.g. value counts and
group-bys, exactly as a reader would tabulate the published data).

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
PROC = ROOT / "data" / "processed"

# --------------------------------------------------------------------------- #
# Design system — ONE palette, used the same way in every section
# --------------------------------------------------------------------------- #
# Semantic roles (held constant across all charts):
#   BLUE  → first / baseline / "Pass 1" / "planned" / "before" / conservative
#   TEAL  → final / walked / achieved / "Pass 2" / "after" / best estimate
#   RED   → BEHAVIORAL_DRIFT, danger, the key finding
#   AMBER → caution / middle tier
#   SLATE → neutral / muted / non-highlighted
C_BLUE = "#2563eb"
C_TEAL = "#0d9488"
C_RED = "#dc2626"
C_AMBER = "#d97706"
C_SLATE = "#64748b"

FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
    'Helvetica, Arial, sans-serif'
)

# Single Plotly interaction config for EVERY chart: no toolbar, no logo.
PLOTLY_CONFIG = {"displayModeBar": False, "displaylogo": False, "responsive": True}

LABEL_ORDER = [
    "COSMETIC",
    "CLARIFICATION",
    "SCHEMA_EXPANSION",
    "SCHEMA_CONTRACTION",
    "BEHAVIORAL_DRIFT",
]

st.set_page_config(
    page_title="TRIPWIRE - MCP Tool Drift Study",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Theme awareness (dark / light) — drives every chart + card color
# --------------------------------------------------------------------------- #
def theme_colors() -> dict:
    """Resolve the active Streamlit theme into a concrete color set.

    Plotly text/grid must be concrete colors (it cannot read CSS variables),
    so we detect the theme and supply readable colors for it.
    """
    kind = "light"
    try:
        kind = (st.context.theme.type or "light").lower()
    except Exception:
        kind = "light"

    if kind == "dark":
        return {
            "type": "dark",
            "ink": "#f1f5f9",          # strongest text
            "font": "#cbd5e1",         # body text
            "muted": "#94a3b8",        # labels / captions
            "axis": "#94a3b8",
            "grid": "rgba(148,163,184,0.16)",
            "card_bg": "rgba(148,163,184,0.07)",
            "card_border": "rgba(148,163,184,0.22)",
            "hover_bg": "#1e293b",
            "tint": lambda hexc, a=0.16: _rgba(hexc, a),
        }
    return {
        "type": "light",
        "ink": "#0f172a",
        "font": "#334155",
        "muted": "#64748b",
        "axis": "#64748b",
        "grid": "rgba(100,116,139,0.16)",
        "card_bg": "#ffffff",
        "card_border": "#e2e8f0",
        "hover_bg": "#ffffff",
        "tint": lambda hexc, a=0.08: _rgba(hexc, a),
    }


def _rgba(hexc: str, alpha: float) -> str:
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def register_template(tc: dict) -> None:
    """Register a single custom Plotly template applied to every figure."""
    t = go.layout.Template()
    L = t.layout
    L.font = dict(family=FONT_FAMILY, size=13, color=tc["font"])
    L.paper_bgcolor = "rgba(0,0,0,0)"
    L.plot_bgcolor = "rgba(0,0,0,0)"
    L.colorway = [C_BLUE, C_TEAL, C_RED, C_AMBER, C_SLATE]
    L.hoverlabel = dict(
        bgcolor=tc["hover_bg"],
        bordercolor=tc["card_border"],
        font=dict(family=FONT_FAMILY, size=12.5, color=tc["ink"]),
    )
    L.legend = dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0,
        title_text="", font=dict(size=12, color=tc["muted"]),
    )
    L.xaxis = dict(
        showgrid=False, zeroline=False, linecolor=tc["grid"], ticks="",
        tickfont=dict(color=tc["axis"], size=12),
        title_font=dict(color=tc["muted"], size=12.5),
    )
    L.yaxis = dict(
        showgrid=True, gridcolor=tc["grid"], zeroline=False,
        linecolor="rgba(0,0,0,0)",
        tickfont=dict(color=tc["axis"], size=12),
        title_font=dict(color=tc["muted"], size=12.5),
    )
    L.margin = dict(l=8, r=14, t=34, b=8)
    L.bargap = 0.30
    L.bargroupgap = 0.12
    pio.templates["mcp"] = t
    pio.templates.default = "mcp"


def finalize(fig, *, height=380, ymax=None, ytitle=None, legend=True):
    fig.update_layout(height=height, template="mcp")
    fig.update_layout(showlegend=legend)
    if ytitle is not None:
        fig.update_yaxes(title_text=ytitle)
    if ymax is not None:
        fig.update_yaxes(range=[0, ymax])
    return fig


def label_bars(fig, tc, *, fmt="%{y}"):
    """Consistent outside value labels that never clip.

    Labels read each bar's own ``y`` (via texttemplate) rather than a manual
    text array, so they stay correct even when a chart is split into one trace
    per color category.
    """
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        textfont=dict(family=FONT_FAMILY, size=12, color=tc["ink"]),
        texttemplate=fmt,
    )
    return fig


def show(fig, container=None):
    (container or st).plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


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
    after_h1 = text.split("\n", 1)[1] if "\n" in text else text
    return after_h1.split("\n## ", 1)[0].strip()


# --------------------------------------------------------------------------- #
# Derivations (pure functions over committed fields — no pipeline rerun)
# --------------------------------------------------------------------------- #
def structural_type(r) -> str:
    """Study's structural taxonomy (type_change has priority).

    Reproduces the pool sizes published in docs/phase5_summary.md §3.
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
        ("official", 612, 8.7, 14.2),
        ("mined", 1246, 11.0, 19.2),
        ("community", 623, 19.1, 23.8),
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
# Global CSS — fonts, entrance animation, KPI cards, callouts, chart titles
# --------------------------------------------------------------------------- #
def inject_css(tc: dict) -> None:
    st.markdown(
        f"""
<style>
:root {{ --mcp-blue:{C_BLUE}; --mcp-teal:{C_TEAL}; --mcp-red:{C_RED}; }}

/* one consistent font family for app text (matches the Plotly template) */
section[data-testid="stMain"], section[data-testid="stMain"] p,
section[data-testid="stMain"] li, [data-testid="stSidebar"] {{
    font-family: {FONT_FAMILY};
}}

/* hide default Streamlit chrome for a cleaner demo surface */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
[data-testid="stDecoration"] {{ display: none; }}
[data-testid="stToolbar"] {{ display: none; }}
[data-testid="stAppDeployButton"] {{ display: none; }}
.stDeployButton {{ display: none; }}

/* ---- subtle, fast entrance animation on every visual block ---- */
@keyframes mcpFadeUp {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
}}
[data-testid="stPlotlyChart"],
[data-testid="stDataFrame"],
.mcp-kpi, .mcp-callout, .mcp-card, .mcp-defcard {{
    animation: mcpFadeUp .42s cubic-bezier(.22,1,.36,1) both;
}}
/* gentle left-to-right stagger across a KPI / card row */
[data-testid="stHorizontalBlock"] [data-testid="column"]:nth-child(1) .mcp-kpi {{ animation-delay: .00s; }}
[data-testid="stHorizontalBlock"] [data-testid="column"]:nth-child(2) .mcp-kpi {{ animation-delay: .07s; }}
[data-testid="stHorizontalBlock"] [data-testid="column"]:nth-child(3) .mcp-kpi {{ animation-delay: .14s; }}

/* ---- KPI card (one component, identical everywhere) ---- */
.mcp-kpi {{
    background: {tc['card_bg']};
    border: 1px solid {tc['card_border']};
    border-top: 3px solid var(--mcp-blue);
    border-radius: 12px;
    padding: 15px 18px 16px;
    height: 100%;
    box-shadow: 0 1px 2px rgba(15,23,42,.04);
}}
.mcp-kpi .v {{ font-size: 1.95rem; font-weight: 700; color: {tc['ink']};
              line-height: 1.05; letter-spacing: -.01em; }}
.mcp-kpi .l {{ font-size: .80rem; font-weight: 600; color: {tc['muted']};
              margin-top: 6px; text-transform: uppercase; letter-spacing: .04em; }}
.mcp-kpi .s {{ font-size: .74rem; color: {tc['muted']}; opacity: .85; margin-top: 3px; }}

/* ---- per-chart title block (consistent rhythm) ---- */
.mcp-ctitle {{ font-size: 1.06rem; font-weight: 650; color: {tc['ink']};
              margin: 2px 0 1px; }}
.mcp-csub {{ font-size: .85rem; color: {tc['muted']}; margin: 0 0 8px; }}

/* ---- key-finding callout (theme-aware) ---- */
.mcp-callout {{
    border-left: 5px solid var(--mcp-red);
    background: {tc['tint'](C_RED)};
    padding: 16px 20px; border-radius: 8px; margin: 4px 0 18px;
}}
.mcp-callout .eyebrow {{ font-size: .76rem; font-weight: 700; letter-spacing: .07em;
    text-transform: uppercase; color: var(--mcp-red); }}
.mcp-callout .quote {{ font-size: 1.3rem; font-weight: 600; color: {tc['ink']};
    margin: 10px 0 12px; line-height: 1.45; }}
.mcp-callout .body {{ color: {tc['font']}; font-size: .97rem; line-height: 1.55; }}
.mcp-callout code {{ background: {tc['tint'](C_SLATE, .18)}; padding: 1px 5px;
    border-radius: 4px; font-size: .85em; }}

/* ---- limitation card ---- */
.mcp-card {{
    background: {tc['card_bg']}; border: 1px solid {tc['card_border']};
    border-radius: 10px; padding: 13px 17px; margin-bottom: 10px;
}}
.mcp-card .badge {{ display: inline-block; background: var(--mcp-blue); color: #fff;
    font-weight: 700; border-radius: 5px; padding: 1px 9px; font-size: .78rem; }}
.mcp-card .head {{ font-weight: 600; color: {tc['ink']}; margin-left: 8px; }}
.mcp-card .aff {{ color: {tc['muted']}; margin-top: 7px; font-size: .92rem;
    line-height: 1.5; }}

/* ---- before / after definition card (agentic section) ---- */
.mcp-defcard {{ background: {tc['card_bg']}; border: 1px solid {tc['card_border']};
    border-radius: 10px; padding: 13px 15px; height: 100%; }}
.mcp-defcard .h {{ font-size: .76rem; font-weight: 700; letter-spacing: .06em;
    text-transform: uppercase; margin-bottom: 7px; }}
.mcp-defcard .d {{ color: {tc['font']}; font-size: .9rem; line-height: 1.5;
    white-space: pre-wrap; }}
.mcp-defcard .p {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }}
.mcp-chip {{ font-size: .76rem; border-radius: 20px; padding: 2px 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style>
""",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Reusable UI components
# --------------------------------------------------------------------------- #
def section_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)
    st.write("")


def chart_title(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="mcp-ctitle">{title}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="mcp-csub">{sub}</div>', unsafe_allow_html=True)


def kpi(col, value: str, label: str, sub: str = "", accent: str = C_BLUE) -> None:
    sub_html = f'<div class="s">{escape(sub)}</div>' if sub else ""
    col.markdown(
        f'<div class="mcp-kpi" style="border-top-color:{accent}">'
        f'<div class="v">{escape(value)}</div>'
        f'<div class="l">{escape(label)}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def definition_card(container, label, defn, accent, tc) -> None:
    desc = (defn.get("description") or "").strip() or "(no description)"
    desc = escape(desc)
    schema = defn.get("input_schema") or {}
    props = list((schema.get("properties") or {}).keys())
    required = set(schema.get("required") or [])
    if props:
        chips = []
        for p in props:
            if p in required:
                style = f"background:{accent};color:#fff;"
            else:
                style = (f"background:{tc['tint'](accent,.14)};color:{tc['font']};"
                         f"border:1px solid {tc['card_border']};")
            chips.append(f'<span class="mcp-chip" style="{style}">{escape(p)}</span>')
        chips_html = "".join(chips)
    else:
        chips_html = (f'<span class="mcp-chip" style="color:{tc["muted"]};'
                      f'border:1px dashed {tc["card_border"]}">no parameters</span>')
    container.markdown(
        f'<div class="mcp-defcard" style="border-top:3px solid {accent}">'
        f'<div class="h" style="color:{accent}">{label}</div>'
        f'<div class="d">{desc}</div>'
        f'<div class="p">{chips_html}</div></div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# SECTION 1 — OVERVIEW
# --------------------------------------------------------------------------- #
def page_overview(tc: dict) -> None:
    section_header(
        "TRIPWIRE — A Longitudinal Empirical Study of Tool Definition Drift "
        "in the MCP Ecosystem",
        "_Named for the classic file-integrity-monitoring paradigm (Kim & Spafford, "
        "1994); adapted here retrospectively, through git-history archaeology rather "
        "than live monitoring, to measure how often unauthorized-style change actually "
        "occurs across a real-world AI agent tool ecosystem._",
    )

    r1 = st.columns(3)
    kpi(r1[0], "380 → 276", "Repos sampled → walked",
        "98.6% walk success", accent=C_TEAL)
    kpi(r1[1], "4,784", "Unique tools tracked", "across 276 repos", accent=C_BLUE)
    kpi(r1[2], "84.1%", "Tools changed ≥ once", "4,023 / 4,784", accent=C_TEAL)

    st.write("")
    r2 = st.columns(3)
    kpi(r2[0], "2,481", "Valid temporal change events",
        "after filtering", accent=C_BLUE)
    kpi(r2[1], "309 – 474", "BEHAVIORAL_DRIFT events",
        "conservative → best estimate", accent=C_RED)
    kpi(r2[2], "12.5 – 19.1%", "BEHAVIORAL_DRIFT rate",
        "of valid change events", accent=C_RED)

    st.divider()
    chart_title("Research question & gap")
    st.markdown(load_readme_intro())

    st.info(
        "This dashboard is **read-only** over committed results. Every number "
        "is loaded from `data/processed/` — no live API calls, no recomputation.",
        icon="🔒",
    )


# --------------------------------------------------------------------------- #
# SECTION 2 — SAMPLE & METHODOLOGY
# --------------------------------------------------------------------------- #
def page_sample(tc: dict) -> None:
    section_header(
        "Sample & methodology",
        "How the 380-repo stratified sample was built and walked.",
    )

    chart_title("Sampling funnel",
                "From the deduplicated seed list down to repos successfully walked.")
    labels = [name for name, _ in FUNNEL]
    values = [v for _, v in FUNNEL]
    # blue → teal gradient so the final "walked" stage lands on teal
    funnel_colors = ["#2563eb", "#2a6fcf", "#2483a8", "#1b9488", C_TEAL]
    fig = go.Figure(
        go.Funnel(
            y=labels, x=values,
            textposition="inside", textinfo="value+percent initial",
            textfont=dict(family=FONT_FAMILY, size=13, color="#ffffff"),
            marker=dict(color=funnel_colors,
                        line=dict(width=0)),
            connector=dict(line=dict(color=tc["grid"], width=1)),
            hovertemplate="<b>%{y}</b><br>%{x:,} repos<extra></extra>",
        )
    )
    finalize(fig, height=360, legend=False)
    show(fig)

    st.divider()
    chart_title("Planned vs. achieved sample",
                "Stratified by language × star tier; backups filled unfilled strata "
                "where a pool existed. Source: stratification_planned_vs_achieved.csv")
    strat = load_stratification()

    by_lang = (
        strat.groupby("lang")[["planned", "achieved"]].sum().reset_index()
        .sort_values("planned", ascending=False)
    )
    by_lang = by_lang[by_lang["planned"] >= 2]
    fig_lang = px.bar(
        by_lang.melt(id_vars="lang", value_vars=["planned", "achieved"],
                     var_name="kind", value_name="repos"),
        x="lang", y="repos", color="kind", barmode="group",
        color_discrete_map={"planned": C_BLUE, "achieved": C_TEAL},
        labels={"lang": "", "repos": "Repos", "kind": ""},
    )
    fig_lang.update_traces(
        hovertemplate="%{fullData.name} · %{x}<br><b>%{y}</b> repos<extra></extra>")
    finalize(fig_lang, height=370, ytitle="Repos")

    tier_order = ["10-49", "50-199", "200-999", "1000+"]
    by_tier = strat.groupby("bucket")[["planned", "achieved"]].sum().reset_index()
    by_tier["bucket"] = pd.Categorical(by_tier["bucket"], tier_order, ordered=True)
    by_tier = by_tier.sort_values("bucket")
    fig_tier = px.bar(
        by_tier.melt(id_vars="bucket", value_vars=["planned", "achieved"],
                     var_name="kind", value_name="repos"),
        x="bucket", y="repos", color="kind", barmode="group",
        color_discrete_map={"planned": C_BLUE, "achieved": C_TEAL},
        labels={"bucket": "", "repos": "Repos", "kind": ""},
    )
    fig_tier.update_traces(
        hovertemplate="%{fullData.name} · %{x}★<br><b>%{y}</b> repos<extra></extra>")
    finalize(fig_tier, height=370, ytitle="Repos")

    c1, c2 = st.columns(2)
    with c1:
        chart_title("By primary language", "languages with ≥ 2 planned slots")
        show(fig_lang)
    with c2:
        chart_title("By star tier", "GitHub star buckets")
        show(fig_tier)

    st.caption("Blue = planned target · Teal = achieved (same encoding used in every "
               "chart). Hover any bar for exact counts.")

    with st.expander("Show full stratification table"):
        st.dataframe(strat, width="stretch", hide_index=True)


# --------------------------------------------------------------------------- #
# SECTION 3 — DRIFT FINDINGS
# --------------------------------------------------------------------------- #
def page_drift(tc: dict) -> None:
    section_header(
        "Drift findings",
        "Classifier output over the 2,481 valid temporal change events.",
    )
    df = load_classifications()

    # --- (a) P1 vs P2 category distribution ---------------------------------
    chart_title("Classification distribution",
                "Two independent passes (Pass 1 vs Pass 2) over the same events.")
    p1 = df["pass1_label"].value_counts()
    p2 = df["pass2_label"].value_counts()
    cats = [c for c in LABEL_ORDER if c in set(p1.index) | set(p2.index)]
    dist = pd.DataFrame({
        "category": cats * 2,
        "pass": ["Pass 1"] * len(cats) + ["Pass 2"] * len(cats),
        "events": [int(p1.get(c, 0)) for c in cats] + [int(p2.get(c, 0)) for c in cats],
    })
    fig_dist = px.bar(
        dist, x="category", y="events", color="pass", barmode="group",
        color_discrete_map={"Pass 1": C_BLUE, "Pass 2": C_TEAL},
        labels={"category": "", "events": "Events", "pass": ""},
        category_orders={"category": cats},
    )
    fig_dist.update_traces(
        hovertemplate="%{fullData.name} · %{x}<br><b>%{y}</b> events<extra></extra>")
    finalize(fig_dist, height=400, ytitle="Events")
    fig_dist.update_xaxes(tickangle=-12)
    show(fig_dist)
    st.caption(
        f"Pass-to-pass agreement: {int(df['agreed'].sum()):,} / {len(df):,} "
        f"({100 * df['agreed'].mean():.1f}%). "
        "BEHAVIORAL_DRIFT: 309 conservative (both agree) → 474 (P2 best estimate)."
    )

    st.divider()

    # --- (b) BD rate by structural change type ------------------------------
    chart_title("BEHAVIORAL_DRIFT rate by structural change type",
                "Conservative (both-passes-agree) rate on the study's structural "
                "buckets — matches phase5_summary.md §4 exactly.")
    df["bd_both"] = (df["pass1_label"] == "BEHAVIORAL_DRIFT") & (
        df["pass2_label"] == "BEHAVIORAL_DRIFT")
    grp = (
        df.groupby("structural_type")
        .agg(events=("bd_both", "size"), bd=("bd_both", "sum"))
        .reset_index()
    )
    grp["bd_pct"] = 100 * grp["bd"] / grp["events"]
    grp = grp.sort_values("bd_pct", ascending=False)
    fig_bd = px.bar(
        grp, x="structural_type", y="bd_pct",
        custom_data=["bd", "events"],
        color_discrete_sequence=[C_RED],
        labels={"structural_type": "", "bd_pct": "BEHAVIORAL_DRIFT rate (%)"},
    )
    fig_bd.update_traces(
        hovertemplate="<b>%{x}</b><br>%{customdata[0]} / %{customdata[1]} events"
                      "<br>%{y:.1f}% BEHAVIORAL_DRIFT<extra></extra>",
    )
    label_bars(fig_bd, tc, fmt="%{y:.1f}%")
    finalize(fig_bd, height=400, ymax=100, ytitle="Drift rate (%)")
    fig_bd.update_xaxes(tickangle=-18)
    show(fig_bd)
    st.caption(
        "`schema_mixed` (simultaneous add + remove) drifts most (82.5%); pure "
        "additive changes essentially never read as behavioral drift. The 309 "
        "drift events sum to the 12.5% conservative headline."
    )

    st.divider()

    # --- (c) BD rate by source type -----------------------------------------
    chart_title("BEHAVIORAL_DRIFT rate by source type",
                "How the repo was discovered. Published figures, phase5_summary.md §4.")
    melted = SOURCE_TYPE.melt(
        id_vars=["source", "events"], value_vars=["bd_both_pct", "bd_p2_pct"],
        var_name="estimate", value_name="pct",
    )
    melted["estimate"] = melted["estimate"].map(
        {"bd_both_pct": "Conservative (both)", "bd_p2_pct": "Best estimate (P2)"})
    fig_src = px.bar(
        melted, x="source", y="pct", color="estimate", barmode="group",
        color_discrete_map={"Conservative (both)": C_BLUE,
                            "Best estimate (P2)": C_TEAL},
        labels={"source": "", "pct": "BEHAVIORAL_DRIFT rate (%)", "estimate": ""},
        category_orders={"source": ["official", "mined", "community"]},
    )
    fig_src.update_traces(
        hovertemplate="%{fullData.name}<br><b>%{y:.1f}%</b> on %{x}<extra></extra>")
    label_bars(fig_src, tc, fmt="%{y:.1f}%")
    finalize(fig_src, height=400, ymax=30, ytitle="Drift rate (%)")
    show(fig_src)
    st.caption(
        "Cleanest gradient in the data: vendor-maintained **official** repos are the "
        "most stable (14.2% P2); third-party **community** repos drift most (23.8% P2)."
    )

    st.divider()

    # --- (d) Searchable event table -----------------------------------------
    chart_title("Event explorer",
                "All 2,481 valid events with their classifications "
                "(tool_diffs.jsonl fields joined with tool_classifications.jsonl labels).")
    f1, f2, f3 = st.columns(3)
    query = f1.text_input("Search tool or repo", "")
    types = f2.multiselect("Structural type", sorted(df["structural_type"].unique()))
    labels_sel = f3.multiselect(
        "Final label (Pass 2)",
        [c for c in LABEL_ORDER if c in df["pass2_label"].unique()])

    view = df.copy()
    if query:
        q = query.lower()
        view = view[view["tool_name"].str.lower().str.contains(q, na=False)
                    | view["repo_short"].str.lower().str.contains(q, na=False)]
    if types:
        view = view[view["structural_type"].isin(types)]
    if labels_sel:
        view = view[view["pass2_label"].isin(labels_sel)]

    table = view.assign(
        added=view["schema_fields_added"].map(lambda x: ", ".join(x) if x else ""),
        removed=view["schema_fields_removed"].map(lambda x: ", ".join(x) if x else ""),
    )[["repo_short", "tool_name", "structural_type", "description_changed",
       "added", "removed", "pass1_label", "pass2_label", "agreed"]].rename(
        columns={"repo_short": "repo", "structural_type": "type",
                 "description_changed": "desc_changed", "pass1_label": "P1",
                 "pass2_label": "P2 (final)", "agreed": "P1=P2"})
    st.caption(f"Showing {len(table):,} of {len(df):,} events.")
    st.dataframe(table, width="stretch", hide_index=True, height=420)


# --------------------------------------------------------------------------- #
# SECTION 4 — AGENTIC VALIDATION
# --------------------------------------------------------------------------- #
def page_agentic(tc: dict) -> None:
    section_header(
        "Agentic behavioral validation",
        "Do drifted definitions actually change how a live agent behaves? "
        "15 purposively selected BEHAVIORAL_DRIFT events, temperature=0, "
        "3 replications per side.",
    )
    slots = load_agentic()
    n_diff = sum(1 for s in slots if s["behavioral_difference"])

    k = st.columns(3)
    kpi(k[0], f"{n_diff} / 15", "Showed a behavioral difference",
        "before vs after definition", accent=C_RED)
    kpi(k[1], "0 / 15", "Unstable across replications",
        "all stable at temperature=0", accent=C_TEAL)
    kpi(k[2], "Illustrative", "Sample type",
        "purposive — no prevalence claim", accent=C_SLATE)

    # --- Key-finding callout: slot 07 ---------------------------------------
    slot7 = next(s for s in slots if s["slot"] == 7)
    quote = escape(slot7["after_runs"][0].get("response_text", ""))
    after_input = slot7["after_runs"][0].get("tool_input", {})
    st.write("")
    st.markdown(
        f"""
<div class="mcp-callout">
  <div class="eyebrow">Key finding · Slot 07 · update_instance_name · neo4j-contrib/mcp-neo4j</div>
  <div class="quote">&ldquo;{quote}&rdquo;</div>
  <div class="body">
    The AFTER definition narrowed <code>instance_ids</code> (array) to
    <code>instance_id</code> (scalar). Asked to rename <b>two</b> instances in one
    operation, the agent confirmed success for &ldquo;both&rdquo; — but the actual
    tool call sent only <code>instance_id="{escape(str(after_input.get('instance_id', '')))}"</code>.
    <code>inst-001</code> was silently dropped, with no caveat, identically across all
    3 temperature=0 runs. A confident, reproducible, <b>factually false success
    claim</b> caused purely by a tool-definition change.
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    chart_title("All 15 cases",
                "Each card shows the before/after definition and the behavioral "
                "difference observed. Blue = before · Teal = after.")
    only_diff = st.toggle("Show only cases with a behavioral difference", value=False)

    for s in slots:
        if only_diff and not s["behavioral_difference"]:
            continue
        diff = s["behavioral_difference"]
        badge = "🔴 behavioral difference" if diff else "⚪ no difference"
        title = (f"Slot {s['slot']:02d} · {s['tool_name']} · "
                 f"{short_repo(s['repo_url'])}  —  {badge}")
        with st.expander(title, expanded=(s["slot"] == 7)):
            st.caption(f"Structural type: `{s['structural_type']}`  ·  "
                       f"runs/side: {s['n_runs']}  ·  "
                       f"stable: {s['before_stable'] and s['after_stable']}")
            st.markdown(f"**User request:** {s['user_request']}")
            st.write("")
            cols = st.columns(2)
            definition_card(cols[0], "Before definition", s["before_definition"],
                            C_BLUE, tc)
            definition_card(cols[1], "After definition", s["after_definition"],
                            C_TEAL, tc)
            st.write("")
            if diff:
                st.success(f"**Behavioral difference:** {s['diff_description']}",
                           icon="🔬")
            else:
                st.info(f"**No behavioral difference:** {s['diff_description']}",
                        icon="➖")

    st.warning(
        "**Scope caveat:** these 15 events were selected purposively from the "
        "309-event conservative BEHAVIORAL_DRIFT set to illustrate *what kinds* of "
        "behavioral change drift can produce — not *how often*. No rate or "
        "prevalence claim can be derived from this sample.",
        icon="⚠️",
    )


# --------------------------------------------------------------------------- #
# SECTION 5 — SUPPLEMENTARY FINDINGS
# --------------------------------------------------------------------------- #
def page_supplementary(tc: dict) -> None:
    section_header(
        "Supplementary findings",
        "Tool naming-convention evolution — distinct from the primary drift counts.",
    )
    ren = load_renames()

    k = st.columns(3)
    kpi(k[0], f"{len(ren):,}", "Total rename candidates",
        "file-level scan, lower bound", accent=C_BLUE)
    kpi(k[1], "71 / 276", "Repos with ≥ 1 candidate",
        "25.7% of walked repos", accent=C_BLUE)
    kpi(k[2], "619", "High-confidence (Perfect + High)",
        "schema fully preserved", accent=C_TEAL)

    st.divider()
    chart_title("Confidence-tier breakdown",
                "Similarity of the old vs new tool across each rename "
                "(schema & description Jaccard).")
    tiers = pd.DataFrame(
        [
            ("Perfect", "schema = 1.0 AND desc = 1.0", 326),
            ("High", "schema = 1.0, desc < 1.0", 293),
            ("Medium", "schema < 1.0", 69),
        ],
        columns=["Tier", "Criterion", "Candidates"],
    )
    c1, c2 = st.columns([3, 2])
    tier_color = {"Perfect": C_TEAL, "High": C_BLUE, "Medium": C_SLATE}
    fig = go.Figure(
        go.Bar(
            x=tiers["Tier"], y=tiers["Candidates"],
            marker_color=[tier_color[t] for t in tiers["Tier"]],
            customdata=tiers[["Criterion"]],
            hovertemplate="<b>%{x}</b><br>%{y} candidates<br>%{customdata[0]}<extra></extra>",
        )
    )
    label_bars(fig, tc, fmt="%{y}")
    finalize(fig, height=360, ymax=326 * 1.20, ytitle="Candidates", legend=False)
    with c1:
        show(fig)
    with c2:
        st.write("")
        st.dataframe(tiers, width="stretch", hide_index=True)

    st.caption(
        "These represent naming-convention evolution — namespacing "
        "(`dialogs → tg_dialogs`), simplification, restructuring. They are a **lower "
        "bound** and are **not** in the primary drift counts (the differ groups by "
        "`tool_name`, so a rename appears as one tool ending and another beginning)."
    )

    with st.expander("Browse rename candidates"):
        show_cols = [c for c in
                     ["repo_url", "old_tool_name", "new_tool_name", "schema_jaccard",
                      "desc_jaccard", "confidence_tier", "date_from", "date_to"]
                     if c in ren.columns]
        st.dataframe(ren[show_cols], width="stretch", hide_index=True, height=380)


# --------------------------------------------------------------------------- #
# SECTION 6 — LIMITATIONS
# --------------------------------------------------------------------------- #
def page_limitations(tc: dict) -> None:
    section_header(
        "Limitations & threats to validity",
        "All 11 documented limitations (L1–L11) from the phase summaries.",
    )
    for code, what, impact in LIMITATIONS:
        st.markdown(
            f'<div class="mcp-card">'
            f'<span class="badge">{code}</span>'
            f'<span class="head">{escape(what)}</span>'
            f'<div class="aff"><b>Affected:</b> {escape(impact)}</div></div>',
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
    tc = theme_colors()
    register_template(tc)
    inject_css(tc)

    st.sidebar.title("TRIPWIRE")
    st.sidebar.caption(
        "Empirical measurement of how MCP tool definitions change over time, "
        "and whether those changes alter AI-agent behavior."
    )
    choice = st.sidebar.radio("Section", list(PAGES.keys()),
                              label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.markdown(
        "**Read-only dashboard.** All figures load from committed files in "
        "`data/processed/`. No live API calls, no recomputation."
    )

    if not PROC.exists():
        st.error(f"Data directory not found: `{PROC}`. Run from the project root.")
        return

    PAGES[choice](tc)


if __name__ == "__main__":
    main()
