"""
=============================================================
  NCAA D-I Men's Basketball Transfer Portal Tracker
  portal_tracker_cbbd.py  |  v2.0  — CBBD Native
=============================================================
  WHAT CHANGED FROM v1.0:
    - Data source: CBBD API (collegebasketballdata.com)
      instead of Book2.csv
    - TS%, WS/40, USG% now pulled directly from CBBD
      (no manual calculation needed)
    - PER replaced by PORPAG (Points Over Replacement Per
      Adjusted Game) — a superior CBBD-native metric
    - Conference strength now driven by live adjusted
      efficiency ratings from CBBD RatingsApi (Barttorvik)
      instead of our hand-coded multiplier table
    - Position and year-in-school (Elig) now native fields
    - Caches API data locally so re-runs don't burn API calls

  STILL THE SAME:
    - Composite scoring algorithm and weights
    - Normalization (z-score → 0–100)
    - All output formats (terminal + CSV)
    - portal_gui.py compatibility (drop-in replacement)

  REQUIREMENTS:
    pip install cbbd pandas numpy

  USAGE:
    python portal_tracker_cbbd.py
    Or import for GUI:
        from portal_tracker_cbbd import load_data, apply_filters,
                                        compute_composite, WEIGHTS
=============================================================
"""

import pandas as pd
import numpy as np
import os
import sys
import json
from datetime import datetime, date

# Custom formula engine (KenPom + NIL)
try:
    from formula_engine import compute_custom_score, load_kenpom, KENPOM_FILE
    FORMULA_ENGINE_AVAILABLE = True
except ImportError:
    FORMULA_ENGINE_AVAILABLE = False

# ──────────────────────────────────────────────
#  CONFIGURATION  — edit these
# ──────────────────────────────────────────────

CBBD_API_KEY   = "YOUR_API_KEY_HERE"   # paste your CBBD key here
SEASON         = 2026                  # 2026 = 2025-26 season
OUTPUT_CSV     = "portal_scores_v2.csv"
CACHE_FILE     = "cbbd_cache.json"     # local cache — delete to force refresh

MIN_GAMES         = 10
MIN_TOTAL_MINUTES = 50

# Composite score weights — must sum to 1.0
# PORPAG replaces PER; CONF now uses live adjusted efficiency
WEIGHTS = {
    "PORPAG": 0.25,   # Points Over Replacement Per Adjusted Game (CBBD)
    "TS":     0.20,   # True Shooting % (CBBD native)
    "WS40":   0.15,   # Win Shares per 40 min (CBBD native)
    "USG":    0.10,   # Usage % (CBBD native)
    "DEF":    0.15,   # Defensive composite: def_rating + stl + blk
    "CONF":   0.15,   # Conference strength via live adjusted efficiency
}

# ──────────────────────────────────────────────
#  CBBD API SETUP
# ──────────────────────────────────────────────

def get_cbbd_config():
    try:
        import cbbd
        configuration = cbbd.Configuration(access_token=CBBD_API_KEY)
        return cbbd, configuration
    except ImportError:
        sys.exit("[ERROR] cbbd not installed. Run: pip install cbbd")

# ──────────────────────────────────────────────
#  CACHE HELPERS  (saves API calls)
# ──────────────────────────────────────────────

def load_cache(path):
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        # Invalidate cache if it's from a different season or >7 days old
        if (data.get("season") == SEASON and
                data.get("date") == str(date.today())):
            return data
    return {}

def save_cache(path, data):
    data["season"] = SEASON
    data["date"]   = str(date.today())
    with open(path, "w") as f:
        json.dump(data, f)

# ──────────────────────────────────────────────
#  DATA FETCHING
# ──────────────────────────────────────────────

def fetch_player_stats(cbbd, configuration, cache):
    """Fetch player season stats from CBBD StatsApi."""
    if "player_stats" in cache:
        print("  [cache] Player stats loaded from local cache.")
        return cache["player_stats"]

    print(f"  Fetching player season stats (season={SEASON})...")
    with cbbd.ApiClient(configuration) as client:
        api   = cbbd.StatsApi(client)
        stats = api.get_player_season_stats(season=SEASON)

    records = []
    for p in stats:
        # Safely extract nested objects
        fg  = p.field_goals       or type('', (), {'pct': 0, 'attempted': 0, 'made': 0})()
        ft  = p.free_throws       or type('', (), {'pct': 0, 'attempted': 0, 'made': 0})()
        tp  = p.three_point_field_goals or type('', (), {'pct': 0, 'attempted': 0, 'made': 0})()
        reb = p.rebounds          or type('', (), {'total': 0, 'defensive': 0, 'offensive': 0})()
        ws  = p.win_shares        or type('', (), {'total': 0, 'total_per40': 0,
                                                    'offensive': 0, 'defensive': 0})()

        records.append({
            "athlete_id":     p.athlete_id,
            "Player":         p.name or "",
            "Team":           p.team or "",
            "Conference":     p.conference or "",
            "Position":       p.position or "",
            "G":              p.games or 0,
            "GS":             p.starts or 0,
            "TotalMin":       p.minutes or 0,
            "Min":            round((p.minutes or 0) / max(p.games or 1, 1), 1),
            "PTS":            round((p.points or 0) / max(p.games or 1, 1), 1),
            "FGM":            round(fg.made    / max(p.games or 1, 1), 1),
            "FGA":            round(fg.attempted / max(p.games or 1, 1), 1),
            "FGPct":          fg.pct or 0,
            "3PM":            round(tp.made    / max(p.games or 1, 1), 1),
            "3PA":            round(tp.attempted / max(p.games or 1, 1), 1),
            "FTM":            round(ft.made    / max(p.games or 1, 1), 1),
            "FTA":            round(ft.attempted / max(p.games or 1, 1), 1),
            "Tot":            round((reb.total    or 0) / max(p.games or 1, 1), 1),
            "Def":            round((reb.defensive or 0) / max(p.games or 1, 1), 1),
            "Off":            round((reb.offensive or 0) / max(p.games or 1, 1), 1),
            "AST":            round((p.assists  or 0) / max(p.games or 1, 1), 1),
            "STL":            round((p.steals   or 0) / max(p.games or 1, 1), 1),
            "BLK":            round((p.blocks   or 0) / max(p.games or 1, 1), 1),
            "TO":             round((p.turnovers or 0) / max(p.games or 1, 1), 1),
            "PF":             round((p.fouls    or 0) / max(p.games or 1, 1), 1),
            # CBBD native advanced metrics
            "TS_cbbd":        p.true_shooting_pct or 0,     # already 0–1 decimal
            "USG_cbbd":       p.usage or 0,                  # integer %
            "WS40_cbbd":      ws.total_per40 or 0,
            "WS_total":       ws.total or 0,
            "WS_off":         ws.offensive or 0,
            "WS_def":         ws.defensive or 0,
            "PORPAG":         p.porpag or 0,
            "OrtgPlayer":     p.offensive_rating or 0,
            "DrtgPlayer":     p.defensive_rating or 0,
            "NetRtgPlayer":   p.net_rating or 0,
            "ASTtoTO":        p.assists_turnover_ratio or 0,
            "eFGPct":         p.effective_field_goal_pct or 0,
            "FTRate":         p.free_throw_rate or 0,
            "ORebPct":        p.offensive_rebound_pct or 0,
        })

    cache["player_stats"] = records
    print(f"  {len(records):,} player records fetched.")
    return records


def fetch_adjusted_efficiency(cbbd, configuration, cache):
    """
    Fetch team adjusted efficiency ratings.
    Used to build a live conference strength multiplier
    instead of our hand-coded table.
    """
    if "ratings" in cache:
        print("  [cache] Ratings loaded from local cache.")
        return cache["ratings"]

    print("  Fetching adjusted efficiency ratings...")
    with cbbd.ApiClient(configuration) as client:
        api     = cbbd.RatingsApi(client)
        ratings = api.get_adjusted_efficiency(SEASON)

    records = []
    for r in ratings:
        records.append({
            "team":         r.team or "",
            "conference":   r.conference or "",
            "off_rating":   r.offensive_rating or 100,
            "def_rating":   r.defensive_rating or 100,
            "net_rating":   r.net_rating or 0,
        })

    cache["ratings"] = records
    print(f"  {len(records)} team ratings fetched.")
    return records


# ──────────────────────────────────────────────
#  BUILD CONFERENCE STRENGTH MULTIPLIER
#  Uses live net_rating from CBBD instead of
#  our hand-coded table — scales to 1.00–1.20
# ──────────────────────────────────────────────

def build_conf_multiplier_map(ratings_records):
    """
    Convert CBBD net efficiency ratings into a 1.00–1.20 multiplier.
    Best team (highest net) → 1.20, average team → 1.00, worst → ~0.92
    We clip the floor at 0.98 so HBCU/low-major players aren't over-penalized.
    """
    if not ratings_records:
        return {}, 1.00

    df = pd.DataFrame(ratings_records)
    min_net = df["net_rating"].min()
    max_net = df["net_rating"].max()
    rng     = max_net - min_net if max_net != min_net else 1

    team_map = {}
    for _, row in df.iterrows():
        # Scale net_rating range → 0.98–1.20
        scaled = 0.98 + ((row["net_rating"] - min_net) / rng) * 0.22
        team_map[row["team"].lower()] = round(scaled, 4)

    median_mult = float(df["net_rating"].apply(
        lambda n: 0.98 + ((n - min_net) / rng) * 0.22
    ).median())

    return team_map, median_mult


# ──────────────────────────────────────────────
#  DATAFRAME BUILDER
# ──────────────────────────────────────────────

def load_data(api_key=None, use_cache=True):
    """
    Main entry point — fetches from CBBD and returns a clean DataFrame.
    Compatible with portal_gui.py's load_data() call signature
    (gui passes a file path; if path given and exists, falls back to CSV mode).
    """
    # ── GUI compatibility: if called with a file path, use CSV loader ──
    if api_key and os.path.exists(str(api_key)):
        return _load_from_csv(api_key)

    key = api_key or CBBD_API_KEY
    if key == "YOUR_API_KEY_HERE":
        print("[WARN] No API key set. Falling back to CSV mode.")
        if os.path.exists("Book2_enriched.csv"):
            return _load_from_csv("Book2_enriched.csv")
        elif os.path.exists("Book2.csv"):
            return _load_from_csv("Book2.csv")
        else:
            sys.exit("[ERROR] No API key and no fallback CSV found.")

    cbbd_mod, configuration = get_cbbd_config()
    configuration.access_token = key

    cache = load_cache(CACHE_FILE) if use_cache else {}

    player_records  = fetch_player_stats(cbbd_mod, configuration, cache)
    ratings_records = fetch_adjusted_efficiency(cbbd_mod, configuration, cache)

    if use_cache:
        save_cache(CACHE_FILE, cache)

    df = pd.DataFrame(player_records)
    df["TotalMin"] = pd.to_numeric(df["TotalMin"], errors="coerce").fillna(0)

    # Build live conference multiplier map
    conf_map, median_mult = build_conf_multiplier_map(ratings_records)
    df["ConfMult_raw"] = df["Team"].str.lower().map(
        lambda t: conf_map.get(t, median_mult))

    # ── Auto-enrich eligibility from Book2_enriched.csv ──
    df = _enrich_eligibility(df)

    return df


def _enrich_eligibility(df):
    """
    Merge eligibility (FR/SO/JR/SR) from Book2_enriched.csv.
    CBBD does not provide class year in their stats endpoint.
    Falls back gracefully if the file is not present.
    """
    import re

    # Look for enriched CSV in same folder as this script
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "Book2_enriched.csv"),
        os.path.join(base, "..", "Book2_enriched.csv"),
        "Book2_enriched.csv",
    ]
    elig_path = next((p for p in candidates if os.path.exists(p)), None)

    if elig_path is None:
        df["Elig"] = "UNK"
        return df

    try:
        elig_df = pd.read_csv(elig_path, encoding="utf-8-sig",
                               usecols=["Player", "Elig"])
    except Exception:
        df["Elig"] = "UNK"
        return df

    def _norm(name):
        name = str(name).lower().strip()
        name = re.sub(r"\b(jr\.?|sr\.?|ii|iii|iv)\b", "", name)
        name = re.sub(r"[^a-z ]", "", name)
        return re.sub(r"\s+", " ", name).strip()

    # Build name → elig lookup
    lookup = {
        _norm(row["Player"]): row["Elig"]
        for _, row in elig_df.iterrows()
        if str(row.get("Elig", "UNK")) not in ("UNK", "", "nan")
    }

    df = df.copy()
    df["Elig"] = df["Player"].apply(
        lambda n: lookup.get(_norm(n), "UNK"))

    matched = (df["Elig"] != "UNK").sum()
    print(f"  Eligibility: {matched:,}/{len(df):,} players matched "
          f"from {os.path.basename(elig_path)}")
    return df


def _load_from_csv(path):
    """Legacy CSV loader — used as fallback or when GUI passes a file path."""
    with open(path, encoding="utf-8-sig") as f:
        header_line = f.readline().strip()

    raw_headers = header_line.split(",")
    pct_count, pct_names = 0, ["FGPct", "P3Pct", "FTPct"]
    new_headers = []
    for col in raw_headers:
        if col == "Pct":
            new_headers.append(pct_names[min(pct_count, 2)])
            pct_count += 1
        else:
            new_headers.append(col)

    df = pd.read_csv(path, encoding="utf-8-sig", skiprows=1,
                     header=None, names=new_headers)

    for col in ["%Tm", "FGPct", "P3Pct", "FTPct"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace("%", "").astype(float)

    numeric_cols = ["G", "GS", "Min", "Poss", "PTS", "FGM", "FGA",
                    "3PM", "3PA", "FTM", "FTA", "Tot", "Def", "Off",
                    "AST", "PF", "TO", "BLK", "STL", "%Tm"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["TotalMin"] = df["G"] * df["Min"]
    # CSV mode — no live ratings; use defaults
    df["ConfMult_raw"] = 1.00
    # Map CSV columns to CBBD-style names where needed
    if "%Tm" in df.columns and "USG_cbbd" not in df.columns:
        df["USG_cbbd"] = df["%Tm"]
    if "TS_cbbd" not in df.columns and "FGA" in df.columns:
        denom = 2 * (df["FGA"] + 0.44 * df["FTA"])
        df["TS_cbbd"] = np.where(denom > 0, df["PTS"] / denom, 0)
    if "WS40_cbbd" not in df.columns:
        df["WS40_cbbd"] = 0.0
    if "PORPAG" not in df.columns:
        df["PORPAG"] = 0.0
    return df


# ──────────────────────────────────────────────
#  FILTERING
# ──────────────────────────────────────────────

def apply_filters(df, min_games=MIN_GAMES,
                  min_total_min=MIN_TOTAL_MINUTES,
                  elig_filter=None,
                  exclude_gr=True):
    df = df.copy()
    df = df[(pd.to_numeric(df["G"], errors="coerce").fillna(0) >= min_games) &
            (pd.to_numeric(df["TotalMin"], errors="coerce").fillna(0) >= min_total_min)]
    # Exclude graduate transfers by default (toggle off to include)
    if exclude_gr and "Elig" in df.columns:
        df = df[df["Elig"].str.upper() != "GR"]
    if elig_filter and "Elig" in df.columns:
        df = df[df["Elig"].isin(elig_filter)]
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────
#  NORMALIZATION
# ──────────────────────────────────────────────

def normalize_zscore(series, clip=3.0):
    """Z-score normalize, clip outliers, rescale to 0–100."""
    series = pd.to_numeric(series, errors="coerce").fillna(0)
    std  = series.std()
    mean = series.mean()
    if std == 0:
        return pd.Series(50.0, index=series.index)
    z = ((series - mean) / std).clip(-clip, clip)
    return ((z + clip) / (2 * clip) * 100).round(2)


# ──────────────────────────────────────────────
#  METRIC CALCULATIONS
# ──────────────────────────────────────────────

def calc_per(df):
    """
    Simplified PER normalized to league average = 15.0.
    Uses per-game box score stats from CBBD.
    Formula mirrors the original portal_tracker.py approach.
    """
    mp = pd.to_numeric(df["Min"], errors="coerce").replace(0, np.nan)
    pts = pd.to_numeric(df["PTS"],  errors="coerce").fillna(0)
    reb = pd.to_numeric(df["Tot"],  errors="coerce").fillna(0)
    ast = pd.to_numeric(df["AST"],  errors="coerce").fillna(0)
    stl = pd.to_numeric(df["STL"],  errors="coerce").fillna(0)
    blk = pd.to_numeric(df["BLK"],  errors="coerce").fillna(0)
    tov = pd.to_numeric(df["TO"],   errors="coerce").fillna(0)
    fga = pd.to_numeric(df["FGA"],  errors="coerce").fillna(0)
    fgm = pd.to_numeric(df["FGM"],  errors="coerce").fillna(0)
    fta = pd.to_numeric(df["FTA"],  errors="coerce").fillna(0)
    ftm = pd.to_numeric(df["FTM"],  errors="coerce").fillna(0)
    pf  = pd.to_numeric(df["PF"],   errors="coerce").fillna(0)

    raw = (pts * 1.0 + reb * 1.2 + ast * 1.5 + stl * 2.0 + blk * 2.0
           - tov * 2.0 - (fga - fgm) * 0.7
           - (fta - ftm) * 0.4 - pf * 0.6) / mp * 40

    median = raw.median()
    if median != 0 and not pd.isna(median):
        per = (raw / median * 15.0).fillna(0)
    else:
        per = raw.fillna(0)
    return per.round(2)


def calc_def_composite(df):
    """
    Defensive composite per 40 min.
    Combines player defensive rating (inverted), steals, and blocks.
    """
    mp  = pd.to_numeric(df["Min"],       errors="coerce").replace(0, np.nan)
    stl = pd.to_numeric(df.get("STL", 0), errors="coerce").fillna(0)
    blk = pd.to_numeric(df.get("BLK", 0), errors="coerce").fillna(0)
    stl_blk_per40 = (stl * 2.0 + blk * 1.5) / mp * 40
    drtg = pd.to_numeric(df.get("DrtgPlayer", 100), errors="coerce").fillna(100)
    drtg_score = (110 - drtg).clip(0, 40)
    return (stl_blk_per40 * 0.6 + drtg_score * 0.4).fillna(0)


def calc_usg(df):
    """Usage rate with soft cap at 28%."""
    usg = pd.to_numeric(df.get("USG_cbbd", df.get("%Tm", 20)),
                        errors="coerce").fillna(20)
    return np.where(usg > 28, 28 + (usg - 28) * 0.3, usg)


# ──────────────────────────────────────────────
#  COMPOSITE SCORE  —  DUAL SYSTEM
#
#  System 1: Portal Score (0–100, z-score normalized)
#    Uses computed PER (normalized to avg 15), TS%, WS/40,
#    USG%, DEF composite, CONF multiplier
#    Elite threshold: ≥ 85  High: ≥ 70
#
#  System 2: Custom Score (raw scale, your formula)
#    (PER + WS) + ((eFG% + TS%) / 4) + PRA Combo
#    × KenPom multiplier  →  NIL estimate
# ──────────────────────────────────────────────

def compute_composite(df):
    df = df.copy()

    # ── Shared raw metrics ──
    ts_raw = pd.to_numeric(df.get("TS_cbbd", 0), errors="coerce").fillna(0)
    df["TS_raw"]  = np.where(ts_raw < 1.0, ts_raw * 100, ts_raw)
    df["TS%"]     = df["TS_raw"].round(1)

    df["PER"]     = calc_per(df)          # computed, normalized to avg 15
    df["PORPAG"]  = pd.to_numeric(df.get("PORPAG", 0),
                                   errors="coerce").fillna(0).round(2)
    df["WS40_raw"]= pd.to_numeric(df.get("WS40_cbbd", 0),
                                   errors="coerce").fillna(0)
    df["WS40"]    = df["WS40_raw"].round(3)
    df["USG_raw"] = calc_usg(df)
    df["USG%"]    = pd.to_numeric(df.get("USG_cbbd", 0),
                                   errors="coerce").fillna(0).round(1)
    df["DEF_raw"] = calc_def_composite(df)
    df["DEF_comp"]= df["DEF_raw"].round(2)
    df["CONF_raw"]= pd.to_numeric(df.get("ConfMult_raw", 1.0),
                                   errors="coerce").fillna(1.0)
    df["ConfMult"]= df["CONF_raw"].round(4)

    # ════════════════════════════════════════
    #  SYSTEM 1  —  Portal Score  (0–100)
    #  Uses PER (not PORPAG) as primary rating
    # ════════════════════════════════════════
    df["PER_score"]   = normalize_zscore(df["PER"])
    df["TS_score"]    = normalize_zscore(df["TS_raw"])
    df["WS40_score"]  = normalize_zscore(df["WS40_raw"])
    df["USG_score"]   = normalize_zscore(df["USG_raw"])
    df["DEF_score"]   = normalize_zscore(df["DEF_raw"])
    conf_pct          = ((df["CONF_raw"] - 0.98) / 0.22 * 100).clip(0, 100)
    df["CONF_score"]  = conf_pct.round(2)

    df["PortalScore"] = (
        df["PER_score"]   * WEIGHTS["PORPAG"]   # weight key reused
        + df["TS_score"]  * WEIGHTS["TS"]
        + df["WS40_score"]* WEIGHTS["WS40"]
        + df["USG_score"] * WEIGHTS["USG"]
        + df["DEF_score"] * WEIGHTS["DEF"]
        + df["CONF_score"]* WEIGHTS["CONF"]
    ).round(2)

    # ════════════════════════════════════════
    #  SYSTEM 2  —  Custom Score (your formula)
    #  Applied via formula_engine after KenPom merge
    # ════════════════════════════════════════
    if FORMULA_ENGINE_AVAILABLE:
        kenpom_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), KENPOM_FILE)
        df_kenpom, _ = load_kenpom(kenpom_path)
        df = compute_custom_score(df, df_kenpom)

    return df


# ──────────────────────────────────────────────
#  DISPLAY
# ──────────────────────────────────────────────

def print_banner():
    print("=" * 76)
    print("  NCAA D-I MEN'S BASKETBALL  |  TRANSFER PORTAL TRACKER  |  v2.0 CBBD")
    print(f"  Generated: {datetime.now().strftime('%B %d, %Y  %H:%M')}")
    print("=" * 76)


def print_leaderboard(df, n=50):
    has_elig = "Elig" in df.columns
    has_pos  = "Position" in df.columns

    cols = ["Rank", "Player", "Team"]
    if has_elig:  cols.append("Elig")
    if has_pos:   cols.append("Position")
    cols += ["G", "PTS", "TS%", "PORPAG", "WS40",
             "USG%", "DEF_comp", "ConfMult", "PortalScore"]

    top = df.sort_values("PortalScore", ascending=False).head(n).copy()
    top.insert(0, "Rank", range(1, len(top) + 1))

    print(f"\n{'─'*76}")
    print(f"  TOP {n} PORTAL PROSPECTS")
    print(f"{'─'*76}")
    avail = [c for c in cols if c in top.columns]
    print(top[avail].to_string(index=False, float_format="%.2f"))
    print()


def print_tier_breakdown(df):
    tiers = [
        ("🔵 ELITE  (85–100)", 85, 101),
        ("🟢 HIGH   (70–84)",  70,  85),
        ("🟡 SOLID  (55–69)",  55,  70),
        ("🟠 FRINGE (40–54)",  40,  55),
        ("🔴 DEPTH  (<40)",     0,  40),
    ]
    print(f"\n{'─'*76}")
    print("  TIER BREAKDOWN")
    print(f"{'─'*76}")
    for label, lo, hi in tiers:
        count = len(df[(df["PortalScore"] >= lo) & (df["PortalScore"] < hi)])
        print(f"  {label}  →  {count:,} players")
    print()


def print_team_summary(df, top_n=10):
    grp = (df.groupby("Team")["PortalScore"]
             .agg(["mean", "count"])
             .rename(columns={"mean": "AvgScore", "count": "Players"})
             .query("Players >= 2")
             .sort_values("AvgScore", ascending=False)
             .head(top_n))
    grp["AvgScore"] = grp["AvgScore"].round(2)

    print(f"\n{'─'*76}")
    print(f"  TOP {top_n} TEAMS BY AVG PORTAL SCORE  (min 2 qualifying players)")
    print(f"{'─'*76}")
    print(grp.to_string())
    print()


def print_conf_summary(df):
    """Bonus: average portal score by conference."""
    if "Conference" not in df.columns:
        return
    grp = (df.groupby("Conference")["PortalScore"]
             .agg(["mean", "count"])
             .rename(columns={"mean": "AvgScore", "count": "Players"})
             .query("Players >= 5")
             .sort_values("AvgScore", ascending=False))
    grp["AvgScore"] = grp["AvgScore"].round(2)

    print(f"\n{'─'*76}")
    print("  CONFERENCE PORTAL DEPTH  (min 5 qualifying players)")
    print(f"{'─'*76}")
    print(grp.to_string())
    print()


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────

def main():
    print_banner()

    if CBBD_API_KEY == "YOUR_API_KEY_HERE":
        print("\n  [!] Set your CBBD_API_KEY at the top of this file first.")
        print("      Get your key at: https://collegebasketballdata.com\n")
        sys.exit(1)

    print(f"\n  Season: {SEASON-1}-{str(SEASON)[2:]}  (2025-26)")
    print(f"  Fetching data from CBBD API...\n")

    df_raw = load_data()
    print(f"\n  Total players from API:  {len(df_raw):,}")

    df = apply_filters(df_raw)
    print(f"  Qualifying (≥{MIN_GAMES}G, ≥{MIN_TOTAL_MINUTES} total min): {len(df):,}")

    print("  Computing composite portal scores...")
    df = compute_composite(df)
    print("  Done.\n")

    print_leaderboard(df, n=50)
    print_tier_breakdown(df)
    print_team_summary(df)
    print_conf_summary(df)

    # Export
    export_cols = ["Player", "Team", "Conference", "Position", "G", "Min",
                   "PTS", "TS%", "PORPAG", "WS40", "USG%", "DEF_comp",
                   "ConfMult", "ASTtoTO", "OrtgPlayer", "DrtgPlayer",
                   "PORPAG_score", "TS_score", "WS40_score",
                   "USG_score", "DEF_score", "CONF_score", "PortalScore",
                   # Custom formula columns
                   "BaseScore", "PRACombo", "ComboScore",
                   "KenPomRank", "KenPomMult", "FinalScore", "NILValue"]
    if "Elig" in df.columns:
        export_cols.insert(3, "Elig")

    avail = [c for c in export_cols if c in df.columns]
    out = df.sort_values("PortalScore", ascending=False)[avail]
    out.to_csv(OUTPUT_CSV, index=False)

    print(f"  [✓] Exported {len(out):,} ranked players → {OUTPUT_CSV}")
    print(f"  [✓] Cache saved → {CACHE_FILE}  (re-runs use cache, saving API calls)")
    print(f"\n  API calls used this run: ~2  |  Monthly budget: 3,000")
    print("=" * 76)


if __name__ == "__main__":
    main()
