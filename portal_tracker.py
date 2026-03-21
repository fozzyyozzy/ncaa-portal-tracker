"""
=============================================================
  NCAA D-I Men's Basketball Transfer Portal Tracker
  Composite Portal Score Algorithm v1.0
=============================================================
  Metrics computed:
    - TS%   (True Shooting Percentage)
    - PER   (Simplified Player Efficiency Rating)
    - WS40  (Win Shares per 40 Minutes, estimated)
    - USG%  (Usage Rate — already in source as %Tm)
    - DEF   (Defensive Composite: STL + BLK per 40 min)
    - CONF  (Conference Strength Multiplier via Barttorvik tiers)

  Eligibility filter: FR / SO / JR / SR / GR (5th)
  Minimum thresholds: 10+ games, 50+ total minutes
=============================================================
"""

import pandas as pd
import numpy as np
import os
import sys
from datetime import datetime

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────

CSV_PATH = "Book2.csv"          # path to your stats CSV
OUTPUT_CSV = "portal_scores.csv"
OUTPUT_SUMMARY = "portal_summary.txt"

MIN_GAMES = 10
MIN_TOTAL_MINUTES = 50

# Composite score weights (must sum to 1.0)
WEIGHTS = {
    "PER":  0.25,
    "TS":   0.20,
    "WS40": 0.15,
    "USG":  0.10,
    "DEF":  0.15,
    "CONF": 0.15,
}

# ──────────────────────────────────────────────
#  CONFERENCE STRENGTH MULTIPLIERS
#  Source: Barttorvik composite tier ratings
#  Scale: 1.00 = average D1 | 1.20 = elite
# ──────────────────────────────────────────────

CONF_MULTIPLIERS = {
    # Power / High-Major
    "DUKE":  1.20, "KAN":   1.20, "KY":    1.20, "UNC":   1.18,
    "GONZ":  1.17, "HOUS":  1.17, "AUB":   1.17, "TENN":  1.17,
    "IOWA":  1.16, "MICH":  1.16, "ARK":   1.16, "OSU":   1.16,
    "ILL":   1.15, "MARQ":  1.15, "NOVA":  1.15, "CREI":  1.15,
    "NU":    1.15, "PURD":  1.15, "MSU":   1.15, "IND":   1.14,
    "MIA":   1.14, "UVA":   1.14, "BAY":   1.14, "TCU":   1.14,
    "TTU":   1.14, "TEX":   1.14, "MSST":  1.13, "ALA":   1.13,
    "LSU":   1.13, "FLST":  1.13, "CLEM":  1.13, "GTCH":  1.13,
    "WIS":   1.13, "MINN":  1.12, "PSU":   1.12, "RUT":   1.12,
    "NEB":   1.12, "MD":    1.12, "STAN":  1.12, "UTAH":  1.12,
    "ASU":   1.12, "WSU":   1.11, "OSU":   1.11, "ORE":   1.11,
    "WASH":  1.11, "UCLA":  1.11, "ORST":  1.11, "CAL":   1.11,
    "SMU":   1.11, "TUL":   1.11, "CINC":  1.11, "UAB":   1.10,
    "WKU":   1.10, "FAU":   1.10, "MRSH":  1.10, "LOU":   1.10,
    "PITT":  1.10, "SYR":   1.10, "BC":    1.10, "ND":    1.10,
    "ACC":   1.10, "WFU":   1.10, "VT":    1.10,
    # Mid-Major (upper)
    "BYU":   1.09, "BUF":   1.09, "BALL":  1.09, "TOL":   1.09,
    "NMX":   1.09, "SDST":  1.09, "UNLV":  1.09, "SJSU":  1.08,
    "WICH":  1.08, "DRKE":  1.08, "BRAD":  1.08, "EVAN":  1.08,
    "VAN":   1.08, "WVU":   1.08, "NCST":  1.08, "WAKE":  1.08,
    "USA":   1.07, "SBU":   1.07, "HOF":   1.07, "DREX":  1.07,
    "URI":   1.07, "UNF":   1.07, "DEN":   1.07, "LONB":  1.07,
    "KSU":   1.07,
    # Mid-Major (lower)
    "ECU":   1.05, "AKR":   1.05, "BGSU":  1.05, "NKU":   1.05,
    "IND":   1.05, "IPFW":  1.04, "DRK":   1.04, "ELON":  1.04,
    "WOF":   1.04, "SAM":   1.04, "CBU":   1.04, "BELL":  1.04,
    "CARK":  1.04, "CHSO":  1.04, "NCOLO": 1.04, "STB":   1.04,
    "ROWAN": 1.03, "TUL":   1.05, "UNC":   1.05,
    # Low-Major / HBCU / Small Conference (default 1.00 via fallback)
}

DEFAULT_CONF_MULTIPLIER = 1.00   # fallback for unlisted teams


# ──────────────────────────────────────────────
#  ELIGIBILITY LABEL MAP
#  User provides year in class; we tag each player
#  NOTE: Without a year-in-school column in the CSV,
#  the tool prompts you to add it or assigns 'UNK'
# ──────────────────────────────────────────────

ELIG_ORDER = ["FR", "SO", "JR", "SR", "GR", "UNK"]


# ──────────────────────────────────────────────
#  DATA LOADING
# ──────────────────────────────────────────────

def load_data(path: str) -> pd.DataFrame:
    """Load the stats CSV, handling duplicate 'Pct' column names."""
    with open(path, encoding="utf-8-sig") as f:
        header_line = f.readline().strip()

    raw_headers = header_line.split(",")
    # Rename duplicate Pct columns to FGPct, P3Pct, FTPct
    pct_count = 0
    pct_names = ["FGPct", "P3Pct", "FTPct"]
    new_headers = []
    for col in raw_headers:
        if col == "Pct":
            new_headers.append(pct_names[pct_count])
            pct_count += 1
        else:
            new_headers.append(col)

    df = pd.read_csv(path, encoding="utf-8-sig", skiprows=1, header=None,
                     names=new_headers)

    # Strip % signs and convert to float where needed
    for col in ["%Tm", "FGPct", "P3Pct", "FTPct"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace("%", "").astype(float)

    numeric_cols = ["G", "GS", "Min", "Poss", "PTS", "FGM", "FGA",
                    "3PM", "3PA", "FTM", "FTA", "Tot", "Def", "Off",
                    "AST", "PF", "TO", "BLK", "STL", "%Tm"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["TotalMin"] = df["G"] * df["Min"]
    return df


# ──────────────────────────────────────────────
#  FILTERING
# ──────────────────────────────────────────────

def apply_filters(df: pd.DataFrame,
                  min_games: int = MIN_GAMES,
                  min_total_min: int = MIN_TOTAL_MINUTES,
                  elig_filter: list = None) -> pd.DataFrame:
    df = df[(df["G"] >= min_games) & (df["TotalMin"] >= min_total_min)].copy()
    if elig_filter and "Elig" in df.columns:
        df = df[df["Elig"].isin(elig_filter)]
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────
#  METRIC CALCULATIONS
# ──────────────────────────────────────────────

def calc_ts(df: pd.DataFrame) -> pd.Series:
    """True Shooting %: PTS / (2 * (FGA + 0.44 * FTA))"""
    denom = 2 * (df["FGA"] + 0.44 * df["FTA"])
    return np.where(denom > 0, df["PTS"] / denom, 0.0)


def calc_per(df: pd.DataFrame) -> pd.Series:
    """
    Simplified PER (college approximation).
    Uses per-game averages scaled to 40 minutes.
    League average PER targets ~15.0.
    """
    mp = df["Min"].replace(0, np.nan)

    # Raw contribution per minute
    raw = (
        df["PTS"] * 1.0
        + df["Tot"] * 1.2
        + df["AST"] * 1.5
        + df["STL"] * 2.0
        + df["BLK"] * 2.0
        - df["TO"]  * 2.0
        - (df["FGA"] - df["FGM"]) * 0.7
        - (df["FTA"] - df["FTM"]) * 0.4
        - df["PF"]  * 0.6
    ) / mp

    # Scale to per-40 and normalize so league avg ≈ 15
    per40 = raw * 40
    # Normalize relative to dataset median
    median_val = per40.median()
    if median_val != 0:
        per40_normalized = per40 / median_val * 15.0
    else:
        per40_normalized = per40
    return per40_normalized.fillna(0)


def calc_ws40(df: pd.DataFrame) -> pd.Series:
    """
    Estimated Win Shares per 40 minutes.
    Simplified: combines offensive and defensive contribution proxies.
    """
    mp = df["Min"].replace(0, np.nan)

    # Offensive proxy: points produced efficiency
    off_ws = (df["PTS"] + df["AST"] * 1.5 - df["TO"] * 2.0) / mp * 40

    # Defensive proxy: stops per 40
    def_ws = (df["STL"] * 2.0 + df["BLK"] * 1.5 + df["Def"] * 0.7) / mp * 40

    # Combine; weights favor offense slightly
    ws40_raw = (off_ws * 0.55 + def_ws * 0.45) / 20.0   # scale to ~0–1 range
    return ws40_raw.fillna(0)


def calc_def_composite(df: pd.DataFrame) -> pd.Series:
    """Defensive composite: STL + BLK per 40 min, weighted."""
    mp = df["Min"].replace(0, np.nan)
    return ((df["STL"] * 2.0 + df["BLK"] * 1.5) / mp * 40).fillna(0)


def calc_usg(df: pd.DataFrame) -> pd.Series:
    """
    Usage rate from %Tm column (already percentage in source).
    Sweet spot: 18–28%. Penalty for <15% (role player), diminishing above 30%.
    """
    usg = df["%Tm"].copy()
    # Normalize: apply soft cap above 28% (prevents ball-stopper inflation)
    usg_adj = np.where(usg > 28, 28 + (usg - 28) * 0.3, usg)
    return pd.Series(usg_adj, index=df.index)


def conf_multiplier(df: pd.DataFrame) -> pd.Series:
    return df["Team"].map(lambda t: CONF_MULTIPLIERS.get(t.upper(), DEFAULT_CONF_MULTIPLIER))


# ──────────────────────────────────────────────
#  NORMALIZATION HELPER
# ──────────────────────────────────────────────

def normalize_zscore(series: pd.Series, clip: float = 3.0) -> pd.Series:
    """Z-score normalize, clip outliers, then rescale to 0–100."""
    std = series.std()
    mean = series.mean()
    if std == 0:
        return pd.Series(50.0, index=series.index)
    z = (series - mean) / std
    z = z.clip(-clip, clip)
    # Rescale [-3, 3] → [0, 100]
    return ((z + clip) / (2 * clip) * 100).round(2)


# ──────────────────────────────────────────────
#  COMPOSITE SCORE
# ──────────────────────────────────────────────

def compute_composite(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Raw metrics
    df["TS_raw"]   = calc_ts(df)
    df["PER_raw"]  = calc_per(df)
    df["WS40_raw"] = calc_ws40(df)
    df["DEF_raw"]  = calc_def_composite(df)
    df["USG_raw"]  = calc_usg(df)
    df["CONF_raw"] = conf_multiplier(df)

    # Normalize each metric to 0–100
    df["TS_score"]   = normalize_zscore(df["TS_raw"])
    df["PER_score"]  = normalize_zscore(df["PER_raw"])
    df["WS40_score"] = normalize_zscore(df["WS40_raw"])
    df["DEF_score"]  = normalize_zscore(df["DEF_raw"])
    df["USG_score"]  = normalize_zscore(df["USG_raw"])
    # CONF multiplier: normalize differently — center on 1.00
    conf_pct = (df["CONF_raw"] - 1.00) / 0.20 * 100   # 0 pts at 1.00, 100 pts at 1.20
    df["CONF_score"] = conf_pct.clip(0, 100).round(2)

    # Weighted composite
    df["PortalScore"] = (
        df["PER_score"]  * WEIGHTS["PER"]
        + df["TS_score"]   * WEIGHTS["TS"]
        + df["WS40_score"] * WEIGHTS["WS40"]
        + df["USG_score"]  * WEIGHTS["USG"]
        + df["DEF_score"]  * WEIGHTS["DEF"]
        + df["CONF_score"] * WEIGHTS["CONF"]
    ).round(2)

    # Add readable raw metric columns
    df["TS%"]  = (df["TS_raw"] * 100).round(1)
    df["PER"]  = df["PER_raw"].round(1)
    df["WS40"] = df["WS40_raw"].round(3)
    df["USG%"] = df["%Tm"].round(1)
    df["DEF_per40"] = df["DEF_raw"].round(2)
    df["ConfMult"]  = df["CONF_raw"].round(3)

    return df


# ──────────────────────────────────────────────
#  DISPLAY FORMATTING
# ──────────────────────────────────────────────

def print_banner():
    print("=" * 72)
    print("  NCAA D-I MEN'S BASKETBALL  |  TRANSFER PORTAL TRACKER  |  v1.0")
    print(f"  Generated: {datetime.now().strftime('%B %d, %Y  %H:%M')}")
    print("=" * 72)


def print_leaderboard(df: pd.DataFrame, n: int = 50, title: str = "TOP PORTAL PROSPECTS"):
    cols = ["Rank", "Player", "Team", "G", "PTS", "TS%", "PER", "WS40",
            "USG%", "DEF_per40", "ConfMult", "PortalScore"]

    top = df.sort_values("PortalScore", ascending=False).head(n).copy()
    top.insert(0, "Rank", range(1, len(top) + 1))
    if "Elig" in df.columns:
        cols.insert(3, "Elig")

    display = top[cols]

    print(f"\n{'─'*72}")
    print(f"  {title}  (n={n})")
    print(f"{'─'*72}")
    print(display.to_string(index=False, float_format="%.2f"))
    print()


def print_tier_breakdown(df: pd.DataFrame):
    """Print players bucketed into tiers by PortalScore."""
    tiers = [
        ("🔵 ELITE  (85–100)", 85, 101),
        ("🟢 HIGH   (70–84)",  70,  85),
        ("🟡 SOLID  (55–69)",  55,  70),
        ("🟠 FRINGE (40–54)",  40,  55),
        ("🔴 DEPTH  (<40)",     0,  40),
    ]
    print(f"\n{'─'*72}")
    print("  TIER BREAKDOWN")
    print(f"{'─'*72}")
    for label, lo, hi in tiers:
        tier_df = df[(df["PortalScore"] >= lo) & (df["PortalScore"] < hi)]
        print(f"  {label}  →  {len(tier_df)} players")
    print()


def print_team_summary(df: pd.DataFrame, top_n: int = 10):
    """Top teams by average portal score of their qualifying players."""
    team_avg = (df.groupby("Team")["PortalScore"]
                .agg(["mean", "count"])
                .rename(columns={"mean": "AvgScore", "count": "Players"})
                .query("Players >= 2")
                .sort_values("AvgScore", ascending=False)
                .head(top_n))
    team_avg["AvgScore"] = team_avg["AvgScore"].round(2)

    print(f"\n{'─'*72}")
    print(f"  TOP {top_n} TEAMS BY AVERAGE PORTAL SCORE  (min 2 qualifying players)")
    print(f"{'─'*72}")
    print(team_avg.to_string())
    print()


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────

def main():
    print_banner()

    # ── Load ──
    if not os.path.exists(CSV_PATH):
        sys.exit(f"\n[ERROR] Could not find '{CSV_PATH}'.\n"
                 f"        Place Book2.csv (or update CSV_PATH) in the same\n"
                 f"        directory and re-run.\n")

    print(f"\n  Loading data from: {CSV_PATH}")
    df_raw = load_data(CSV_PATH)
    print(f"  Total players in file:        {len(df_raw):,}")

    # ── Optional: eligibility column ──
    if "Elig" not in df_raw.columns:
        print("\n  [NOTE] No 'Elig' column found in CSV.")
        print("         To enable eligibility filtering, add a column named 'Elig'")
        print("         with values: FR / SO / JR / SR / GR")
        print("         Continuing without eligibility filter.\n")
        elig_filter = None
    else:
        # Prompt for filter
        print(f"\n  Eligibility values found: {sorted(df_raw['Elig'].dropna().unique())}")
        user_in = input("  Filter by eligibility? Enter comma-separated values or ENTER to skip: ").strip()
        if user_in:
            elig_filter = [e.strip().upper() for e in user_in.split(",")]
        else:
            elig_filter = None

    # ── Filter ──
    df = apply_filters(df_raw, MIN_GAMES, MIN_TOTAL_MINUTES, elig_filter)
    print(f"  Qualifying players (≥{MIN_GAMES}G, ≥{MIN_TOTAL_MINUTES} total min): {len(df):,}")

    # ── Compute ──
    print("  Computing metrics: TS%, PER, WS/40, USG%, DEF composite, CONF multiplier...")
    df = compute_composite(df)
    print("  Composite portal scores computed.\n")

    # ── Display ──
    print_leaderboard(df, n=50)
    print_tier_breakdown(df)
    print_team_summary(df, top_n=10)

    # ── Export ──
    export_cols = ["Player", "Team", "G", "Min", "PTS", "TS%", "PER",
                   "WS40", "USG%", "DEF_per40", "ConfMult",
                   "PER_score", "TS_score", "WS40_score",
                   "USG_score", "DEF_score", "CONF_score", "PortalScore"]
    if "Elig" in df.columns:
        export_cols.insert(2, "Elig")

    out = df.sort_values("PortalScore", ascending=False)[export_cols]
    out.to_csv(OUTPUT_CSV, index=False)
    print(f"  [✓] Full scored table exported → {OUTPUT_CSV}")
    print(f"  [✓] Contains {len(out):,} ranked players\n")
    print("=" * 72)


if __name__ == "__main__":
    main()
