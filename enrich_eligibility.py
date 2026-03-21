"""
=============================================================
  NCAA D-I Men's Basketball — Eligibility Enrichment Tool
  enrich_eligibility.py  |  v1.0
=============================================================
  WHAT THIS DOES:
    1. Pulls every unique team abbreviation from your stats CSV
    2. Looks up each team's ESPN ID via the ESPN hidden API
    3. Fetches the full roster for each team (returns class year
       + position + jersey number per player — no API key needed)
    4. Fuzzy-matches player names from ESPN rosters to your CSV
    5. Writes Book2_enriched.csv with added columns:
         Elig      → FR / SO / JR / SR / GR
         Position  → G / F / C / G-F / F-C etc.
         Jersey    → jersey number string
         ESPN_ID   → ESPN athlete ID (useful for future lookups)
    6. Saves a JSON cache so re-runs skip already-fetched teams

  REQUIREMENTS:
    pip install pandas requests
    (requests is the only non-stdlib install needed)

  USAGE:
    python3 enrich_eligibility.py
    (run from the same folder as Book2.csv)

  OUTPUT:
    Book2_enriched.csv  — drop-in replacement for portal_tracker.py
    espn_roster_cache.json  — cache file, safe to delete to refresh
    enrichment_report.txt   — match summary log
=============================================================
"""

import pandas as pd
import requests
import json
import time
import re
import os
import sys
from difflib import SequenceMatcher
from datetime import datetime

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────

INPUT_CSV       = "Book2.csv"
OUTPUT_CSV      = "Book2_enriched.csv"
CACHE_FILE      = "espn_roster_cache.json"
REPORT_FILE     = "enrichment_report.txt"

FUZZY_THRESHOLD = 0.82   # 0.0–1.0 — lower = more lenient matches
REQUEST_DELAY   = 0.4    # seconds between ESPN API calls (be polite)
REQUEST_TIMEOUT = 10     # seconds before giving up on a request

# ESPN API base URLs
ESPN_TEAMS_URL  = ("https://site.api.espn.com/apis/site/v2/sports/"
                   "basketball/mens-college-basketball/teams"
                   "?limit=400")
ESPN_ROSTER_URL = ("https://site.api.espn.com/apis/site/v2/sports/"
                   "basketball/mens-college-basketball/teams/"
                   "{team_id}/roster")

# ESPN class year → short label
ESPN_YEAR_MAP = {
    "1": "FR",
    "2": "SO",
    "3": "JR",
    "4": "SR",
    "5": "GR",   # 5th-year / graduate
    "6": "GR",   # medical redshirt extra year edge case
    "Freshman":  "FR",
    "Sophomore": "SO",
    "Junior":    "JR",
    "Senior":    "SR",
    "Graduate":  "GR",
}

# Manual overrides: CSV abbreviation → ESPN full team slug
# Add entries here if the auto-lookup misses a team
MANUAL_TEAM_OVERRIDES = {
    "TARL":  "tarleton-state",
    "CBU":   "cal-baptist",
    "POLY":  "cal-poly",
    "PVAMU": "prairie-view-am",
    "JACST": "jackson-state",
    "IPFW":  "purdue-fort-wayne",
    "CARK":  "central-arkansas",
    "MSST":  "mississippi-state",
    "KENN":  "kennesaw-state",
    "NCOLO": "northern-colorado",
    "JSST":  "jacksonville-state",
    "MVSU":  "mississippi-valley-state",
    "WGA":   "west-georgia",
    "SDK":   "south-dakota",
    "SMIS":  "southern-miss",
    "LONB":  "long-beach-state",
    "DRK":   "drake",
    "BELL":  "bellarmine",
    "SAM":   "samford",
    "STB":   "st-bonaventure",
    "WOF":   "wofford",
    "PST":   "portland-state",
    "BUF":   "buffalo",
    "BGSU":  "bowling-green",
    "CHSO":  "charleston-southern",
    "SJSU":  "san-jose-state",
    "UVSU":  "utah-valley",
    "SJU":   "st-johns-ny",
    "SLU":   "saint-louis",
    "EVAN":  "evansville",
    "GWU":   "george-washington",
    "HOF":   "hofstra",
    "NKU":   "northern-kentucky",
    "SHSU":  "sam-houston-state",
    "LEHI":  "lehigh",
    "LEH":   "lehigh",
    "UIW":   "incarnate-word",
    "UNCA":  "unc-asheville",
    "UNCW":  "unc-wilmington",
    "USD":   "san-diego",
    "SCU":   "santa-clara",
    "NCST":  "nc-state",
    "NCAAB": "",
}

# ──────────────────────────────────────────────
#  DATA LOADING (same dedup logic as portal_tracker)
# ──────────────────────────────────────────────

def load_stats_csv(path: str) -> pd.DataFrame:
    with open(path, encoding="utf-8-sig") as f:
        header_line = f.readline().strip()
    raw_headers = header_line.split(",")
    pct_count = 0
    pct_names = ["FGPct", "P3Pct", "FTPct"]
    new_headers = []
    for col in raw_headers:
        if col == "Pct":
            new_headers.append(pct_names[pct_count])
            pct_count += 1
        else:
            new_headers.append(col)
    df = pd.read_csv(path, encoding="utf-8-sig", skiprows=1,
                     header=None, names=new_headers)
    return df

# ──────────────────────────────────────────────
#  CACHE HELPERS
# ──────────────────────────────────────────────

def load_cache(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_cache(path: str, cache: dict):
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)

# ──────────────────────────────────────────────
#  ESPN TEAM LOOKUP
# ──────────────────────────────────────────────

def fetch_espn_team_map() -> dict:
    """
    Fetch all D1 teams from ESPN and return a dict of:
        abbreviation (upper) → { 'id': ..., 'slug': ..., 'displayName': ... }
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; portal-tracker/1.0)"}
    try:
        resp = requests.get(ESPN_TEAMS_URL, headers=headers,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [WARN] Could not fetch ESPN team list: {e}")
        return {}

    team_map = {}
    for item in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = item.get("team", {})
        abbrev = t.get("abbreviation", "").upper()
        slug   = t.get("slug", "")
        tid    = t.get("id", "")
        name   = t.get("displayName", "")
        if abbrev:
            team_map[abbrev] = {"id": tid, "slug": slug, "name": name}
    return team_map

# ──────────────────────────────────────────────
#  ESPN ROSTER FETCH
# ──────────────────────────────────────────────

def fetch_roster(team_id: str) -> list:
    """
    Fetch the roster for a given ESPN team ID.
    Returns list of dicts with keys:
        name, first_name, last_name, espn_id, jersey, position,
        year_raw, elig
    """
    url = ESPN_ROSTER_URL.format(team_id=team_id)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; portal-tracker/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    [WARN] Roster fetch failed for team {team_id}: {e}")
        return []

    players = []
    for athlete in data.get("athletes", []):
        # ESPN roster returns athletes in position groups
        # Each group has a 'position' key and 'items' list
        position_label = athlete.get("position", {}).get("abbreviation", "")
        items = athlete.get("items", [athlete])  # some formats nest, some don't

        # Handle both flat and nested structures
        if "items" in athlete:
            items = athlete["items"]
        else:
            items = [athlete]

        for a in items:
            full_name  = a.get("displayName", a.get("fullName", "")).strip()
            first_name = a.get("firstName", "").strip()
            last_name  = a.get("lastName", "").strip()
            espn_id    = str(a.get("id", ""))
            jersey     = a.get("jersey", "")
            pos        = (a.get("position", {}).get("abbreviation", "")
                          or position_label)

            # Year / class
            year_raw = str(a.get("year", ""))
            elig = ESPN_YEAR_MAP.get(year_raw, "")
            if not elig:
                # Try experience field
                exp = str(a.get("experience", {}).get("years", ""))
                elig = ESPN_YEAR_MAP.get(exp, "UNK")

            if full_name:
                players.append({
                    "name":       full_name,
                    "first_name": first_name,
                    "last_name":  last_name,
                    "espn_id":    espn_id,
                    "jersey":     jersey,
                    "position":   pos,
                    "year_raw":   year_raw,
                    "elig":       elig,
                })
    return players

# ──────────────────────────────────────────────
#  NAME NORMALIZATION & FUZZY MATCHING
# ──────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, remove suffixes for matching."""
    name = name.lower().strip()
    # Remove suffixes
    name = re.sub(r"\b(jr\.?|sr\.?|ii|iii|iv)\b", "", name)
    # Remove punctuation
    name = re.sub(r"[^a-z ]", "", name)
    # Collapse whitespace
    return re.sub(r"\s+", " ", name).strip()

def fuzzy_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def best_match(csv_name: str, roster: list,
               threshold: float = FUZZY_THRESHOLD):
    """
    Find the best matching player from roster for a CSV player name.
    Returns the roster dict if score >= threshold, else None.
    """
    norm_csv = normalize_name(csv_name)
    best_score = 0.0
    best_player = None

    for player in roster:
        norm_espn = normalize_name(player["name"])
        score = fuzzy_score(norm_csv, norm_espn)
        if score > best_score:
            best_score = score
            best_player = player

    if best_score >= threshold:
        return best_player, best_score
    return None, best_score

# ──────────────────────────────────────────────
#  MAIN ENRICHMENT PIPELINE
# ──────────────────────────────────────────────

def main():
    start_time = datetime.now()

    print("=" * 68)
    print("  NCAA D-I BASKETBALL — ELIGIBILITY ENRICHMENT TOOL  v1.0")
    print(f"  Started: {start_time.strftime('%B %d, %Y  %H:%M')}")
    print("=" * 68)

    # ── Load CSV ──
    if not os.path.exists(INPUT_CSV):
        sys.exit(f"\n[ERROR] '{INPUT_CSV}' not found. Run from the same "
                 "folder as your stats CSV.\n")

    print(f"\n  Loading: {INPUT_CSV}")
    df = load_stats_csv(INPUT_CSV)
    print(f"  Players loaded: {len(df):,}")

    teams_in_csv = sorted(df["Team"].str.upper().unique())
    print(f"  Unique teams:   {len(teams_in_csv)}")

    # ── Load cache ──
    cache = load_cache(CACHE_FILE)
    cached_teams = set(cache.keys())
    print(f"  Cached rosters: {len(cached_teams)}")

    # ── Fetch ESPN team map ──
    print("\n  Fetching ESPN team directory...")
    espn_team_map = fetch_espn_team_map()
    if espn_team_map:
        print(f"  ESPN teams found: {len(espn_team_map)}")
    else:
        print("  [WARN] ESPN team directory unavailable — "
              "will use manual overrides only")
    time.sleep(REQUEST_DELAY)

    # ── Build team → ESPN ID map ──
    team_id_map = {}
    not_found = []
    for abbrev in teams_in_csv:
        if abbrev in espn_team_map:
            team_id_map[abbrev] = espn_team_map[abbrev]["id"]
        elif abbrev in MANUAL_TEAM_OVERRIDES:
            slug = MANUAL_TEAM_OVERRIDES[abbrev]
            # Look up by slug
            for k, v in espn_team_map.items():
                if v.get("slug") == slug:
                    team_id_map[abbrev] = v["id"]
                    break
            else:
                not_found.append(abbrev)
        else:
            not_found.append(abbrev)

    print(f"\n  Teams matched to ESPN IDs: {len(team_id_map)}/{len(teams_in_csv)}")
    if not_found:
        print(f"  Teams NOT matched ({len(not_found)}): "
              f"{', '.join(sorted(not_found)[:20])}"
              + (" ..." if len(not_found) > 20 else ""))
        print("  → Add unmatched teams to MANUAL_TEAM_OVERRIDES in the script.")

    # ── Fetch rosters (with cache) ──
    teams_to_fetch = [t for t in team_id_map
                      if t not in cached_teams]
    print(f"\n  Rosters to fetch from ESPN: {len(teams_to_fetch)}")
    if teams_to_fetch:
        print(f"  Estimated time: ~{len(teams_to_fetch) * REQUEST_DELAY:.0f}s")
        print()

    for i, abbrev in enumerate(teams_to_fetch, 1):
        tid = team_id_map[abbrev]
        print(f"  [{i:3d}/{len(teams_to_fetch)}] Fetching {abbrev:<8} "
              f"(ESPN ID: {tid})", end="", flush=True)
        roster = fetch_roster(tid)
        cache[abbrev] = roster
        print(f" → {len(roster)} players")
        time.sleep(REQUEST_DELAY)

    if teams_to_fetch:
        save_cache(CACHE_FILE, cache)
        print(f"\n  Cache saved → {CACHE_FILE}")

    # ── Match players ──
    print("\n  Matching players to ESPN rosters...")

    elig_col     = []
    position_col = []
    jersey_col   = []
    espn_id_col  = []

    matched_count   = 0
    unmatched_count = 0
    low_conf_count  = 0

    match_log = []  # for report

    for _, row in df.iterrows():
        csv_name = str(row["Player"])
        csv_team = str(row["Team"]).upper()

        roster = cache.get(csv_team, [])

        if not roster:
            elig_col.append("UNK")
            position_col.append("")
            jersey_col.append("")
            espn_id_col.append("")
            unmatched_count += 1
            match_log.append(f"NO_ROSTER  | {csv_team:<8} | {csv_name}")
            continue

        player, score = best_match(csv_name, roster)

        if player:
            elig_col.append(player["elig"] or "UNK")
            position_col.append(player["position"])
            jersey_col.append(player["jersey"])
            espn_id_col.append(player["espn_id"])
            matched_count += 1
            flag = "✓" if score >= 0.92 else "~"
            match_log.append(
                f"{flag} {score:.2f}      | {csv_team:<8} | "
                f"{csv_name:<30} → {player['name']} [{player['elig']}]"
            )
            if score < 0.92:
                low_conf_count += 1
        else:
            elig_col.append("UNK")
            position_col.append("")
            jersey_col.append("")
            espn_id_col.append("")
            unmatched_count += 1
            match_log.append(
                f"✗ {score:.2f}      | {csv_team:<8} | "
                f"{csv_name:<30} — NO MATCH (best={score:.2f})"
            )

    # ── Add columns to DataFrame ──
    df["Elig"]     = elig_col
    df["Position"] = position_col
    df["Jersey"]   = jersey_col
    df["ESPN_ID"]  = espn_id_col

    # ── Export enriched CSV ──
    df.to_csv(OUTPUT_CSV, index=False)

    # ── Summary ──
    total = len(df)
    elapsed = (datetime.now() - start_time).seconds

    print(f"\n  {'─'*60}")
    print(f"  MATCH SUMMARY")
    print(f"  {'─'*60}")
    print(f"  Total players:          {total:,}")
    print(f"  Matched (high conf ✓):  {matched_count - low_conf_count:,}")
    print(f"  Matched (low conf  ~):  {low_conf_count:,}")
    print(f"  Unmatched (UNK):        {unmatched_count:,}")
    print(f"  Match rate:             "
          f"{matched_count/total*100:.1f}%")
    print()

    # Eligibility breakdown
    elig_counts = df["Elig"].value_counts()
    print(f"  ELIGIBILITY BREAKDOWN")
    print(f"  {'─'*30}")
    for label in ["FR", "SO", "JR", "SR", "GR", "UNK"]:
        n = elig_counts.get(label, 0)
        bar = "█" * (n // 50)
        print(f"  {label:<4} {n:>5}  {bar}")
    print()

    print(f"  [✓] Enriched CSV saved  → {OUTPUT_CSV}")
    print(f"  [✓] ESPN cache saved    → {CACHE_FILE}")

    # ── Write report ──
    with open(REPORT_FILE, "w") as f:
        f.write(f"NCAA Portal Tracker — Eligibility Enrichment Report\n")
        f.write(f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}\n")
        f.write(f"{'='*70}\n\n")
        f.write(f"Match rate: {matched_count/total*100:.1f}%  "
                f"({matched_count}/{total} players)\n\n")
        f.write("FLAG  SCORE  | TEAM     | CSV NAME                → "
                "ESPN NAME [ELIG]\n")
        f.write("─" * 70 + "\n")
        for line in match_log:
            f.write(line + "\n")

    print(f"  [✓] Match report saved  → {REPORT_FILE}")
    print(f"\n  Completed in {elapsed}s")
    print("=" * 68)
    print()
    print("  NEXT STEP:")
    print(f"  Copy {OUTPUT_CSV} to your working folder and run:")
    print(f"  → Update CSV_PATH = '{OUTPUT_CSV}' in portal_tracker.py")
    print(f"  → The eligibility filter prompt will now be active")
    print()
    print("  TIP: To filter by eligibility when running portal_tracker.py,")
    print("  enter e.g.:  FR,SO  or  SR,GR  at the prompt.")
    print("=" * 68)


if __name__ == "__main__":
    main()
