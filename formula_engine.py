"""
=============================================================
  formula_engine.py  |  v2.0
  Custom scoring formula, KenPom integration, NIL estimator,
  density shading (Red → Yellow → Green)
=============================================================
  YOUR FORMULA:
    Base Score  = (PORPAG + WS_total) + ((eFG% + TS%) / 4)
    PRA Combo   = (PPG × 0.5) + (RPG × 0.33) + (APG × 0.5)
    Combo Score = Base Score + PRA Combo
    KenPom Mult = rank-based (1–50 → 1.0  ...  300+ → 0.7)
    Final Score = Combo Score × KenPom Multiplier

  NIL ESTIMATE (tiered market model):
    Final ≥ 50  (Elite):  $1M base + $150K per point above 50
    Final ≥ 38  (High):   $400K base + $50K per point above 38
    Final ≥ 28  (Mid):    $150K base + $25K per point above 28
    Final < 28  (Role):   $50K floor

  DENSITY SHADING:
    Red (#da3633) → Yellow (#d29922) → Green (#3fb950)
    Applied per-column so each stat shades relative to its
    own distribution — not a global scale.
=============================================================
"""

import pandas as pd
import numpy as np
import os
import re
from difflib import SequenceMatcher

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────

KENPOM_FILE         = "kenpom_rankings.csv"
NIL_FLOOR           = 50_000

KENPOM_MULT_TABLE = [
    (50,  1.00),
    (100, 0.95),
    (150, 0.90),
    (200, 0.85),
    (250, 0.80),
    (300, 0.75),
]
KENPOM_DEFAULT_MULT = 0.70

# Columns to shade in the GUI leaderboard (higher = greener)
SHADE_COLS_HIGH = [
    "TS%", "eFG%", "PORPAG", "WS", "FinalScore",
    "NILValue", "BaseScore", "PRACombo", "Score", "USG%",
]
# Columns where lower = greener (e.g. KP rank — lower rank is better)
SHADE_COLS_LOW = ["KP Rank"]

# ──────────────────────────────────────────────
#  NAME NORMALIZATION
# ──────────────────────────────────────────────

def _norm(name):
    name = str(name).lower().strip()
    name = name.replace("\xa0", " ")           # non-breaking space
    name = re.sub(r"\s+\d+$", "", name)        # strip trailing seed numbers
    name = re.sub(r"[^a-z0-9 .]", " ", name)  # keep dots for St./Mt.
    return re.sub(r"\s+", " ", name).strip()

def _fuzzy(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()

# Manual overrides: CBBD team name (lowercase) → KenPom team name (normalized)
# Add entries here whenever a team name mismatch is discovered
TEAM_NAME_OVERRIDES = {
    "uconn":               "connecticut",
    "connecticut":         "connecticut",
    "lsu":                 "lsu",
    "ucf":                 "ucf",
    "usc":                 "usc",
    "unlv":                "unlv",
    "utep":                "utep",
    "vcu":                 "vcu",
    "smu":                 "smu",
    "tcu":                 "tcu",
    "byu":                 "byu",
    "uab":                 "uab",
    "uncw":                "unc wilmington",
    "unc":                 "north carolina",
    "nc state":            "nc state",
    "fiu":                 "fiu",
    "fau":                 "fau",
    "fgcu":                "florida gulf coast",
    "umass":               "massachusetts",
    "umbc":                "umbc",
    "umkc":                "kansas city",
    "utsa":                "utsa",
    "utrgv":               "ut rio grande valley",
    "ut martin":           "ut martin",
    "uc santa barbara":    "uc santa barbara",
    "uc irvine":           "uc irvine",
    "uc san diego":        "uc san diego",
    "uc davis":            "uc davis",
    "uc riverside":        "uc riverside",
    "cal poly":            "cal poly",
    "cal baptist":         "california baptist",
    "st. john's":          "st. john's",
    "saint louis":         "saint louis",
    "saint mary's":        "saint mary's",
    "mount st. mary's":    "mount st. mary's",
    "loyola chicago":      "loyola chicago",
    "loyola maryland":     "loyola md",
    "prairie view a&m":    "prairie view",
    "texas a&m":           "texas a&m",
    "miami":               "miami fl",
    "miami fl":            "miami fl",
    "miami oh":            "miami oh",
    "florida int'l":       "fiu",
    "purdue fort wayne":   "purdue fort wayne",
    "iupui":               "iupui",
    "northern kentucky":   "northern kentucky",
    "south florida":       "south florida",
    "central florida":     "ucf",
}

def _best_team_match(cbbd_team, kenpom_teams, threshold=0.70):
    norm_cbbd = _norm(cbbd_team)

    # Check manual overrides first
    override = TEAM_NAME_OVERRIDES.get(norm_cbbd)
    if override:
        # Find the KenPom team that best matches the override
        for kt in kenpom_teams:
            if _norm(kt) == override:
                return kt
        # Fuzzy match on the override name
        best_score, best_match = 0, None
        for kt in kenpom_teams:
            score = _fuzzy(override, kt)
            if score > best_score:
                best_score = score
                best_match = kt
        if best_score >= 0.80:
            return best_match

    # Standard fuzzy match
    best_score, best_match = 0, None
    for kt in kenpom_teams:
        score = _fuzzy(norm_cbbd, kt)
        if score > best_score:
            best_score = score
            best_match = kt
    return best_match if best_score >= threshold else None

# ──────────────────────────────────────────────
#  KENPOM LOADING
# ──────────────────────────────────────────────

def load_kenpom(path=KENPOM_FILE):
    """
    Load KenPom CSV. Handles KenPom's actual export format which has:
    - Team names with trailing non-breaking space + seed number
    - Multiple rating columns with duplicate headers
    - Rank in 'Rk' column

    Returns (DataFrame, message_str)
    DataFrame columns: kp_team, kp_rank, kp_netrtg, kp_ortg, kp_drtg, kp_mult
    """
    if not os.path.exists(path):
        return None, f"KenPom file not found: {path}"

    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        return None, f"Could not read KenPom CSV: {e}"

    # ── Detect rank column ──
    rank_col = next(
        (c for c in df.columns if c.lower() in ("rk", "rank", "#", "no")),
        None)
    if rank_col is None:
        return None, "No rank column ('Rk' or 'Rank') found in KenPom CSV."

    # ── Detect team column ──
    team_col = next(
        (c for c in df.columns
         if c.lower() in ("team", "school", "name", "program")),
        None)
    if team_col is None:
        return None, "No 'Team' column found in KenPom CSV."

    # ── Clean team names (strip seed numbers + non-breaking spaces) ──
    out = pd.DataFrame()
    out["kp_team"] = df[team_col].apply(_norm)
    out["kp_rank"] = pd.to_numeric(df[rank_col], errors="coerce")
    out = out.dropna(subset=["kp_rank"])
    out["kp_rank"] = out["kp_rank"].astype(int)

    # ── Net rating ──
    net_col = next(
        (c for c in df.columns
         if "netrtg" in c.lower() or c.lower() == "netrtg"
         or c.lower() == "adj em" or "adjem" in c.lower()),
        None)
    if net_col:
        out["kp_netrtg"] = pd.to_numeric(df[net_col], errors="coerce")

    # ── Offensive / defensive rating ──
    ortg_col = next((c for c in df.columns if c.lower() in ("ortg",)), None)
    drtg_col = next((c for c in df.columns if c.lower() in ("drtg",)), None)
    if ortg_col:
        out["kp_ortg"] = pd.to_numeric(df[ortg_col], errors="coerce")
    if drtg_col:
        out["kp_drtg"] = pd.to_numeric(df[drtg_col], errors="coerce")

    # ── Assign multiplier ──
    def _mult(rank):
        if pd.isna(rank):
            return KENPOM_DEFAULT_MULT
        for cutoff, mult in KENPOM_MULT_TABLE:
            if rank <= cutoff:
                return mult
        return KENPOM_DEFAULT_MULT

    out["kp_mult"] = out["kp_rank"].apply(_mult)
    out = out.reset_index(drop=True)

    return out, f"Loaded {len(out)} teams from KenPom"


def merge_kenpom(df_players, df_kenpom):
    """
    Fuzzy-join KenPom data onto player DataFrame by team name.
    Adds: kp_rank, kp_mult, kp_netrtg
    """
    if df_kenpom is None or len(df_kenpom) == 0:
        df_players = df_players.copy()
        df_players["kp_rank"] = np.nan
        df_players["kp_mult"] = KENPOM_DEFAULT_MULT
        return df_players

    kp_teams = df_kenpom["kp_team"].tolist()   # already normalized

    # Cache: one lookup per unique CBBD team
    cbbd_teams  = df_players["Team"].unique()
    match_cache = {}
    unmatched   = []
    for ct in cbbd_teams:
        km = _best_team_match(ct, kp_teams)
        match_cache[ct] = km
        if km is None:
            unmatched.append(ct)

    if unmatched:
        print(f"  [KenPom] {len(unmatched)} unmatched "
              f"(default ×{KENPOM_DEFAULT_MULT}): "
              f"{', '.join(unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    kp_idx = df_kenpom.set_index("kp_team")

    def _get(cbbd_team, col, default):
        km = match_cache.get(cbbd_team)
        if km and km in kp_idx.index:
            val = kp_idx.at[km, col]
            return val if not pd.isna(val) else default
        return default

    df = df_players.copy()
    df["kp_rank"]   = df["Team"].apply(lambda t: _get(t, "kp_rank",   np.nan))
    df["kp_mult"]   = df["Team"].apply(lambda t: _get(t, "kp_mult",   KENPOM_DEFAULT_MULT))
    if "kp_netrtg" in kp_idx.columns:
        df["kp_netrtg"] = df["Team"].apply(lambda t: _get(t, "kp_netrtg", np.nan))

    return df


# ──────────────────────────────────────────────
#  NIL ESTIMATOR  (tiered market model)
# ──────────────────────────────────────────────

def nil_tiered(final_score, floor=NIL_FLOOR):
    """
    Tiered NIL estimate calibrated to the 2025-26 market:
      Elite  (Final ≥ 50): $1M + $150K per point above 50
      High   (Final ≥ 38): $400K + $50K per point above 38
      Mid    (Final ≥ 28): $150K + $25K per point above 28
      Role   (Final < 28): $50K floor
    """
    if pd.isna(final_score):
        return floor
    if final_score >= 50:
        return max(floor, 1_000_000 + (final_score - 50) * 150_000)
    elif final_score >= 38:
        return max(floor, 400_000  + (final_score - 38) * 50_000)
    elif final_score >= 28:
        return max(floor, 150_000  + (final_score - 28) * 25_000)
    return floor


def nil_display(value):
    """Format NIL value for compact display."""
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.0f}K"
    return f"${value:,.0f}"


# ──────────────────────────────────────────────
#  CUSTOM FORMULA
# ──────────────────────────────────────────────

def compute_custom_score(df, df_kenpom=None):
    """
    Apply the custom formula to a player DataFrame.
    System 2: (PER + WS) + ((eFG% + TS%) / 4) + PRA × KenPom Mult

    Uses computed PER (normalized avg=15) NOT PORPAG.
    Adds: BaseScore, PRACombo, ComboScore,
          KenPomRank, KenPomMult, FinalScore, NILValue, NILDisplay
    """
    df = merge_kenpom(df, df_kenpom)

    def col(name, default=0):
        return pd.to_numeric(df.get(name, default),
                             errors="coerce").fillna(default)

    # Use computed PER (avg=15 normalized) for PER term in formula
    per      = col("PER")        # computed PER, normalized to avg 15
    ws_total = col("WS_total")   # CBBD native total win shares
    efg      = col("eFGPct")
    ts_raw   = col("TS_cbbd")

    ts_pct  = np.where(ts_raw < 1.0, ts_raw * 100, ts_raw)
    efg_pct = np.where(efg    < 1.0, efg    * 100, efg)

    ppg = col("PTS")
    rpg = col("Tot")
    apg = col("AST")

    # Base Score = (PER + WS_total) + ((eFG% + TS%) / 4)
    base_score  = (per + ws_total) + ((efg_pct + ts_pct) / 4)
    pra_combo   = (ppg * 0.5) + (rpg * 0.33) + (apg * 0.5)
    combo_score = base_score + pra_combo
    kp_mult     = pd.to_numeric(df["kp_mult"], errors="coerce").fillna(
                      KENPOM_DEFAULT_MULT)
    final_score = combo_score * kp_mult

    nil_vals = final_score.apply(nil_tiered)

    df = df.copy()
    df["BaseScore"]  = base_score.round(2)
    df["PRACombo"]   = pra_combo.round(2)
    df["ComboScore"] = combo_score.round(2)
    df["KenPomRank"] = df["kp_rank"]
    df["KenPomMult"] = kp_mult.round(3)
    df["FinalScore"] = final_score.round(2)
    df["NILValue"]   = nil_vals.round(0).astype(int)
    df["NILDisplay"] = nil_vals.apply(nil_display)

    return df


# ──────────────────────────────────────────────
#  DENSITY SHADING  (Red → Yellow → Green)
# ──────────────────────────────────────────────

# Colour stops
_RED    = (0xda, 0x36, 0x33)   # #da3633
_YELLOW = (0xd2, 0x99, 0x22)   # #d29922
_GREEN  = (0x3f, 0xb9, 0x50)   # #3fb950

def _lerp_colour(c1, c2, t):
    """Linear interpolate between two RGB tuples; t in [0,1]."""
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"

def density_colour(value, col_min, col_max, invert=False):
    """
    Map a value to a hex colour on the Red→Yellow→Green gradient.
    invert=True flips so lower values are greener (for KP Rank).
    """
    if col_max == col_min:
        return _lerp_colour(_RED, _GREEN, 0.5)
    t = (value - col_min) / (col_max - col_min)   # 0 = min, 1 = max
    t = max(0.0, min(1.0, t))
    if invert:
        t = 1.0 - t
    # Midpoint at t=0.5 is yellow
    if t < 0.5:
        return _lerp_colour(_RED, _YELLOW, t * 2)
    else:
        return _lerp_colour(_YELLOW, _GREEN, (t - 0.5) * 2)


def build_shade_map(df, cols_high=None, cols_low=None):
    """
    Pre-compute per-column shade maps for all rows.
    Returns dict: { col_display_name: { row_index: hex_colour } }
    Used by the GUI treeview to tag each cell.

    cols_high: list of df column names where higher = greener
    cols_low:  list of df column names where lower  = greener
    """
    if cols_high is None: cols_high = []
    if cols_low  is None: cols_low  = []

    shade_map = {}
    for col, invert in ([(c, False) for c in cols_high] +
                        [(c, True)  for c in cols_low]):
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        valid  = series.dropna()
        if len(valid) == 0:
            continue
        lo, hi = valid.quantile(0.05), valid.quantile(0.95)
        shade_map[col] = {}
        for idx, val in series.items():
            if pd.isna(val):
                shade_map[col][idx] = "#30363d"   # BORDER colour for N/A
            else:
                shade_map[col][idx] = density_colour(val, lo, hi, invert)
    return shade_map


# Mapping from LB_COLS display names to df column names for shading
SHADE_COL_MAP_HIGH = {
    "TS%":      "TS%",
    "eFG%":     "eFGPct",
    "PER":      "PER",
    "PORPAG":   "PORPAG",
    "WS/40":    "WS40",
    "S2: Final":"FinalScore",
    "S2: NIL":  "NILValue",
    "Base":     "BaseScore",
    "PRA":      "PRACombo",
    "S1: Score":"PortalScore",
    "USG%":     "USG%",
    "PTS":      "PTS",
}
SHADE_COL_MAP_LOW = {
    "KP Rank":  "KenPomRank",
}
