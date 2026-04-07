"""
Inference Group — Gov Contracts Dashboard
==========================================
Live search across Contracts Finder and Find a Tender.

Run with:  streamlit run dashboard.py
"""

import html as html_mod
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.scrapers import contracts_finder, find_a_tender
from src.analysis.qc_agents import run_all_qc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Noise patterns to strip from descriptions
_NOISE = re.compile(
    r"(\*{3,}[^*]*\*{3,}"  # ***** AWARD NOTICE *****
    r"|please\s+note[:\s][^.]*\."
    r"|this\s+is\s+(a\s+)?contract\s+award\s+notice[^.]*\."
    r"|a\s+contract\/?agreement\s+has\s+been\s+awarded[^.]*\."
    r"|this\s+notice\s+is\s+for\s+information\s+only[^.]*\."
    r"|this\s+procurement\s+is\s+being\s+concluded[^.]*\."
    r"|this\s+contract\s+was\s+awarded\s+from[^.]*\."
    r"|for\s+more\s+information[^.]*\."
    r"|please\s+refer\s+to\s+https?://[^\s]*"
    r"|contract\s+period:\s*[^.]*\.?"
    r"|total\s+award\s+value[^.]*\.)",
    re.IGNORECASE,
)


def _clean_description(raw: str) -> str:
    """Strip HTML, boilerplate, and noise from a description."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html_mod.unescape(text)
    text = text.replace("&amp;", "&")
    # Remove truncation artefacts from FaT ("...word...word...")
    text = re.sub(r"\.{3,}", " ", text)
    text = re.sub(r"^[\s.]+", "", text)  # strip leading dots/spaces
    text = _NOISE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def summarise(description: str, title: str, buyer: str = "", value=None) -> str:
    """Extract what the contract involves — scope of work only.

    Designed for quick scan to decide: pursue or skip?
    """
    scope = ""

    if description and description.strip():
        text = _clean_description(description)

        if text:
            # Split into sentences and find the one that best describes the deliverable
            sentences = re.split(r"(?<=[.!?])\s+", text)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

            # Pick the best scope sentence — the first one that describes actual work
            scope_words = (
                "deliver", "develop", "provid", "build", "design", "creat",
                "implement", "deploy", "support", "consult", "advis", "research",
                "analys", "automat", "model", "platform", "framework", "system",
                "solution", "service", "strategy", "roadmap", "discovery",
                "pilot", "proof of concept", "transformation",
            )
            for s in sentences:
                s_lower = s.lower()
                # Skip award/appointment announcements — they don't describe the work
                if any(skip in s_lower for skip in (
                    "have been appointed", "has been appointed", "was appointed",
                    "has been awarded", "was awarded to", "have been selected",
                    "award of contract", "awarded from the",
                )):
                    continue
                if any(w in s_lower for w in scope_words):
                    # Trim to ~25 words max
                    words = s.split()
                    if len(words) > 25:
                        cut = " ".join(words[:25])
                        last_stop = cut.rfind(".")
                        last_comma = cut.rfind(",")
                        break_at = max(last_stop, last_comma)
                        if break_at > len(cut) // 2:
                            scope = cut[:break_at + 1].rstrip(",")
                        else:
                            scope = cut + "..."
                    else:
                        scope = s.rstrip(".")
                    break

            # Fallback: first non-trivial sentence
            if not scope:
                for s in sentences:
                    if len(s.split()) >= 5:
                        words = s.split()
                        scope = " ".join(words[:25]).rstrip(".")
                        if len(words) > 25:
                            scope += "..."
                        break

    # If still nothing, use the title as scope
    if not scope:
        scope = title

    return scope

# ---------------------------------------------------------------------------
# Brand palette
# ---------------------------------------------------------------------------

BRAINWAVE_BLUE = "#30475E"
INNOVATION_BURST = "#D08770"
TECH_SLATE = "#B0B0B0"
NEPTUNE = "#70BAD0"

# ---------------------------------------------------------------------------
# Minimal CSS — widget colours from .streamlit/config.toml [theme]
# ---------------------------------------------------------------------------

BRAND_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&family=Merriweather:wght@400;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Roboto', sans-serif;
    }}

    .dashboard-header {{
        background: linear-gradient(135deg, {BRAINWAVE_BLUE} 0%, #3d5a73 100%);
        padding: 1.5rem 2rem;
        border-radius: 10px;
        margin-bottom: 1.5rem;
    }}
    .dashboard-header h1 {{
        font-family: 'Merriweather', serif;
        color: #ffffff;
        margin: 0;
        font-size: 1.75rem;
    }}
    .dashboard-header p {{
        color: {NEPTUNE};
        margin: 0.25rem 0 0 0;
        font-size: 0.9rem;
    }}

    .sidebar-brand {{
        text-align: center;
        padding: 0.25rem 0 1rem 0;
    }}

    div[data-testid="stMetric"] {{
        background: #ffffff;
        border: 1px solid #e2e6ea;
        border-left: 4px solid {BRAINWAVE_BLUE};
        border-radius: 8px;
        padding: 0.75rem 1rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}

    .detail-card {{
        border-left: 3px solid {INNOVATION_BURST};
        padding: 0.75rem 1rem;
        margin-bottom: 0.75rem;
        background: #f9fafb;
        border-radius: 6px;
    }}
    .detail-card strong {{ color: {BRAINWAVE_BLUE}; font-size: 1rem; }}
    .detail-card .meta {{ color: #6b7785; font-size: 0.85rem; }}
    .detail-card .desc {{ margin: 0.4rem 0 0 0; font-size: 0.9rem; color: #3a4550; }}

    .page-info {{
        text-align: center;
        color: #6b7785;
        font-size: 0.85rem;
        padding: 0.5rem 0;
    }}

    .truncation-warning {{
        background: #fff3cd;
        border: 1px solid #ffc107;
        border-radius: 6px;
        padding: 0.5rem 1rem;
        font-size: 0.85rem;
        color: #856404;
        margin-bottom: 1rem;
    }}

    [data-testid="stSidebar"] .stMultiSelect span[data-baseweb="tag"] {{
        background-color: {BRAINWAVE_BLUE} !important;
        color: #ffffff !important;
    }}
</style>
"""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config" / "search_criteria.json"


def load_config() -> dict:
    """Load config without aggressive caching so edits take effect."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        st.error(f"Config error: {e}")
        return {"keywords": [], "cpv_codes": [], "default_filters": {}}


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Gov Contracts — Inference Group",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(BRAND_CSS, unsafe_allow_html=True)

st.markdown("""
<div class="dashboard-header">
    <h1>Government Contracts Dashboard</h1>
    <p>Live data from Contracts Finder &amp; Find a Tender &mdash; Inference Group</p>
</div>
""", unsafe_allow_html=True)

config = load_config()
defaults = config.get("default_filters", {})

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.markdown(f"""
<div class="sidebar-brand">
    <span style="font-family:Merriweather,serif; font-size:1.15rem; font-weight:700; color:{BRAINWAVE_BLUE};">
        inference</span>
    <span style="font-family:Merriweather,serif; font-size:1.15rem; color:#6b7785;">
        group</span>
</div>
""", unsafe_allow_html=True)

st.sidebar.header("Search Filters")

# Source
sources = st.sidebar.multiselect(
    "Sources",
    ["Contracts Finder", "Find a Tender"],
    default=["Contracts Finder", "Find a Tender"],
)

# Keywords
all_keywords = config.get("keywords", [])
selected_keywords = st.sidebar.multiselect(
    "Keywords (combined OR query)",
    all_keywords,
    default=all_keywords,
    help="All selected keywords are combined into one search query.",
)
custom_keyword = st.sidebar.text_input("Add custom keyword", "")
if custom_keyword:
    selected_keywords.append(custom_keyword)

# Date range
default_days = defaults.get("published_days_back", 30)
days_back = st.sidebar.slider("Published in last N days", 1, 90, default_days)
date_from = datetime.now() - timedelta(days=days_back)
date_to = datetime.now()

st.sidebar.markdown("---")

# Value range
st.sidebar.subheader("Contract Value")
vc1, vc2 = st.sidebar.columns(2)
default_min = defaults.get("min_value") or 0
min_value = vc1.number_input("Min (\u00a3)", min_value=0, value=default_min, step=10000)
max_value = vc2.number_input("Max (\u00a3, 0=no limit)", min_value=0, value=0, step=10000)
min_val = min_value if min_value > 0 else None
max_val = max_value if max_value > 0 else None

# Location
location = st.sidebar.text_input("Location (Contracts Finder only)", "")

# Stage — applied to Find a Tender; optionally to Contracts Finder
stage_options = ["tender", "planning", "award"]
default_stages = defaults.get("stages", ["tender", "planning"])
selected_stages = st.sidebar.multiselect("Stages (Find a Tender)", stage_options, default=default_stages)
filter_cf_status = st.sidebar.checkbox("Also apply stage filter to Contracts Finder", value=False,
                                        help="When off, CF returns all statuses (Open, Awarded, etc). "
                                             "Turn on to restrict CF to matching statuses only.")

# CPV codes
cpv_codes = config.get("cpv_codes", [])
use_cpv = st.sidebar.checkbox("Filter by CPV codes (IT/Data/R&D)", value=True,
                               help="Restricts Contracts Finder results to IT services, data services, and R&D CPV codes. "
                                    "Disable to search all categories.")

st.sidebar.markdown("---")

# Results per page
view_options = ["10", "25", "50", "100", "All"]
view_choice = st.sidebar.selectbox("Results per page", view_options, index=1)

# Search
search_clicked = st.sidebar.button("Search", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

if search_clicked:
    if not selected_keywords:
        st.warning("Select at least one keyword.")
        st.stop()

    all_results = []
    total_hit_count = 0
    cf_truncated = False
    total_steps = len(sources)
    step = 0
    progress = st.progress(0, text="Fetching contracts...")

    # Map stages to CF statuses (only if user opted in)
    cf_statuses = None
    if filter_cf_status:
        cf_statuses = [contracts_finder.STAGE_TO_STATUS[s]
                       for s in selected_stages
                       if s in contracts_finder.STAGE_TO_STATUS] or None

    if "Contracts Finder" in sources:
        step += 1
        progress.progress(step / max(total_steps, 1), text="Contracts Finder: searching...")
        try:
            results, hit_count = contracts_finder.fetch_notices(
                keywords=selected_keywords,
                published_from=date_from,
                published_to=date_to,
                min_value=min_val,
                max_value=max_val,
                location=location or None,
                statuses=cf_statuses,
                cpv_codes=cpv_codes if use_cpv else None,
            )
            all_results.extend(results)
            total_hit_count += hit_count
            if hit_count > len(results):
                cf_truncated = True
        except Exception as e:
            st.warning(f"Contracts Finder error: {e}")

    if "Find a Tender" in sources:
        step += 1
        progress.progress(step / max(total_steps, 1), text="Find a Tender: searching...")
        try:
            results = find_a_tender.fetch_notices(
                keywords=selected_keywords,
                stages=selected_stages,
                min_value=min_val,
                max_value=max_val,
                published_from=date_from,
                published_to=date_to,
                max_pages=5,
            )
            all_results.extend(results)
        except Exception as e:
            st.warning(f"Find a Tender error: {e}")

    progress.empty()

    if not all_results:
        st.info("No results found. Try broadening your filters.")
        st.stop()

    # Deduplicate by ocid
    seen: set[str] = set()
    unique = []
    for r in all_results:
        key = r["ocid"]
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    st.session_state["results"] = unique
    st.session_state["current_page"] = 0
    st.session_state["cf_truncated"] = cf_truncated
    st.session_state["cf_hit_count"] = total_hit_count

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

if "results" in st.session_state and st.session_state["results"]:
    results = st.session_state["results"]
    df = pd.DataFrame(results)

    # Truncation warning
    if st.session_state.get("cf_truncated"):
        hit_count = st.session_state.get("cf_hit_count", 0)
        st.markdown(
            f'<div class="truncation-warning">'
            f'Contracts Finder returned {len(df[df["source"]=="Contracts Finder"])} of '
            f'{hit_count} total matches. Refine your keywords or filters to see all results.'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", len(df))
    m2.metric("Contracts Finder", len(df[df["source"] == "Contracts Finder"]))
    m3.metric("Find a Tender", len(df[df["source"] == "Find a Tender"]))
    has_val = df["total_value"].notna()
    m4.metric("Avg Value", f"\u00a3{df.loc[has_val, 'total_value'].mean():,.0f}" if has_val.any() else "N/A")

    st.markdown("---")

    # Normalise notice types for consistent cross-source filtering
    def _normalise_status(nt: str) -> str:
        nt_lower = nt.lower()
        if "open" in nt_lower or nt_lower == "tender":
            return "Open"
        if "awarded" in nt_lower or "contract detail" in nt_lower:
            return "Awarded"
        if "pipeline" in nt_lower or "planning" in nt_lower or "engagement" in nt_lower:
            return "Planning"
        if "closed" in nt_lower:
            return "Closed"
        if "termination" in nt_lower:
            return "Terminated"
        return nt

    # Result filters
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        filter_ct = st.multiselect("Type", sorted(df["ct"].dropna().unique()), default=[])
    with f2:
        filter_source = st.multiselect("Source", sorted(df["source"].unique()), default=[])
    with f3:
        status_col = df["notice_type"].apply(_normalise_status)
        filter_status = st.multiselect("Status", sorted(status_col.dropna().unique()), default=[])
    with f4:
        filter_text = st.text_input("Search title", "")

    filtered = df.copy()
    filtered["status"] = filtered["notice_type"].apply(_normalise_status)

    # Pre-compute summaries once for the full result set (reused in table, cards, and CSV)
    if "_summary" not in filtered.columns:
        filtered["_summary"] = [
            summarise(row["description"], row["title"], row["buyer"], row.get("total_value"))
            for _, row in filtered.iterrows()
        ]

    if filter_ct:
        filtered = filtered[filtered["ct"].isin(filter_ct)]
    if filter_source:
        filtered = filtered[filtered["source"].isin(filter_source)]
    if filter_status:
        filtered = filtered[filtered["status"].isin(filter_status)]
    if filter_text:
        filtered = filtered[filtered["title"].str.contains(filter_text, case=False, na=False)]

    # Sort by published date descending (newest first)
    filtered = filtered.sort_values("published_date", ascending=False, na_position="last")
    filtered = filtered.reset_index(drop=True)

    # Pagination
    total_results = len(filtered)
    show_all = view_choice == "All"
    per_page = total_results if show_all else int(view_choice)
    total_pages = 1 if show_all else max(1, math.ceil(total_results / per_page))

    # Sync pagination state after filter changes
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = 0
    current_page = min(st.session_state["current_page"], total_pages - 1)
    st.session_state["current_page"] = current_page

    start_idx = current_page * per_page
    end_idx = min(start_idx + per_page, total_results)
    page_df = filtered.iloc[start_idx:end_idx]

    # Build display table
    display = pd.DataFrame()
    display["ID"] = range(start_idx + 1, end_idx + 1)
    display["Name"] = page_df["title"].values
    display["Reference"] = page_df["reference"].values
    display["Published Date"] = page_df["published_date"].values
    display["Closing Date"] = page_df["closing_date"].values
    display["Type"] = page_df["ct"].values
    display["Status"] = page_df["status"].values
    display["Total Value"] = [
        f"\u00a3{v:,.0f}" if pd.notna(v) else "\u2014"
        for v in page_df["total_value"].values
    ]
    display["Buyer"] = page_df["buyer"].values
    display["Location"] = page_df["location"].values
    display["Source"] = page_df["source"].values
    display["Summary"] = page_df["_summary"].values
    display["Link"] = page_df["link"].values

    # Table
    label = f"Results ({total_results})" if show_all else f"Results ({start_idx+1}\u2013{end_idx} of {total_results})"
    st.subheader(label)

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=min(700, 55 + len(display) * 38),
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="View"),
            "Name": st.column_config.TextColumn("Name", width="large"),
            "Summary": st.column_config.TextColumn("Summary", width="large"),
            "ID": st.column_config.NumberColumn("ID", width="small"),
            "Type": st.column_config.TextColumn("Type", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Total Value": st.column_config.TextColumn("Value", width="small"),
            "Source": st.column_config.TextColumn("Source", width="small"),
        },
    )

    # Pagination controls
    if not show_all:
        st.markdown(
            f'<div class="page-info">Page {current_page+1} of {total_pages}</div>',
            unsafe_allow_html=True,
        )
        p1, p2, _, p4, p5 = st.columns([1, 1, 2, 1, 1])
        with p1:
            if st.button("\u00ab First", disabled=(current_page == 0), use_container_width=True):
                st.session_state["current_page"] = 0
                st.rerun()
        with p2:
            if st.button("\u2039 Prev", disabled=(current_page == 0), use_container_width=True):
                st.session_state["current_page"] = current_page - 1
                st.rerun()
        with p4:
            if st.button("Next \u203a", disabled=(current_page >= total_pages - 1), use_container_width=True):
                st.session_state["current_page"] = current_page + 1
                st.rerun()
        with p5:
            if st.button("Last \u00bb", disabled=(current_page >= total_pages - 1), use_container_width=True):
                st.session_state["current_page"] = total_pages - 1
                st.rerun()

    st.markdown("---")

    # Expandable detail cards — XSS-safe with auto-summaries
    with st.expander("Expand full details"):
        for _, row in page_df.iterrows():
            val_str = f"\u00a3{row['total_value']:,.0f}" if pd.notna(row["total_value"]) else "Value TBC"
            summary = row.get("_summary", summarise(row.get("description", ""), row["title"], row["buyer"], row.get("total_value")))
            title_safe = html_mod.escape(str(row["title"]))
            buyer_safe = html_mod.escape(str(row["buyer"]))
            summary_safe = html_mod.escape(summary)

            closing = row.get("closing_date", "")
            closing_str = f" &bull; Closes: {html_mod.escape(str(closing))}" if closing else ""

            st.markdown(
                f'<div class="detail-card">'
                f'<strong>{title_safe}</strong><br>'
                f'<span class="meta">{buyer_safe} &bull; {html_mod.escape(row["ct"])} &bull; {val_str}{closing_str}</span>'
                f'<p class="desc">{summary_safe}</p>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # CSV export — uses cached summaries and normalised status
    export = filtered.copy()
    export_display = pd.DataFrame({
        "Name": export["title"],
        "Reference": export["reference"],
        "Published Date": export["published_date"],
        "Closing Date": export["closing_date"],
        "Type": export["ct"],
        "Status": export["status"],
        "Total Value": export["total_value"],
        "Buyer": export["buyer"],
        "Location": export["location"],
        "Source": export["source"],
        "Summary": export["_summary"],
        "Link": export["link"],
    })
    csv = export_display.to_csv(index=False)
    st.download_button("Download CSV", csv, "gov_contracts.csv", "text/csv")

    # ------------------------------------------------------------------
    # QC Panel
    # ------------------------------------------------------------------

    st.markdown("---")
    with st.expander("Quality Control Report"):

        qc = run_all_qc(filtered.to_dict("records"), filtered["_summary"].tolist())
        s = qc["summary"]

        # --- Overview metrics ---
        st.markdown(f"##### Overview")
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Relevance", f"{s['avg_relevance_score']}/5")
        q2.metric("Summary Quality", f"{s['avg_summary_quality']}/5")
        q3.metric("Completeness", f"{s['avg_completeness']}/10")
        q4.metric("Duplicate Groups", s["duplicate_groups"])

        # --- Tabs for each agent ---
        tab1, tab2, tab3, tab4 = st.tabs([
            f"Relevance ({s['false_positives']} flagged)",
            f"Summaries ({s['low_quality_summaries']} issues)",
            f"Completeness ({s['incomplete_records']} gaps)",
            f"Duplicates ({s['duplicate_groups']} groups)",
        ])

        # Tab 1: Keyword Relevance
        with tab1:
            flagged = [f for f in qc["relevance"] if f["is_false_positive"]]
            if flagged:
                st.warning(f"{len(flagged)} potential false positive(s) detected")
                for f in flagged:
                    st.markdown(
                        f"- **{html_mod.escape(f['title'][:60])}** ({f['source']}) "
                        f"— Score: {f['relevance_score']}/5 — {f['reason']}"
                    )
            else:
                st.success("All results appear relevant to AI/Data")

            # Show score distribution
            scores = [f["relevance_score"] for f in qc["relevance"]]
            if scores:
                score_df = pd.DataFrame({"Relevance Score": scores})
                st.bar_chart(score_df["Relevance Score"].value_counts().sort_index())

        # Tab 2: Summary Quality
        with tab2:
            poor = [f for f in qc["summary_quality"] if f["quality_score"] <= 2]
            if poor:
                st.warning(f"{len(poor)} summary/summaries need improvement")
                for f in poor:
                    st.markdown(
                        f"- **{html_mod.escape(f['title'])}**\n"
                        f"  - Summary: *{html_mod.escape(f['summary'][:80])}*\n"
                        f"  - Issues: {', '.join(f['issues'])}"
                    )
            else:
                st.success("All summaries describe deliverables")

            ok = [f for f in qc["summary_quality"] if f["quality_score"] >= 4]
            st.caption(f"{len(ok)}/{len(qc['summary_quality'])} summaries rated good or excellent")

        # Tab 3: Data Completeness
        with tab3:
            gaps = [f for f in qc["completeness"] if f["missing_fields"]]
            if gaps:
                # Aggregate most common missing fields
                from collections import Counter
                field_counts = Counter()
                for f in gaps:
                    for m in f["missing_fields"]:
                        field_counts[m] += 1

                st.markdown("**Most commonly missing fields:**")
                for field, count in field_counts.most_common():
                    pct = count / len(qc["completeness"]) * 100
                    st.markdown(f"- {field}: missing in {count} results ({pct:.0f}%)")

                st.markdown("")
                st.markdown("**Records with most gaps:**")
                worst = sorted(gaps, key=lambda f: len(f["missing_fields"]), reverse=True)[:5]
                for f in worst:
                    st.markdown(
                        f"- **{html_mod.escape(f['title'])}** ({f['source']}) "
                        f"— {f['fields_present']} fields — missing: {', '.join(f['missing_fields'])}"
                    )
            else:
                st.success("All records are complete")

            # Warnings
            all_warnings = [w for f in qc["completeness"] for w in f.get("warnings", [])]
            if all_warnings:
                with st.expander(f"{len(all_warnings)} data warnings"):
                    for w in all_warnings[:20]:
                        st.caption(f"- {w}")

        # Tab 4: Duplicates
        with tab4:
            dupes = qc["duplicates"]
            if dupes:
                st.warning(f"{len(dupes)} duplicate group(s) found")
                for g in dupes:
                    st.markdown(f"**{html_mod.escape(g['title_sample'])}** — {g['relationship']}")
                    for n in g["notices"]:
                        val_str = f" — \u00a3{n['value']:,.0f}" if n.get("value") else ""
                        st.markdown(
                            f"  - {n['source']} | {n['type']}{val_str} | `{n['ocid']}`"
                        )
                    st.markdown("")
            else:
                st.success("No duplicates detected")

elif "results" in st.session_state:
    st.info("No results found.")
else:
    st.markdown(
        '<div style="text-align:center; padding:3rem; color:#6b7785;">'
        '<p style="font-size:1.1rem;">Configure filters in the sidebar and click '
        '<strong style="color:#D08770;">Search</strong> to fetch live contracts.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
