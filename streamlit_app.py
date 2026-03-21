"""
=============================================================
  NCAA D-I Men's Basketball Transfer Portal Tracker
  streamlit_app.py  |  v1.0
=============================================================
  PUBLIC-FACING WEB DASHBOARD

  Run locally:
    pip install streamlit plotly
    streamlit run streamlit_app.py

  Deploy free on Streamlit Cloud:
    1. Push your repo to GitHub (include this file +
       portal_tracker_cbbd.py + formula_engine.py)
    2. Go to share.streamlit.io → New app → select repo
    3. Set CBBD_API_KEY in Streamlit Secrets:
       Settings → Secrets → paste:
         CBBD_API_KEY = "your_key_here"
    4. Click Deploy — live URL in ~2 minutes

  For Vercel/other hosts: wrap in FastAPI instead.
=============================================================
"""

import streamlit as st
import pandas as pd
import numpy as np
import os
import sys
import json
from datetime import datetime, date

# ── Page config (must be first Streamlit call) ──
st.set_page_config(
    page_title="NCAA Transfer Portal Tracker",
    page_icon="⛹",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Path setup ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Custom CSS ──
st.markdown("""
<style>
  /* Dark theme adjustments */
  .main { background-color: #0d1117; }
  .stDataFrame { font-size: 12px; }

  /* Metric cards */
  div[data-testid="metric-container"] {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 12px 16px;
  }

  /* Tier badges */
  .tier-elite  { color: #58a6ff; font-weight: bold; }
  .tier-high   { color: #3fb950; font-weight: bold; }
  .tier-solid  { color: #d29922; font-weight: bold; }
  .tier-fringe { color: #e3b341; }
  .tier-depth  { color: #8b949e; }

  /* Section headers */
  .section-header {
      font-size: 11px;
      font-weight: bold;
      color: #8b949e;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 8px;
  }

  /* System labels */
  .sys1-label { color: #1f6feb; font-weight: bold; }
  .sys2-label { color: #8957e5; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
#  API KEY  — reads from Streamlit Secrets or env
# ──────────────────────────────────────────────

def get_api_key():
    # Streamlit Cloud secrets
    if hasattr(st, "secrets") and "CBBD_API_KEY" in st.secrets:
        return st.secrets["CBBD_API_KEY"]
    # Environment variable (local dev)
    return os.environ.get("CBBD_API_KEY", "")


# ──────────────────────────────────────────────
#  DATA LOADING  (cached — only hits API once per day)
# ──────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner="Fetching player data from CBBD API...")
def load_player_data(api_key, season=2026):
    """Load and score all players. Cached for 24 hours."""
    try:
        from portal_tracker_cbbd import load_data, apply_filters, compute_composite
        import portal_tracker_cbbd as pt
        pt.CBBD_API_KEY = api_key
        pt.SEASON       = season

        df_raw = load_data(api_key=api_key, use_cache=False)
        df     = apply_filters(df_raw, exclude_gr=True)
        df     = compute_composite(df)
        return df, None
    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=86400 * 7)
def load_kenpom_data():
    """Load KenPom CSV if present."""
    try:
        from formula_engine import load_kenpom
        df_kp, msg = load_kenpom("kenpom_rankings.csv")
        return df_kp, msg
    except Exception as e:
        return None, str(e)


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def tier_for_score(score):
    if score >= 85: return "ELITE"
    if score >= 70: return "HIGH"
    if score >= 55: return "SOLID"
    if score >= 40: return "FRINGE"
    return "DEPTH"

TIER_COLOURS = {
    "ELITE":  "#58a6ff",
    "HIGH":   "#3fb950",
    "SOLID":  "#d29922",
    "FRINGE": "#e3b341",
    "DEPTH":  "#8b949e",
}

def fmt_nil(v):
    if pd.isna(v): return "—"
    v = float(v)
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000:     return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"

def colour_scale(val, lo, hi, invert=False):
    """Return a hex colour on Red→Yellow→Green for a value."""
    if pd.isna(val) or hi == lo:
        return "#30363d"
    t = (float(val) - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if invert: t = 1.0 - t
    if t < 0.5:
        r = int(0xda + (0xd2 - 0xda) * t * 2)
        g = int(0x36 + (0x99 - 0x36) * t * 2)
        b = int(0x33 + (0x22 - 0x33) * t * 2)
    else:
        r = int(0xd2 + (0x3f - 0xd2) * (t - 0.5) * 2)
        g = int(0x99 + (0xb9 - 0x99) * (t - 0.5) * 2)
        b = int(0x22 + (0x50 - 0x22) * (t - 0.5) * 2)
    return f"#{r:02x}{g:02x}{b:02x}"


def style_dataframe(df_display, shade_cols_high=None, shade_cols_low=None):
    """Apply per-column gradient styling to a display dataframe."""
    if shade_cols_high is None: shade_cols_high = []
    if shade_cols_low  is None: shade_cols_low  = []

    styler = df_display.style

    for col in shade_cols_high:
        if col not in df_display.columns: continue
        series = pd.to_numeric(df_display[col], errors="coerce")
        lo, hi = series.quantile(0.05), series.quantile(0.95)
        styler = styler.background_gradient(
            subset=[col], cmap="RdYlGn", vmin=lo, vmax=hi)

    for col in shade_cols_low:
        if col not in df_display.columns: continue
        series = pd.to_numeric(df_display[col], errors="coerce")
        lo, hi = series.quantile(0.05), series.quantile(0.95)
        styler = styler.background_gradient(
            subset=[col], cmap="RdYlGn_r", vmin=lo, vmax=hi)

    return styler


# ──────────────────────────────────────────────
#  MAIN APP
# ──────────────────────────────────────────────

def main():
    # ── Header ──
    st.markdown("""
    <div style="display:flex; align-items:center; margin-bottom:8px;">
      <span style="font-size:32px; margin-right:12px;">⛹</span>
      <div>
        <h1 style="margin:0; color:#e6edf3;">NCAA D-I Transfer Portal Tracker</h1>
        <p style="margin:0; color:#8b949e; font-size:13px;">
          2025–26 Season  •  Powered by CBBD API + KenPom
          •  Dual scoring: Portal Score (0–100) & Custom Formula
        </p>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── API Key ──
    api_key = get_api_key()
    if not api_key:
        st.error("⚠️ No CBBD API key found. Set CBBD_API_KEY in Streamlit Secrets or as an environment variable.")
        with st.expander("How to set your API key"):
            st.code("""
# For local development — create .streamlit/secrets.toml:
CBBD_API_KEY = "your_key_here"

# Or set environment variable:
export CBBD_API_KEY="your_key_here"
streamlit run streamlit_app.py
            """)
        return

    # ── Load data ──
    df, err = load_player_data(api_key)
    if err:
        st.error(f"Data load error: {err}")
        if st.button("Retry"):
            st.cache_data.clear()
            st.rerun()
        return

    if df is None or len(df) == 0:
        st.warning("No data returned from API.")
        return

    # ── Sidebar filters ──
    with st.sidebar:
        st.markdown("### ⚙️ Filters")

        # Eligibility
        st.markdown('<p class="section-header">Eligibility</p>',
                    unsafe_allow_html=True)
        elig_opts = ["FR", "SO", "JR", "SR"]
        if "Elig" in df.columns:
            all_eligs = sorted(df["Elig"].dropna().unique())
            elig_opts = [e for e in ["FR","SO","JR","SR","GR","UNK"]
                         if e in all_eligs]
        elig_sel = st.multiselect("Year", elig_opts,
                                   default=[e for e in elig_opts
                                            if e != "GR"],
                                   label_visibility="collapsed")

        # Position
        st.markdown('<p class="section-header">Position</p>',
                    unsafe_allow_html=True)
        pos_opts = ["G", "F", "C"]
        if "Position" in df.columns:
            pos_opts = sorted(df["Position"].dropna().unique())
        pos_sel = st.multiselect("Position", pos_opts,
                                  default=pos_opts,
                                  label_visibility="collapsed")

        # Conference
        st.markdown('<p class="section-header">Conference</p>',
                    unsafe_allow_html=True)
        conf_opts = ["All"]
        if "Conference" in df.columns:
            conf_opts += sorted(df["Conference"].dropna().unique())
        conf_sel = st.selectbox("Conference", conf_opts,
                                 label_visibility="collapsed")

        st.divider()

        # Min score
        st.markdown('<p class="section-header">Min Portal Score (S1)</p>',
                    unsafe_allow_html=True)
        min_score = st.slider("Min Portal Score", 0, 100, 0,
                               label_visibility="collapsed")

        # Search
        st.markdown('<p class="section-header">Search</p>',
                    unsafe_allow_html=True)
        player_search = st.text_input("Player name", "",
                                       placeholder="Search player...",
                                       label_visibility="collapsed")
        team_search   = st.text_input("Team name", "",
                                       placeholder="Search team...",
                                       label_visibility="collapsed")

        st.divider()

        # Algorithm weights
        st.markdown("### ⚖️ Algorithm Weights (S1)")
        st.caption("Adjust and click Recalculate")
        w_per  = st.slider("PER",    0, 50, 25)
        w_ts   = st.slider("TS%",    0, 50, 20)
        w_ws40 = st.slider("WS/40",  0, 50, 15)
        w_usg  = st.slider("USG%",   0, 50, 10)
        w_def  = st.slider("DEF",    0, 50, 15)
        w_conf = st.slider("CONF",   0, 50, 15)

        st.divider()
        st.markdown("### 🎨 Display")
        show_heatmap  = st.checkbox("Density Heatmap", value=True)
        show_system1  = st.checkbox("Show S1 Portal Score", value=True)
        show_system2  = st.checkbox("Show S2 Custom Score", value=True)
        sort_by       = st.selectbox("Sort by", [
            "S1: Portal Score", "S2: Final Score",
            "NIL Est.", "PER", "PORPAG", "PTS", "TS%"])

    # ── Apply filters ──
    filtered = df.copy()

    if "Elig" in filtered.columns and elig_sel:
        filtered = filtered[filtered["Elig"].isin(elig_sel + ["", "UNK"])]
    if "Position" in filtered.columns and pos_sel:
        filtered = filtered[filtered["Position"].isin(pos_sel) |
                            filtered["Position"].isna() |
                            (filtered["Position"] == "")]
    if conf_sel != "All" and "Conference" in filtered.columns:
        filtered = filtered[filtered["Conference"] == conf_sel]
    if "PortalScore" in filtered.columns:
        filtered = filtered[pd.to_numeric(
            filtered["PortalScore"], errors="coerce").fillna(0) >= min_score]
    if player_search:
        filtered = filtered[
            filtered["Player"].str.lower().str.contains(
                player_search.lower(), na=False)]
    if team_search:
        filtered = filtered[
            filtered["Team"].str.lower().str.contains(
                team_search.lower(), na=False)]

    # Sort
    sort_map = {
        "S1: Portal Score": "PortalScore",
        "S2: Final Score":  "FinalScore",
        "NIL Est.":         "NILValue",
        "PER":              "PER",
        "PORPAG":           "PORPAG",
        "PTS":              "PTS",
        "TS%":              "TS%",
    }
    sort_col = sort_map.get(sort_by, "PortalScore")
    if sort_col in filtered.columns:
        filtered = filtered.sort_values(sort_col, ascending=False,
                                         na_position="last")
    filtered = filtered.reset_index(drop=True)

    # ── Summary metrics ──
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    total = len(df)
    shown = len(filtered)
    avg_s = filtered["PortalScore"].mean() if "PortalScore" in filtered.columns else 0
    top_s = filtered["PortalScore"].max()  if "PortalScore" in filtered.columns else 0
    elite = len(filtered[filtered.get("PortalScore", pd.Series([0])) >= 85]) if "PortalScore" in filtered.columns else 0
    high  = len(filtered[(filtered.get("PortalScore", pd.Series([0])) >= 70) &
                          (filtered.get("PortalScore", pd.Series([0])) < 85)]) if "PortalScore" in filtered.columns else 0

    col1.metric("Total Players", f"{total:,}")
    col2.metric("Shown",         f"{shown:,}")
    col3.metric("Avg S1 Score",  f"{avg_s:.1f}")
    col4.metric("Top S1 Score",  f"{top_s:.1f}")
    col5.metric("🔵 Elite (85+)", elite)
    col6.metric("🟢 High (70–84)", high)

    # ── Tabs ──
    tab_lb, tab_team, tab_conf, tab_tiers, tab_heatmap = st.tabs([
        "🏆 Leaderboard",
        "🏫 By Team",
        "🗺 By Conference",
        "📊 Tier View",
        "▦ Heatmap",
    ])

    # ════════════════════════════════════════
    #  LEADERBOARD TAB
    # ════════════════════════════════════════
    with tab_lb:
        # Build display dataframe
        id_cols   = ["Player", "Team", "Conference", "Position", "Elig", "G"]
        stat_cols = ["PTS", "TS%", "eFGPct", "USG%"]
        s1_cols   = ["PER", "PORPAG", "WS40", "DEF_comp",
                     "PortalScore"] if show_system1 else []
        s2_cols   = ["KenPomRank", "KenPomMult", "BaseScore",
                     "PRACombo", "FinalScore", "NILValue"] if show_system2 else []

        all_cols = id_cols + stat_cols + s1_cols + s2_cols
        avail    = [c for c in all_cols if c in filtered.columns]
        display  = filtered[avail].copy().head(500)

        # Format NIL
        if "NILValue" in display.columns:
            display["NILValue"] = display["NILValue"].apply(fmt_nil)

        # Rename for clarity
        rename = {
            "eFGPct":     "eFG%",
            "DEF_comp":   "DEF",
            "WS40":       "WS/40",
            "PortalScore":"S1 Score",
            "KenPomRank": "KP Rank",
            "KenPomMult": "KP ×",
            "BaseScore":  "Base",
            "PRACombo":   "PRA",
            "FinalScore": "S2 Final",
            "NILValue":   "NIL Est.",
        }
        display = display.rename(columns=rename)
        display.insert(0, "#", range(1, len(display) + 1))

        # Apply heatmap styling
        shade_high = ["PTS", "TS%", "eFG%", "USG%", "PER", "PORPAG",
                      "WS/40", "S1 Score", "S2 Final", "Base", "PRA"]
        shade_low  = ["KP Rank"]

        if show_heatmap:
            st.dataframe(
                style_dataframe(display, shade_high, shade_low),
                use_container_width=True,
                height=520,
                hide_index=True,
            )
        else:
            st.dataframe(display, use_container_width=True,
                         height=520, hide_index=True)

        # Download button
        csv = filtered.to_csv(index=False)
        st.download_button(
            label="💾 Download Full Results CSV",
            data=csv,
            file_name=f"portal_scores_{date.today()}.csv",
            mime="text/csv",
        )

    # ════════════════════════════════════════
    #  BY TEAM TAB
    # ════════════════════════════════════════
    with tab_team:
        if len(filtered) == 0:
            st.info("No players match current filters.")
        else:
            grp = (filtered.groupby("Team")
                           .apply(lambda g: pd.Series({
                               "Conference": g["Conference"].iloc[0]
                                             if "Conference" in g.columns else "",
                               "Players":    len(g),
                               "Avg S1":     g["PortalScore"].mean().round(2)
                                             if "PortalScore" in g.columns else 0,
                               "Top S1":     g["PortalScore"].max().round(2)
                                             if "PortalScore" in g.columns else 0,
                               "Avg S2":     g["FinalScore"].mean().round(2)
                                             if "FinalScore" in g.columns else 0,
                               "Best Player":g.loc[g["PortalScore"].idxmax(),
                                               "Player"]
                                             if "PortalScore" in g.columns else "",
                           }))
                           .reset_index()
                           .sort_values("Avg S1", ascending=False))
            st.dataframe(
                style_dataframe(grp, ["Avg S1", "Top S1", "Avg S2"],  []),
                use_container_width=True, height=500, hide_index=True)

    # ════════════════════════════════════════
    #  BY CONFERENCE TAB
    # ════════════════════════════════════════
    with tab_conf:
        if "Conference" not in filtered.columns or len(filtered) == 0:
            st.info("Conference data not available.")
        else:
            rows = []
            for conf, grp in filtered.groupby("Conference"):
                top = grp.loc[grp["PortalScore"].idxmax()] \
                      if "PortalScore" in grp.columns else grp.iloc[0]
                rows.append({
                    "Conference": conf,
                    "Players":    len(grp),
                    "Avg S1":     grp["PortalScore"].mean().round(2)
                                  if "PortalScore" in grp.columns else 0,
                    "Elite":      len(grp[grp.get("PortalScore",
                                    pd.Series([0])) >= 85]),
                    "High":       len(grp[(grp.get("PortalScore",
                                    pd.Series([0])) >= 70) &
                                         (grp.get("PortalScore",
                                    pd.Series([0])) < 85)]),
                    "Top Player": f"{top['Player']} ({top.get('PortalScore',0):.1f})",
                })
            conf_df = pd.DataFrame(rows).sort_values("Avg S1", ascending=False)
            st.dataframe(
                style_dataframe(conf_df, ["Avg S1", "Elite", "High"], []),
                use_container_width=True, height=500, hide_index=True)

    # ════════════════════════════════════════
    #  TIER VIEW TAB
    # ════════════════════════════════════════
    with tab_tiers:
        tiers = [
            ("🔵 ELITE  (85–100)", 85, 101, "#58a6ff"),
            ("🟢 HIGH   (70–84)",  70,  85, "#3fb950"),
            ("🟡 SOLID  (55–69)",  55,  70, "#d29922"),
            ("🟠 FRINGE (40–54)",  40,  55, "#e3b341"),
            ("⚫ DEPTH  (<40)",      0,  40, "#8b949e"),
        ]
        for label, lo, hi, colour in tiers:
            tier_df = filtered[
                (filtered.get("PortalScore", pd.Series([0])) >= lo) &
                (filtered.get("PortalScore", pd.Series([0])) < hi)
            ] if "PortalScore" in filtered.columns else pd.DataFrame()

            with st.expander(f"{label}  —  {len(tier_df):,} players",
                             expanded=(lo >= 70)):
                if len(tier_df) > 0:
                    top10 = tier_df.sort_values(
                        "PortalScore", ascending=False).head(10)
                    cols_show = ["Player", "Team", "Conference",
                                 "Position", "PortalScore"]
                    if "FinalScore" in top10.columns:
                        cols_show.append("FinalScore")
                    if "NILValue" in top10.columns:
                        top10 = top10.copy()
                        top10["NIL Est."] = top10["NILValue"].apply(fmt_nil)
                        cols_show.append("NIL Est.")
                    avail = [c for c in cols_show if c in top10.columns]
                    st.dataframe(top10[avail].reset_index(drop=True),
                                 use_container_width=True, hide_index=True)

    # ════════════════════════════════════════
    #  HEATMAP TAB  (Plotly)
    # ════════════════════════════════════════
    with tab_heatmap:
        try:
            import plotly.graph_objects as go
            import plotly.express as px

            top50 = filtered.head(50).copy()
            if len(top50) == 0:
                st.info("No data to display.")
            else:
                hm_cols_cfg = [
                    ("TS%",      "TS%",        False),
                    ("eFG%",     "eFGPct",     False),
                    ("PER",      "PER",        False),
                    ("PORPAG",   "PORPAG",     False),
                    ("USG%",     "USG%",       False),
                    ("KP Rank",  "KenPomRank", True),   # inverted
                    ("Base",     "BaseScore",  False),
                    ("PRA",      "PRACombo",   False),
                    ("S2 Final", "FinalScore", False),
                    ("S1 Score", "PortalScore",False),
                ]
                avail_hm = [(d, c, inv) for d, c, inv in hm_cols_cfg
                            if c in top50.columns]

                players = [f"{i+1}. {r['Player']}" for i, r
                           in top50.iterrows()]

                # Build normalised z-matrix
                z_vals    = []
                text_vals = []
                col_labels = []

                for disp, df_col, invert in avail_hm:
                    col_labels.append(disp)
                    series = pd.to_numeric(top50[df_col], errors="coerce")
                    lo = series.quantile(0.05)
                    hi = series.quantile(0.95)
                    rng = hi - lo if hi != lo else 1
                    norm = ((series - lo) / rng).clip(0, 1)
                    if invert: norm = 1 - norm

                    z_vals.append(norm.tolist())

                    # Format values for hover
                    col_text = []
                    for v in series:
                        if pd.isna(v):
                            col_text.append("—")
                        elif df_col == "NILValue":
                            col_text.append(fmt_nil(v))
                        elif df_col == "KenPomRank":
                            col_text.append(f"#{int(v)}")
                        else:
                            col_text.append(f"{float(v):.1f}")
                    text_vals.append(col_text)

                fig = go.Figure(go.Heatmap(
                    z=z_vals,
                    x=players,
                    y=col_labels,
                    text=text_vals,
                    texttemplate="%{text}",
                    textfont={"size": 9, "color": "black"},
                    colorscale=[
                        [0.0,  "#da3633"],
                        [0.5,  "#d29922"],
                        [1.0,  "#3fb950"],
                    ],
                    showscale=True,
                    colorbar=dict(
                        title="Low → High",
                        tickvals=[0, 0.5, 1],
                        ticktext=["Low", "Mid", "High"],
                        len=0.5,
                    ),
                    hoverongaps=False,
                ))
                fig.update_layout(
                    title=f"Density Heatmap — Top {len(top50)} Players",
                    paper_bgcolor="#0d1117",
                    plot_bgcolor="#0d1117",
                    font=dict(color="#e6edf3", size=10),
                    height=max(400, len(avail_hm) * 45 + 100),
                    xaxis=dict(side="top", tickangle=-35),
                    margin=dict(l=80, r=40, t=80, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                st.caption("Each cell is normalised relative to the column's "
                           "5th–95th percentile range. KP Rank is inverted "
                           "(lower rank = greener).")

        except ImportError:
            st.warning("Install plotly for the heatmap: `pip install plotly`")

    # ── Footer ──
    st.divider()
    st.caption(
        f"Data: CBBD API (collegebasketballdata.com)  •  "
        f"KenPom rankings  •  "
        f"Last updated: {datetime.now().strftime('%B %d, %Y %H:%M')}  •  "
        f"Season: 2025–26"
    )


if __name__ == "__main__":
    main()
