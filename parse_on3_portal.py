"""
=============================================================
  parse_on3_portal.py  |  v1.0
  Parses On3 Transfer Portal copy/paste CSV into clean data
=============================================================

  HOW TO GET THE DATA FROM ON3:
  1. Go to on3.com/transfer-portal/
  2. Set filters as desired (Basketball, 2026, etc.)
  3. Select all visible players (scroll to load more first)
  4. Copy all text (Ctrl+A, Ctrl+C)
  5. Paste into a blank Excel sheet
  6. Save as CSV — name it "Portalers.csv"
  7. Run this script or call parse_on3_csv("Portalers.csv")

  OUTPUT:
  - portal_entries.csv  (clean, ready for the app)

  COLUMNS:
  Player, Pos, Elig, Height, Weight, On3Rating,
  Status, LastTeam, NewTeam, Hometown, Source, DateAdded
=============================================================
"""

import pandas as pd
import re
import os
from datetime import date

# ──────────────────────────────────────────────
#  HEIGHT PARSER  (handles Excel date mangling)
# ──────────────────────────────────────────────

MONTH_TO_NUM = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
    'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
    'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
}

# Excel datetime month number → feet (May=5ft, Jun=6ft, Jul=7ft)
MONTH_TO_FEET = {5: 5, 6: 6, 7: 7}

def parse_height(raw):
    """
    Handle all Excel height mangling formats:
    - Full datetime: '2026-06-04 00:00:00' → 6-4
    - Short month-num: 'Jun-04' → 6-4
    - Num-month: '4-Jun' → 6-4
    - Plain: '6-4' → 6-4
    - Inverted: '8-6' → 6-8
    """
    raw = str(raw).strip()

    # Excel full datetime: '2026-06-04 00:00:00' → month=feet, day=inches
    m = re.match(r'^\d{4}-(\d{2})-(\d{2})', raw)
    if m:
        month  = int(m.group(1))
        inches = int(m.group(2))
        if month in MONTH_TO_FEET and 0 <= inches <= 11:
            return f"{MONTH_TO_FEET[month]}-{inches}"
        return ""

    # "Jun-00" style (month = feet)
    m = re.match(r'^([A-Za-z]+)-(\d+)$', raw)
    if m:
        feet = MONTH_TO_NUM.get(m.group(1), 0)
        inches = int(m.group(2))
        if 5 <= feet <= 7 and 0 <= inches <= 11:
            return f"{feet}-{inches}"
        return ""

    # "4-Jun" or "11-Jun" style (num may be feet or inches)
    m = re.match(r'^(\d+)-([A-Za-z]+)$', raw)
    if m:
        a = int(m.group(1))
        b = MONTH_TO_NUM.get(m.group(2), 0)
        if a <= 4:    return f"{b}-{a}"
        if a <= 7:    return f"{a}-{b}"
        if a >= 8:    return f"{b}-{a}"
        return ""

    # Plain "6-4" or inverted "8-6"
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 5 <= a <= 7 and 0 <= b <= 11: return f"{a}-{b}"
        if 5 <= b <= 7 and 0 <= a <= 11: return f"{b}-{a}"
        return ""

    return ""


# ──────────────────────────────────────────────
#  VALUE TYPE DETECTORS
# ──────────────────────────────────────────────

POSITIONS = {'PG','SG','SF','PF','C','G','F','CG','WG','G/F','combo'}
ELIGS      = {'FR','SO','JR','SR','GR','RS-SO','RS-FR','RS-JR','RS-SR','5th'}
STATUSES   = {'Expected','Committed','Withdrawn','Graduate','Entered'}

def is_pos(v):
    return str(v).strip() in POSITIONS

def is_elig(v):
    return str(v).strip() in ELIGS

def is_status(v):
    return str(v).strip() in STATUSES

def is_weight(v):
    try:
        w = int(float(str(v)))
        return 140 <= w <= 380
    except Exception:
        return False

def is_rating(v):
    try:
        r = float(str(v))
        return 60.0 <= r <= 100.0
    except Exception:
        return False

def is_nil_value(v):
    """Skip On3 NIL value strings like $1.9M, $500K"""
    return bool(re.match(r'^\$[\d\.]+[KMBkm]?$', str(v).strip()))

def is_height(v):
    return bool(parse_height(v))

def is_avatar(v):
    return 'Avatar' in str(v)

def is_skip(v):
    v = str(v).strip()
    # Don't skip Excel datetime heights (2026-06-04...) — those go to parse_height
    if re.match(r'^\d{4}-\d{2}-\d{2}', v) and parse_height(v):
        return False
    return (is_avatar(v)
            or v.startswith('Update:')
            or re.match(r'^\d+/\d+/\d+$', v)         # dates like 3/6/2026
            or re.match(r'^\d+/\d+$', v)              # dates like 3/6
            or is_nil_value(v)                         # NIL values like $1.9M
            or v == '-'                                # On3 empty placeholder
            or len(v) > 80
            or v in ('Last Team', 'New Team', 'Status', 'Player',
                     'Pos', 'Rating', 'NIL Value', 'NIL'))


# ──────────────────────────────────────────────
#  MAIN PARSER
# ──────────────────────────────────────────────

def parse_on3_csv(input_path="Portalers.csv",
                  output_path="portal_entries.csv"):
    """
    Parse an On3 copy/paste CSV into a clean portal entries DataFrame.
    Saves to output_path and returns the DataFrame.
    """
    if not os.path.exists(input_path):
        print(f"[ERROR] File not found: {input_path}")
        return pd.DataFrame()

    try:
        if input_path.lower().endswith('.xlsx'):
            raw = pd.read_excel(input_path, header=None)
        else:
            raw = pd.read_csv(input_path, encoding="utf-8-sig", header=None)
        values = raw.iloc[:, 0].fillna("").astype(str).tolist()
    except Exception as e:
        print(f"[ERROR] Could not read file: {e}")
        return pd.DataFrame()

    players = []
    i = 0
    today = str(date.today())

    while i < len(values):
        v = values[i].strip()

        if is_skip(v) or not v:
            i += 1
            continue

        # Position marker starts a new player block
        if is_pos(v):
            pos = v

            # Find player name (next non-skip value)
            j = i + 1
            while j < len(values) and (is_skip(values[j])
                                        or not values[j].strip()):
                j += 1
            if j >= len(values):
                i += 1
                continue

            name = values[j].strip()
            # Must look like a real name: 2+ words, letters only
            if not re.match(r'^[A-Za-z][A-Za-z\'\.\s\-]+ [A-Za-z]', name):
                i += 1
                continue

            # Scan forward collecting player attributes
            elig = height = weight = rating = status = ""
            last_team = new_team = hometown = ""
            found = {k: False for k in
                     ['elig','height','weight','status','rating',
                      'last','new']}

            k = j + 1
            while k < len(values) and k - j < 35:
                vk = values[k].strip()

                # CRITICAL: stop if we hit the next player's position marker
                # This prevents scan window from consuming the next player
                if vk in POSITIONS and k > j + 3:
                    break

                if is_avatar(vk):
                    team = (vk.replace(' Avatar', '')
                              .replace('Default', '')
                              .strip())
                    if team:
                        if not found['last']:
                            last_team = team
                            found['last'] = True
                        elif not found['new']:
                            new_team = team
                            found['new'] = True
                    k += 1
                    continue

                if is_skip(vk) or not vk:
                    k += 1
                    continue

                if not found['elig'] and is_elig(vk):
                    elig = vk
                    found['elig'] = True
                elif not found['height'] and is_height(vk):
                    height = parse_height(vk)
                    found['height'] = True
                elif not found['weight'] and is_weight(vk):
                    weight = vk
                    found['weight'] = True
                elif not found['rating'] and is_rating(vk):
                    rating = vk
                    found['rating'] = True
                elif not found['status'] and is_status(vk):
                    status = vk
                    found['status'] = True
                elif re.match(r'^\(.*,\s*[A-Z]{2}\)$', vk):
                    hometown = vk.strip('()')
                elif re.match(r'^\(.*\)$', vk) and not hometown:
                    hometown = vk.strip('()')

                k += 1

                # Stop after we have the key fields
                if found['status'] and found['weight']:
                    break

            players.append({
                'Player':    name,
                'Pos':       pos,
                'Elig':      elig,
                'Height':    height,
                'Weight':    weight,
                'On3Rating': rating,
                'Status':    status or 'Expected',
                'LastTeam':  last_team,
                'NewTeam':   new_team,
                'Hometown':  hometown,
                'Source':    'On3',
            })
            i = k

        else:
            i += 1

    out = pd.DataFrame(players)

    if len(out) == 0:
        print("[WARN] No players parsed — check CSV format")
        return out

    # ── Run through merge/dedup logic (same as scraper) ──
    try:
        from scrape_on3 import merge_portal_data
        out = merge_portal_data(out.to_dict('records'), output_path)
        return out
    except ImportError:
        # scrape_on3.py not present — basic save without dedup
        out['On3Rating'] = pd.to_numeric(out['On3Rating'],
                                          errors='coerce').round(2)
        out['DateEntered'] = today
        out['LastUpdated'] = today
        out = out.sort_values(
            ['On3Rating','Player'], ascending=[False,True],
            na_position='last').reset_index(drop=True)
        out.to_csv(output_path, index=False)

        print(f"{'='*58}")
        print(f"  On3 Portal Parser  |  {today}")
        print(f"{'='*58}")
        print(f"  Players parsed:  {len(out)}")
        print(f"  With ratings:    {out['On3Rating'].notna().sum()}")
        print(f"  Committed:       {(out['Status']=='Committed').sum()}")
        print(f"  Expected:        {(out['Status']=='Expected').sum()}")
        print(f"  Withdrawn:       {(out['Status']=='Withdrawn').sum()}")
        print(f"  Saved to:        {output_path}")
        print(f"{'='*58}")
        print()
        print("Top 10 by On3 Rating:")
        top = out[out['On3Rating'].notna()].head(10)
        for _, r in top.iterrows():
            print(f"  {float(r['On3Rating']):5.2f}  {r['Player']:<25} "
                  f"{r.get('Pos',''):3} {r.get('Elig',''):6} "
                  f"{r.get('LastTeam',''):<20} "
                  f"→ {r.get('NewTeam','') or '?'}")

    return out


# ──────────────────────────────────────────────
#  MERGE WITH CBBD SCORES
# ──────────────────────────────────────────────

def merge_portal_with_scores(portal_df, scores_df):
    """
    Join portal entries with CBBD scoring data by player name.
    Returns merged DataFrame with both On3 and scoring columns.
    """
    import re as _re
    from difflib import SequenceMatcher

    def norm(n):
        n = str(n).lower().strip()
        n = _re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b', '', n)
        n = _re.sub(r'[^a-z ]', '', n)
        return _re.sub(r'\s+', ' ', n).strip()

    score_names = scores_df['Player'].tolist()
    score_norms = [norm(n) for n in score_names]

    matched_idx = []
    for pname in portal_df['Player']:
        pn = norm(pname)
        # Exact match first
        if pn in score_norms:
            matched_idx.append(score_norms.index(pn))
            continue
        # Fuzzy match
        best_score, best_i = 0, -1
        for si, sn in enumerate(score_norms):
            ratio = SequenceMatcher(None, pn, sn).ratio()
            if ratio > best_score:
                best_score = ratio
                best_i = si
        matched_idx.append(best_i if best_score >= 0.85 else -1)

    portal_df = portal_df.copy()
    score_cols = ['Player', 'Team', 'Conference', 'PTS', 'Tot', 'AST',
                  'TS%', 'eFGPct', 'PER', 'PORPAG', 'PortalScore',
                  'FinalScore', 'NILValue', 'KenPomRank']
    avail = [c for c in score_cols if c in scores_df.columns]

    matched_scores = []
    for idx in matched_idx:
        if idx >= 0:
            matched_scores.append(scores_df.iloc[idx][avail].to_dict())
        else:
            matched_scores.append({c: None for c in avail})

    score_data = pd.DataFrame(matched_scores)
    # Rename to avoid collision with portal columns
    score_data = score_data.rename(columns={
        'Player': 'CBBD_Player',
        'Team':   'CurrentTeam',
    })

    return pd.concat([portal_df.reset_index(drop=True),
                      score_data.reset_index(drop=True)], axis=1)


# ──────────────────────────────────────────────
#  RUN
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    input_file  = sys.argv[1] if len(sys.argv) > 1 else "Portalers.csv"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "portal_entries.csv"
    parse_on3_csv(input_file, output_file)
