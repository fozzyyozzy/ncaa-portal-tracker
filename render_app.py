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
    # Environment variable (Render, local dev)
    env_key = os.environ.get("CBBD_API_KEY", "")
    if env_key:
        return env_key
    # Streamlit Cloud secrets (only if secrets file exists)
    try:
        if hasattr(st, "secrets") and "CBBD_API_KEY" in st.secrets:
            return st.secrets["CBBD_API_KEY"]
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────
#  DATA LOADING  (cached — only hits API once per day)
# ──────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner="Fetching player data from CBBD API...")
def load_player_data(api_key, season=2026, cache_version=3):
    """Load and score all players. Cached for 24 hours.
    Bump cache_version to force a fresh pull on next load."""
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
    """
    Apply per-column Red→Yellow→Green shading using inline CSS.
    Does NOT require matplotlib — works on all platforms.
    """
    if shade_cols_high is None: shade_cols_high = []
    if shade_cols_low  is None: shade_cols_low  = []

    df_num = df_display.copy()
    for col in shade_cols_high + shade_cols_low:
        if col in df_num.columns:
            df_num[col] = pd.to_numeric(df_num[col], errors="coerce")

    styler = df_num.style

    def _cell_colour(val, lo, hi, invert=False):
        """Return background-color CSS for a single value."""
        try:
            v = float(val)
            if np.isnan(v) or hi == lo:
                return ""
            t = (v - lo) / (hi - lo)
            t = max(0.0, min(1.0, t))
            if invert:
                t = 1.0 - t
            # Red(218,54,51) → Yellow(210,153,34) → Green(63,185,80)
            if t < 0.5:
                s = t * 2
                r = int(218 + (210 - 218) * s)
                g = int(54  + (153 - 54)  * s)
                b = int(51  + (34  - 51)  * s)
            else:
                s = (t - 0.5) * 2
                r = int(210 + (63  - 210) * s)
                g = int(153 + (185 - 153) * s)
                b = int(34  + (80  - 34)  * s)
            # Dark text on light colours, light text on dark
            brightness = 0.299*r + 0.587*g + 0.114*b
            fg = "#0d1117" if brightness > 100 else "#e6edf3"
            return f"background-color: #{r:02x}{g:02x}{b:02x}; color: {fg}"
        except Exception:
            return ""

    for col in shade_cols_high:
        if col not in df_num.columns:
            continue
        series = df_num[col].dropna()
        if len(series) < 2:
            continue
        lo = float(series.quantile(0.05))
        hi = float(series.quantile(0.95))
        if lo == hi:
            continue
        styler = styler.applymap(
            lambda v, lo=lo, hi=hi: _cell_colour(v, lo, hi, invert=False),
            subset=[col])

    for col in shade_cols_low:
        if col not in df_num.columns:
            continue
        series = df_num[col].dropna()
        if len(series) < 2:
            continue
        lo = float(series.quantile(0.05))
        hi = float(series.quantile(0.95))
        if lo == hi:
            continue
        styler = styler.applymap(
            lambda v, lo=lo, hi=hi: _cell_colour(v, lo, hi, invert=True),
            subset=[col])

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
    df, err = load_player_data(api_key, cache_version=3)
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
        ordered = ["FR", "SO", "JR", "SR", "GR", "UNK"]
        if "Elig" in df.columns:
            present  = set(df["Elig"].astype(str).unique())
            elig_opts = [e for e in ordered if e in present]
        else:
            elig_opts = ["FR", "SO", "JR", "SR"]
        # Default: FR/SO/JR/SR only — exclude GR and UNK
        default_elig = [e for e in elig_opts
                        if e not in ("GR", "UNK")]
        elig_sel = st.multiselect("Year", elig_opts,
                                   default=default_elig,
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

        # Team
        st.markdown('<p class="section-header">Team</p>',
                    unsafe_allow_html=True)
        team_opts = ["All"]
        if "Team" in df.columns:
            team_opts += sorted(df["Team"].dropna().unique())
        team_dd = st.selectbox("Team", team_opts,
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
        # Elig data status
        if "Elig" in df.columns:
            elig_known = (df["Elig"].astype(str) != "UNK").sum()
            if elig_known > 100:
                st.caption(f"✅ Elig data: {elig_known:,} players matched")
            else:
                st.caption("⚠️ Elig data not loaded — Book2_enriched.csv missing from repo")
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

    # Eligibility — simple and explicit
    if "Elig" in filtered.columns and elig_sel:
        filtered = filtered[
            filtered["Elig"].astype(str).isin([str(e) for e in elig_sel])]

    # Position
    if "Position" in filtered.columns and pos_sel:
        filtered = filtered[
            filtered["Position"].isin(pos_sel) |
            filtered["Position"].isna() |
            (filtered["Position"] == "")]

    # Conference
    if conf_sel != "All" and "Conference" in filtered.columns:
        filtered = filtered[filtered["Conference"] == conf_sel]

    # Team dropdown
    if team_dd != "All" and "Team" in filtered.columns:
        filtered = filtered[filtered["Team"] == team_dd]

    # Min portal score
    if "PortalScore" in filtered.columns:
        filtered = filtered[
            pd.to_numeric(filtered["PortalScore"], errors="coerce"
                          ).fillna(0) >= min_score]

    # Player search
    if player_search:
        filtered = filtered[
            filtered["Player"].str.lower().str.contains(
                player_search.lower(), na=False)]

    # Team search
    if team_search:
        filtered = filtered[
            filtered["Team"].str.lower().str.contains(
                team_search.lower(), na=False)]

    # Sort — always last
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
        filtered = filtered.sort_values(
            sort_col, ascending=False, na_position="last")
    filtered = filtered.reset_index(drop=True)

    # ── Summary metrics bar ──
    ps  = pd.to_numeric(filtered["PortalScore"].values
                        if "PortalScore" in filtered.columns
                        else [], errors="coerce")
    fs  = pd.to_numeric(filtered["FinalScore"].values
                        if "FinalScore" in filtered.columns
                        else [], errors="coerce")
    nv  = pd.to_numeric(filtered["NILValue"].values
                        if "NILValue" in filtered.columns
                        else [], errors="coerce")
    ps  = pd.Series(ps).dropna()
    fs  = pd.Series(fs).dropna()
    nv  = pd.Series(nv).dropna()

    c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns(9)
    c1.metric("Total",          f"{len(df):,}")
    c2.metric("Shown",          f"{len(filtered):,}")
    c3.metric("S1 Avg",         f"{ps.mean():.1f}"  if len(ps) else "—")
    c4.metric("S1 Top",         f"{ps.max():.1f}"   if len(ps) else "—")
    c5.metric("🔵 Elite (85+)", int((ps >= 85).sum()) if len(ps) else 0)
    c6.metric("🟢 High (70+)",  int((ps >= 70).sum()) if len(ps) else 0)
    c7.metric("S2 Avg Final",   f"{fs.mean():.1f}"  if len(fs) else "—")
    c8.metric("S2 Top Final",   f"{fs.max():.1f}"   if len(fs) else "—")
    c9.metric("Avg NIL Est.",   fmt_nil(nv.mean())  if len(nv) else "—")

    # ── Tabs ──
    tab_lb, tab_team, tab_conf, tab_portal, tab_compare = st.tabs([
        "👥 All Players",
        "🏫 By Team",
        "🗺 By Conference",
        "🚪 Players in Portal",
        "⚖️ Compare Players",
    ])

    # ════════════════════════════════════════
    #  LEADERBOARD TAB
    # ════════════════════════════════════════
    with tab_lb:
        # ── Column selection — order matters, use want list directly ──
        id_cols   = ["Player", "Team", "Conference", "Position", "Elig", "G"]
        stat_cols = ["PTS", "Tot", "AST", "TS%", "eFGPct", "USG%"]
        s1_cols   = (["PER", "PORPAG", "WS40_cbbd", "DEF_comp", "PortalScore"]
                     if show_system1 else [])
        s2_cols   = (["KenPomRank", "KenPomMult", "BaseScore",
                      "PRACombo", "FinalScore", "NILValue"]
                     if show_system2 else [])

        want  = id_cols + stat_cols + s1_cols + s2_cols
        # Preserve the ORDER from want, only include cols that exist
        avail = [c for c in want if c in filtered.columns]
        display = filtered[avail].head(500).copy()

        # ── Ensure Elig is present and in the right spot ──
        if "Elig" not in display.columns:
            # Insert after Position if possible
            if "Position" in display.columns:
                pos_idx = display.columns.get_loc("Position") + 1
            elif "G" in display.columns:
                pos_idx = display.columns.get_loc("G")
            else:
                pos_idx = 4
            display.insert(pos_idx, "Elig",
                           filtered["Elig"] if "Elig" in filtered.columns
                           else "UNK")

        # ── Format ──
        fmt1 = ["PTS", "Tot", "AST", "TS%", "eFGPct", "USG%",
                "PER", "PORPAG", "WS40_cbbd", "DEF_comp",
                "BaseScore", "PRACombo"]
        fmt2 = ["PortalScore", "FinalScore"]

        for c in fmt1:
            if c in display.columns:
                display[c] = pd.to_numeric(
                    display[c], errors="coerce").round(1)
        for c in fmt2:
            if c in display.columns:
                display[c] = pd.to_numeric(
                    display[c], errors="coerce").round(2)
        if "KenPomRank" in display.columns:
            display["KenPomRank"] = (
                pd.to_numeric(display["KenPomRank"], errors="coerce")
                .apply(lambda x: int(x) if pd.notna(x) else pd.NA))
        if "KenPomMult" in display.columns:
            display["KenPomMult"] = pd.to_numeric(
                display["KenPomMult"], errors="coerce").round(3)
        if "NILValue" in display.columns:
            display["NILValue"] = display["NILValue"].apply(fmt_nil)

        # ── Rename AFTER formatting ──
        rename = {
            "Tot":        "REB",
            "AST":        "AST",
            "eFGPct":     "eFG%",
            "DEF_comp":   "DEF",
            "WS40_cbbd":  "WS/40",
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

        # ── Column tooltips ──
        col_help = {
            "#":         "Rank by selected sort column",
            "Player":    "Player name",
            "Team":      "Current team",
            "Conference":"Conference",
            "Position":  "Position (G/F/C)",
            "Elig":      "Eligibility year (FR/SO/JR/SR)",
            "G":         "Games played",
            "PTS":       "Points per game",
            "REB":       "Total rebounds per game",
            "AST":       "Assists per game",
            "TS%":       "True Shooting % — measures shooting efficiency accounting for FG, 3P, and FT",
            "eFG%":      "Effective Field Goal % — adjusts FG% to account for 3-pointers being worth more",
            "USG%":      "Usage Rate — % of team plays used by player while on court",
            "PER":       "Player Efficiency Rating — per-minute production normalized to league avg = 15.0",
            "PORPAG":    "Points Over Replacement Per Adjusted Game — CBBD native advanced metric",
            "WS/40":     "Win Shares per 40 minutes — estimated wins contributed per 40 min played",
            "DEF":       "Defensive Composite — STL+BLK per 40 min + player defensive rating",
            "S1 Score":  "System 1: Portal Score (0–100) — normalized composite. Elite = 85+",
            "KP Rank":   "KenPom team ranking — lower is better",
            "KP ×":      "KenPom multiplier applied to System 2 score (1–50: ×1.0, 300+: ×0.70)",
            "Base":      "S2 Base Score = (PER + WS_total) + ((eFG% + TS%) / 4)",
            "PRA":       "S2 PRA Combo = (PPG×0.5) + (RPG×0.33) + (APG×0.5)",
            "S2 Final":  "System 2: Custom Formula = (Base + PRA) × KenPom Multiplier",
            "NIL Est.":  "Estimated NIL value based on S2 Final Score. Tiered market model. Not financial advice.",
        }

        # Display tooltips as caption row
        st.markdown(
            " &nbsp;|&nbsp; ".join(
                f"**{c}**: {col_help[c]}"
                for c in display.columns if c in col_help
            ),
            unsafe_allow_html=True
        ) if False else None   # disabled inline — shown in expander below

        with st.expander("ℹ️ Column Definitions", expanded=False):
            col_a, col_b = st.columns(2)
            items = [(k, v) for k, v in col_help.items()
                     if k in display.columns and k != "#"]
            half  = len(items) // 2
            with col_a:
                for k, v in items[:half]:
                    st.markdown(f"**{k}** — {v}")
            with col_b:
                for k, v in items[half:]:
                    st.markdown(f"**{k}** — {v}")

        # ── Shading ──
        shade_high = [c for c in ["PTS", "REB", "AST", "TS%", "eFG%",
                                   "USG%", "PER", "PORPAG", "WS/40",
                                   "S1 Score", "S2 Final", "Base", "PRA"]
                      if c in display.columns]
        shade_low  = [c for c in ["KP Rank"] if c in display.columns]

        # ── Format map ──
        fmt_map = {}
        for c in display.columns:
            if c in ("#", "Player", "Team", "Conference",
                     "Position", "Elig", "NIL Est."):
                continue
            elif c == "G":
                fmt_map[c] = "{:.0f}"
            elif c in ("S1 Score", "S2 Final"):
                fmt_map[c] = "{:.2f}"
            elif c == "KP ×":
                fmt_map[c] = "{:.3f}"
            elif c == "KP Rank":
                fmt_map[c] = "{:.0f}"
            else:
                fmt_map[c] = "{:.1f}"

        if show_heatmap:
            styled = style_dataframe(display, shade_high, shade_low)
            styled = styled.format(fmt_map, na_rep="—")
            st.dataframe(styled, use_container_width=True,
                         height=520, hide_index=True)
        else:
            st.dataframe(
                display.style.format(fmt_map, na_rep="—"),
                use_container_width=True, height=520, hide_index=True)

        st.download_button(
            label="💾 Download Full Results CSV",
            data=filtered.to_csv(index=False),
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
            def safe_round(val, n=2):
                try:
                    return round(float(val), n)
                except Exception:
                    return 0.0

            rows_team = []
            for team, g in filtered.groupby("Team"):
                ps = pd.to_numeric(g.get("PortalScore", pd.Series([0])),
                                   errors="coerce").dropna()
                fs = pd.to_numeric(g.get("FinalScore",  pd.Series([0])),
                                   errors="coerce").dropna()
                best_idx = ps.idxmax() if len(ps) > 0 else None
                rows_team.append({
                    "Team":        team,
                    "Conference":  g["Conference"].iloc[0]
                                   if "Conference" in g.columns else "",
                    "Players":     len(g),
                    "Avg S1":      safe_round(ps.mean()) if len(ps) else 0,
                    "Top S1":      safe_round(ps.max())  if len(ps) else 0,
                    "Avg S2":      safe_round(fs.mean()) if len(fs) else 0,
                    "Best Player": g.loc[best_idx, "Player"]
                                   if best_idx is not None else "",
                })
            grp = pd.DataFrame(rows_team).sort_values("Avg S1", ascending=False)
            st.dataframe(
                style_dataframe(grp, ["Avg S1", "Top S1", "Avg S2"], []),
                use_container_width=True, height=500, hide_index=True)

    # ════════════════════════════════════════
    #  BY CONFERENCE TAB
    # ════════════════════════════════════════
    with tab_conf:
        if "Conference" not in filtered.columns or len(filtered) == 0:
            st.info("Conference data not available.")
        else:
            rows_conf = []
            for conf, g in filtered.groupby("Conference"):
                ps = pd.to_numeric(g.get("PortalScore", pd.Series([0])),
                                   errors="coerce").dropna()
                best_idx = ps.idxmax() if len(ps) > 0 else None
                top_player = (f"{g.loc[best_idx, 'Player']} "
                              f"({ps.max():.1f})"
                              if best_idx is not None else "")
                rows_conf.append({
                    "Conference": conf,
                    "Players":    len(g),
                    "Avg S1":     round(float(ps.mean()), 2) if len(ps) else 0,
                    "Elite":      int((ps >= 85).sum()),
                    "High":       int(((ps >= 70) & (ps < 85)).sum()),
                    "Top Player": top_player,
                })
            conf_df = pd.DataFrame(rows_conf).sort_values(
                "Avg S1", ascending=False)
            st.dataframe(
                style_dataframe(conf_df, ["Avg S1", "Elite", "High"], []),
                use_container_width=True, height=500, hide_index=True)

    # ════════════════════════════════════════
    #  PLAYERS IN PORTAL TAB
    # ════════════════════════════════════════
    with tab_portal:
        st.markdown("### 🚪 Players in Portal")
        st.caption("Source: On3 Transfer Portal  •  Upload a fresh CSV to update")

        # ── Load portal_entries.csv ──
        portal_path = "portal_entries.csv"
        portal_df   = None

        if os.path.exists(portal_path):
            try:
                portal_df = pd.read_csv(portal_path, encoding="utf-8-sig")
            except Exception as e:
                st.error(f"Could not load portal_entries.csv: {e}")

        # ── CSV upload to refresh ──
        with st.expander("📤 Upload fresh portal data (On3 CSV)", expanded=(portal_df is None)):
            st.markdown("""
            **How to get fresh data:**
            1. Go to [on3.com/transfer-portal](https://on3.com/transfer-portal/)
            2. Scroll all the way down to load all players
            3. Select all → Copy → Paste into Excel → Save as `Portalers.csv`
            4. Run `parse_on3_portal.py` locally to generate `portal_entries.csv`
            5. Upload the result below
            """)
            uploaded = st.file_uploader("Upload portal_entries.csv",
                                         type="csv", key="portal_upload")
            if uploaded:
                try:
                    portal_df = pd.read_csv(uploaded, encoding="utf-8-sig")
                    st.success(f"✅ Loaded {len(portal_df)} portal entries")
                except Exception as e:
                    st.error(f"Upload error: {e}")

        if portal_df is None or len(portal_df) == 0:
            st.info("No portal data loaded. Upload a portal_entries.csv file above.")
        else:
            # ── Merge with CBBD scores ──
            def norm_name(n):
                import re
                n = str(n).lower().strip()
                n = re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b', '', n)
                n = re.sub(r'[^a-z ]', '', n)
                return re.sub(r'\s+', ' ', n).strip()

            score_lookup = {}
            if df is not None:
                for _, row in df.iterrows():
                    score_lookup[norm_name(row['Player'])] = row

            score_cols = ['PTS','Tot','AST','TS%','eFGPct','DEF_comp',
                          'PER','PortalScore','FinalScore','NILValue',
                          'Conference','Elig']

            merged_rows = []
            for _, p in portal_df.iterrows():
                row_data = p.to_dict()
                key = norm_name(p.get('Player',''))
                if key in score_lookup:
                    sr = score_lookup[key]
                    for c in score_cols:
                        if c in sr.index:
                            row_data[f'__{c}'] = sr[c]
                merged_rows.append(row_data)

            merged = pd.DataFrame(merged_rows)

            # ── Portal filters ──
            pcol1, pcol2, pcol3, pcol4 = st.columns(4)
            with pcol1:
                status_opts = ["All"] + sorted(
                    portal_df['Status'].dropna().unique().tolist())
                p_status = st.selectbox("Status", status_opts,
                                         key="p_status")
            with pcol2:
                pos_opts_p = ["All"] + sorted(
                    portal_df['Pos'].dropna().unique().tolist())
                p_pos = st.selectbox("Position", pos_opts_p, key="p_pos")
            with pcol3:
                p_search = st.text_input("Search player",
                                          placeholder="Name...",
                                          key="p_search")
            with pcol4:
                p_sort = st.selectbox("Sort by",
                    ["S2 Final", "S1 Score", "On3 Rating",
                     "NIL Est.", "PTS", "Player"],
                    key="p_sort")

            # Apply filters
            pf = merged.copy()
            if p_status != "All":
                pf = pf[pf['Status'] == p_status]
            if p_pos != "All":
                pf = pf[pf['Pos'] == p_pos]
            if p_search:
                pf = pf[pf['Player'].str.lower().str.contains(
                    p_search.lower(), na=False)]

            # Sort
            sort_col_map = {
                "S2 Final":   "__FinalScore",
                "S1 Score":   "__PortalScore",
                "On3 Rating": "On3Rating",
                "NIL Est.":   "__NILValue",
                "PTS":        "__PTS",
                "Player":     "Player",
            }
            sc = sort_col_map.get(p_sort, "__FinalScore")
            if sc in pf.columns:
                pf = pf.sort_values(sc, ascending=(p_sort=="Player"),
                                    na_position='last')

            # ── Build display ──
            disp_cols = {
                'Player':          'Player',
                'Pos':             'Pos',
                'Elig':            'Elig',
                'Height':          'Height',
                'On3Rating':       'On3 Rating',
                'Status':          'Status',
                'LastTeam':        'Last Team',
                'NewTeam':         'New Team',
                '__PTS':           'PTS',
                '__Tot':           'REB',
                '__AST':           'AST',
                '__TS%':           'TS%',
                '__DEF_comp':      'DEF',
                '__PER':           'PER',
                '__PortalScore':   'S1 Score',
                '__FinalScore':    'S2 Final',
                '__NILValue':      'NIL Est.',
            }
            avail_p = {k:v for k,v in disp_cols.items() if k in pf.columns}
            p_display = pf[list(avail_p.keys())].copy()
            p_display = p_display.rename(columns=avail_p)
            p_display.insert(0, '#', range(1, len(p_display)+1))

            # ── Format decimals ──
            fmt_map_p = {}
            for c in p_display.columns:
                if c in ('#','Player','Pos','Elig','Height','Status',
                          'Last Team','New Team','NIL Est.'):
                    continue
                elif c == 'On3 Rating':
                    fmt_map_p[c] = "{:.2f}"
                elif c in ('S1 Score','S2 Final'):
                    fmt_map_p[c] = "{:.2f}"
                else:
                    fmt_map_p[c] = "{:.1f}"

            for c in ['PTS','REB','AST','TS%','DEF','PER']:
                if c in p_display.columns:
                    p_display[c] = pd.to_numeric(
                        p_display[c], errors='coerce').round(1)
            for c in ['S1 Score','S2 Final','On3 Rating']:
                if c in p_display.columns:
                    p_display[c] = pd.to_numeric(
                        p_display[c], errors='coerce').round(2)
            if 'NIL Est.' in p_display.columns:
                p_display['NIL Est.'] = pd.to_numeric(
                    p_display['NIL Est.'], errors='coerce').apply(
                    lambda x: fmt_nil(x) if pd.notna(x) else '—')

            # Summary
            pm1, pm2, pm3, pm4 = st.columns(4)
            pm1.metric("Total in Portal", len(portal_df))
            pm2.metric("Shown",           len(p_display))
            pm3.metric("Committed",
                       len(portal_df[portal_df['Status']=='Committed']))
            pm4.metric("Avg On3 Rating",
                       f"{pd.to_numeric(portal_df['On3Rating'], errors='coerce').mean():.1f}")

            shade_p = [c for c in ['On3 Rating','PTS','REB','AST',
                                    'TS%','PER','S1 Score','S2 Final']
                       if c in p_display.columns]
            styled_p = style_dataframe(p_display, shade_p, [])
            styled_p = styled_p.format(fmt_map_p, na_rep="—")
            st.dataframe(styled_p, use_container_width=True,
                         height=540, hide_index=True)

            st.download_button(
                "💾 Download Portal List",
                data=pf.to_csv(index=False),
                file_name=f"portal_entries_{date.today()}.csv",
                mime="text/csv")

    # ════════════════════════════════════════
    #  COMPARE PLAYERS TAB
    # ════════════════════════════════════════
    with tab_compare:
        st.markdown("### ⚖️ Player Comparison Tool")
        st.caption("Build a custom comparison — search and add any D-I players")

        if df is None or len(df) == 0:
            st.info("Load data first.")
        else:
            # ── Player search and roster builder ──
            all_players = sorted(df['Player'].dropna().unique().tolist())

            col_search, col_team = st.columns([2, 1])
            with col_search:
                compare_search = st.text_input(
                    "Search for a player to add",
                    placeholder="Type name...",
                    key="compare_search")
            with col_team:
                base_team = st.selectbox(
                    "Or load a full team roster",
                    ["— select —"] + sorted(df['Team'].dropna().unique().tolist()),
                    key="base_team")

            # Filter search results
            if compare_search:
                matches = [p for p in all_players
                           if compare_search.lower() in p.lower()][:20]
                if matches:
                    add_player = st.selectbox(
                        f"Found {len(matches)} matches — select to add:",
                        ["— select —"] + matches,
                        key="add_player_sel")
                else:
                    st.caption("No players found.")
                    add_player = "— select —"
            else:
                add_player = "— select —"

            # Session state for comparison roster
            if 'compare_roster' not in st.session_state:
                st.session_state.compare_roster = []

            # Add from search
            if add_player != "— select —":
                if add_player not in st.session_state.compare_roster:
                    st.session_state.compare_roster.append(add_player)

            # Load full team roster
            if base_team != "— select —":
                team_players = df[df['Team'] == base_team]['Player'].tolist()
                for p in team_players:
                    if p not in st.session_state.compare_roster:
                        st.session_state.compare_roster.append(p)

            # ── Roster management ──
            if st.session_state.compare_roster:
                st.markdown(f"**Comparison roster — {len(st.session_state.compare_roster)} players:**")

                # Remove individual players
                remove_cols = st.columns(min(6, len(st.session_state.compare_roster)))
                to_remove = []
                for idx, pname in enumerate(st.session_state.compare_roster):
                    col = remove_cols[idx % len(remove_cols)]
                    if col.button(f"✕ {pname[:18]}", key=f"rm_{idx}_{pname}"):
                        to_remove.append(pname)
                for p in to_remove:
                    st.session_state.compare_roster.remove(p)

                c_clear, c_portal = st.columns([1, 2])
                with c_clear:
                    if st.button("🗑 Clear all", key="clear_roster"):
                        st.session_state.compare_roster = []
                        st.rerun()
                with c_portal:
                    # Add all portal players to comparison
                    if os.path.exists("portal_entries.csv"):
                        if st.button("➕ Add all portal players",
                                     key="add_portal"):
                            try:
                                pdf = pd.read_csv("portal_entries.csv")
                                for p in pdf['Player'].tolist():
                                    if p not in st.session_state.compare_roster:
                                        st.session_state.compare_roster.append(p)
                            except Exception:
                                pass

            # ── Comparison table ──
            if not st.session_state.compare_roster:
                st.info("Search for players above or load a team roster to start comparing.")
            else:
                comp_df = df[df['Player'].isin(
                    st.session_state.compare_roster)].copy()

                if len(comp_df) == 0:
                    st.warning("None of the selected players were found in the scoring data.")
                else:
                    # Sort options
                    comp_sort = st.selectbox("Sort comparison by",
                        ["S2 Final", "S1 Score", "NIL Est.",
                         "PER", "PORPAG", "PTS", "REB", "AST"],
                        key="comp_sort")

                    comp_sort_map = {
                        "S1 Score":  "PortalScore",
                        "S2 Final":  "FinalScore",
                        "NIL Est.":  "NILValue",
                        "PER":       "PER",
                        "PORPAG":    "PORPAG",
                        "PTS":       "PTS",
                        "REB":       "Tot",
                        "AST":       "AST",
                    }
                    cs = comp_sort_map.get(comp_sort, "PortalScore")
                    if cs in comp_df.columns:
                        comp_df = comp_df.sort_values(
                            cs, ascending=False, na_position='last')

                    # Build display
                    comp_show_cols = [
                        ('Player',      'Player'),
                        ('Team',        'Team'),
                        ('Conference',  'Conf'),
                        ('Position',    'Pos'),
                        ('Elig',        'Elig'),
                        ('G',           'G'),
                        ('PTS',         'PTS'),
                        ('Tot',         'REB'),
                        ('AST',         'AST'),
                        ('TS%',         'TS%'),
                        ('eFGPct',      'eFG%'),
                        ('USG%',        'USG%'),
                        ('PER',         'PER'),
                        ('PORPAG',      'PORPAG'),
                        ('WS40_cbbd',   'WS/40'),
                        ('DEF_comp',    'DEF'),
                        ('PortalScore', 'S1 Score'),
                        ('FinalScore',  'S2 Final'),
                        ('NILValue',    'NIL Est.'),
                    ]
                    avail_c = [(dc, dn) for dc, dn in comp_show_cols
                               if dc in comp_df.columns]
                    c_disp = comp_df[[dc for dc,_ in avail_c]].copy()
                    c_disp = c_disp.rename(
                        columns={dc:dn for dc,dn in avail_c})

                    # Format
                    fmt_map_c = {}
                    for c in c_disp.columns:
                        if c in ('#','Player','Team','Conf','Pos',
                                  'Elig','NIL Est.'):
                            continue
                        elif c == 'G':
                            fmt_map_c[c] = "{:.0f}"
                        elif c in ('S1 Score','S2 Final'):
                            fmt_map_c[c] = "{:.2f}"
                        else:
                            fmt_map_c[c] = "{:.1f}"

                    for c in ['PTS','REB','AST','TS%','eFG%','USG%',
                              'PER','PORPAG','WS/40','DEF']:
                        if c in c_disp.columns:
                            c_disp[c] = pd.to_numeric(
                                c_disp[c], errors='coerce').round(1)
                    for c in ['S1 Score','S2 Final']:
                        if c in c_disp.columns:
                            c_disp[c] = pd.to_numeric(
                                c_disp[c], errors='coerce').round(2)
                    if 'NIL Est.' in c_disp.columns:
                        c_disp['NIL Est.'] = pd.to_numeric(
                            c_disp['NIL Est.'], errors='coerce').apply(
                            lambda x: fmt_nil(x) if pd.notna(x) else '—')
                    if 'G' in c_disp.columns:
                        c_disp['G'] = pd.to_numeric(
                            c_disp['G'], errors='coerce'
                            ).round(0).astype('Int64')

                    c_disp = c_disp.reset_index(drop=True)
                    c_disp.insert(0, '#', range(1, len(c_disp)+1))

                    shade_c = [c for c in ['PTS','REB','AST','TS%','eFG%',
                                            'USG%','PER','PORPAG','WS/40',
                                            'S1 Score','S2 Final']
                               if c in c_disp.columns]

                    # Stat summary row
                    st.markdown(f"**{len(c_disp)} players selected**")
                    styled_c = style_dataframe(c_disp, shade_c, [])
                    styled_c = styled_c.format(fmt_map_c, na_rep="—")
                    st.dataframe(styled_c,
                                 use_container_width=True,
                                 height=min(600, 45 + len(c_disp) * 35),
                                 hide_index=True)

                    # ── Visual radar/bar comparison ──
                    if len(c_disp) >= 2:
                        st.markdown("---")
                        st.markdown("**📊 Side-by-side stat bars**")
                        bar_stat = st.selectbox(
                            "Compare stat",
                            ['PTS','REB','AST','TS%','PER',
                             'PORPAG','S1 Score','S2 Final'],
                            key="bar_stat")

                        if bar_stat in c_disp.columns:
                            try:
                                import plotly.express as px
                                bar_data = c_disp[['Player', bar_stat]].copy()
                                bar_data[bar_stat] = pd.to_numeric(
                                    bar_data[bar_stat], errors='coerce')
                                bar_data = bar_data.dropna().sort_values(
                                    bar_stat, ascending=True)
                                fig = px.bar(
                                    bar_data,
                                    x=bar_stat, y='Player',
                                    orientation='h',
                                    color=bar_stat,
                                    color_continuous_scale=[
                                        [0.0, '#da3633'],
                                        [0.5, '#d29922'],
                                        [1.0, '#3fb950']],
                                    title=f"{bar_stat} Comparison",
                                    template='plotly_dark',
                                )
                                fig.update_layout(
                                    paper_bgcolor='#0d1117',
                                    plot_bgcolor='#161b22',
                                    showlegend=False,
                                    coloraxis_showscale=False,
                                    height=max(300,
                                               len(bar_data) * 40 + 80),
                                    margin=dict(l=160, r=20, t=40, b=20),
                                    yaxis=dict(tickfont=dict(size=11)),
                                )
                                st.plotly_chart(fig,
                                                use_container_width=True)
                            except ImportError:
                                st.caption(
                                    "Install plotly for bar charts: "
                                    "`pip install plotly`")

                    st.download_button(
                        "💾 Download Comparison CSV",
                        data=comp_df.to_csv(index=False),
                        file_name=f"comparison_{date.today()}.csv",
                        mime="text/csv")
    st.divider()
    with st.expander("📖 Methodology & Scoring Guide", expanded=False):
        st.markdown("""
### Two Scoring Systems

This tool computes **two independent scores** for every player:

---

#### 🔵 System 1 — Portal Score (0–100)
A normalized composite score where the D1 average sits around **50** and elite prospects score **85+**.

| Metric | Weight | Source |
|--------|--------|--------|
| PER (Player Efficiency Rating) | 25% | Computed from box score, normalized to avg = 15 |
| True Shooting % | 20% | CBBD native |
| Win Shares per 40 min | 15% | CBBD native |
| Usage Rate | 10% | CBBD native, soft-capped at 28% |
| Defensive Composite | 15% | STL+BLK per 40 + player DRtg |
| Conference Strength | 15% | Live adjusted efficiency (Barttorvik) |

Tiers: 🔵 Elite 85+ &nbsp;|&nbsp; 🟢 High 70–84 &nbsp;|&nbsp; 🟡 Solid 55–69 &nbsp;|&nbsp; 🟠 Fringe 40–54 &nbsp;|&nbsp; ⚫ Depth <40

---

#### 🟣 System 2 — Custom Score (raw scale)
A production-based formula emphasizing scoring efficiency and volume:

```
Base Score  = (PER + WS_total) + ((eFG% + TS%) / 4)
PRA Combo   = (PPG × 0.5) + (RPG × 0.33) + (APG × 0.5)
Combo Score = Base Score + PRA Combo
Final Score = Combo Score × KenPom Multiplier
```

**KenPom Multiplier** (based on team ranking):

| Rank | Multiplier |
|------|-----------|
| 1–50 | 1.00 |
| 51–100 | 0.95 |
| 101–150 | 0.90 |
| 151–200 | 0.85 |
| 201–250 | 0.80 |
| 251–300 | 0.75 |
| 300+ | 0.70 |

---

#### 💰 NIL Estimate
Market-based tiered estimate for 2025–26:
- **Final ≥ 50** (Elite): $1M + $150K per point above 50
- **Final ≥ 38** (High): $400K + $50K per point above 38
- **Final ≥ 28** (Mid): $150K + $25K per point above 28
- **Final < 28** (Role): $50K floor

> ⚠️ NIL estimates are analytical approximations based on publicly available
> market data and player performance metrics. They are not actual NIL valuations
> and should not be used as financial guidance.

---

#### Data Sources
- **Player stats**: [College Basketball Data (CBBD)](https://collegebasketballdata.com) — updated daily
- **Team efficiency**: Barttorvik adjusted ratings via CBBD
- **Team rankings**: KenPom (kenpom.com) — manually updated
- **Eligibility**: ESPN roster data via CBBD enrichment

*Data refreshes every 24 hours. Season: 2025–26.*
        """)

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
