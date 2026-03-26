"""
=============================================================
  scrape_on3.py  |  v1.0
  Playwright-based On3 Transfer Portal scraper
  Runs twice daily via GitHub Actions or Windows Task Scheduler
=============================================================

  INSTALL (one time):
    pip install playwright
    playwright install chromium

  RUN MANUALLY:
    python scrape_on3.py

  OUTPUT:
    portal_entries.csv  — merged, deduplicated, one row per player
=============================================================
"""

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime

import pandas as pd
from playwright.async_api import async_playwright

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────

ON3_URL      = "https://www.on3.com/transfer-portal/industry/basketball/2026/"
OUTPUT_FILE  = "portal_entries.csv"
SCROLL_PAUSE = 1.5    # seconds between scrolls
MAX_SCROLLS  = 80     # safety cap (~160 players per scroll load)
HEADLESS     = True   # set False to watch the browser

# ──────────────────────────────────────────────
#  HEIGHT PARSER  (Excel date-mangling fix)
# ──────────────────────────────────────────────

MONTH_TO_NUM = {
    'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
    'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12
}

def parse_height(raw):
    raw = str(raw).strip()
    m = re.match(r'^([A-Za-z]+)-(\d+)$', raw)
    if m:
        feet = MONTH_TO_NUM.get(m.group(1), 0)
        inches = int(m.group(2))
        if 5 <= feet <= 7 and 0 <= inches <= 11:
            return f"{feet}-{inches}"
        return ""
    m = re.match(r'^(\d+)-([A-Za-z]+)$', raw)
    if m:
        a = int(m.group(1))
        b = MONTH_TO_NUM.get(m.group(2), 0)
        if a <= 4:  return f"{b}-{a}"
        if a <= 7:  return f"{a}-{b}"
        if a >= 8:  return f"{b}-{a}"
        return ""
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 5 <= a <= 7 and 0 <= b <= 11: return f"{a}-{b}"
        if 5 <= b <= 7 and 0 <= a <= 11: return f"{b}-{a}"
        return ""
    return ""


# ──────────────────────────────────────────────
#  SCRAPER
# ──────────────────────────────────────────────

async def scrape_on3():
    """
    Launch Playwright, scroll through On3 portal, extract player data.
    Returns list of raw player dicts.
    """
    players = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        print(f"  Opening {ON3_URL}...")
        await page.goto(ON3_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # Scroll to load all players
        print("  Scrolling to load all portal entries...")
        prev_height = 0
        scroll_count = 0
        while scroll_count < MAX_SCROLLS:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_PAUSE)
            curr_height = await page.evaluate("document.body.scrollHeight")
            scroll_count += 1
            if curr_height == prev_height:
                print(f"  Reached bottom after {scroll_count} scrolls")
                break
            prev_height = curr_height
            if scroll_count % 10 == 0:
                print(f"  ... {scroll_count} scrolls")

        # Extract player rows
        print("  Extracting player data...")
        players = await _extract_players(page)
        await browser.close()

    print(f"  Scraped {len(players)} players from On3")
    return players


async def _extract_players(page):
    """Extract structured player data from the rendered page."""
    players = []

    # Try to get player cards via On3's data attributes / class names
    # On3 uses React so we grab the rendered DOM
    rows = await page.query_selector_all(
        "[class*='TransferPortalPlayer'], "
        "[class*='transfer-portal-player'], "
        "[class*='PlayerCard'], "
        "[class*='player-row']"
    )

    if rows:
        print(f"  Found {len(rows)} player card elements")
        for row in rows:
            player = await _parse_player_card(row)
            if player:
                players.append(player)
    else:
        # Fallback: grab all text and use our CSV parser logic
        print("  Card selector failed — falling back to text parse")
        content = await page.inner_text("body")
        players = _parse_text_content(content)

    return players


async def _parse_player_card(element):
    """Extract fields from a single player card element."""
    try:
        text = await element.inner_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) < 3:
            return None

        POSITIONS = {'PG','SG','SF','PF','C','G','F','CG','WG','G/F'}
        STATUSES  = {'Expected','Committed','Withdrawn','Graduate'}
        ELIGS     = {'FR','SO','JR','SR','GR','RS-SO','RS-FR',
                     'RS-JR','RS-SR','5th'}

        player = {
            'Player': '', 'Pos': '', 'Elig': '',
            'Height': '', 'Weight': '', 'On3Rating': '',
            'Status': 'Expected', 'LastTeam': '', 'NewTeam': '',
            'Hometown': '',
        }

        for line in lines:
            if not player['Pos'] and line in POSITIONS:
                player['Pos'] = line
            elif not player['Player'] and re.match(
                    r'^[A-Za-z][A-Za-z\'\.\s\-]+ [A-Za-z]', line):
                player['Player'] = line
            elif line in ELIGS:
                player['Elig'] = line
            elif not player['Height'] and parse_height(line):
                player['Height'] = parse_height(line)
            elif not player['Weight']:
                try:
                    w = int(float(line))
                    if 140 <= w <= 380:
                        player['Weight'] = str(w)
                except Exception:
                    pass
            elif not player['On3Rating']:
                try:
                    r = float(line)
                    if 60 <= r <= 100:
                        player['On3Rating'] = str(round(r, 2))
                except Exception:
                    pass
            elif line in STATUSES:
                player['Status'] = line
            elif re.match(r'^\(.*,.*\)$', line):
                player['Hometown'] = line.strip('()')

        return player if player['Player'] else None
    except Exception:
        return None


def _parse_text_content(text):
    """
    Fallback parser — same logic as parse_on3_portal.py but
    operates on raw page text instead of CSV.
    """
    from parse_on3_portal import parse_on3_csv
    import tempfile

    # Write to temp CSV and reuse our battle-tested parser
    lines = text.split('\n')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                     delete=False, encoding='utf-8') as f:
        f.write("Status\n")
        for line in lines:
            clean = line.strip().replace('"', '""')
            if clean:
                f.write(f'"{clean}"\n')
        tmp_path = f.name

    result_path = tmp_path.replace('.csv', '_out.csv')
    try:
        df = parse_on3_csv(tmp_path, result_path)
        return df.to_dict('records') if len(df) > 0 else []
    except Exception as e:
        print(f"  [WARN] Fallback parser error: {e}")
        return []
    finally:
        for p in [tmp_path, result_path]:
            if os.path.exists(p):
                os.remove(p)


# ──────────────────────────────────────────────
#  DEDUPLICATION & MERGE
# ──────────────────────────────────────────────

def _player_key(player_name, last_team=""):
    """
    Unique key = normalized player name only.
    One row per player forever — LastTeam and NewTeam
    are fields on that row, not part of the key.
    """
    import re as _re
    name = str(player_name).lower().strip()
    name = _re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b', '', name)
    name = _re.sub(r'[^a-z ]', '', name)
    return _re.sub(r'\s+', ' ', name).strip()


def merge_portal_data(new_players, output_path=OUTPUT_FILE):
    """
    Merge newly scraped players into the existing portal_entries.csv.

    Rules:
    - One row per (player_name, last_team) combination
    - If player+team already exists: UPDATE status, new_team, rating, last_updated
    - If genuinely new: INSERT new row with DateEntered = today
    - DateEntered is NEVER overwritten once set
    - Withdrawn players are kept but marked Withdrawn
    """
    today = str(date.today())
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Load existing data ──
    if os.path.exists(output_path):
        try:
            existing = pd.read_csv(output_path, encoding="utf-8-sig",
                                   dtype=str)   # load ALL as strings
            existing = existing.fillna("")
            for col in ['DateEntered','LastUpdated','UniqueKey']:
                if col not in existing.columns:
                    if col == 'DateEntered':
                        existing[col] = today
                    elif col == 'LastUpdated':
                        existing[col] = today
                    else:
                        existing[col] = existing.apply(
                            lambda r: _player_key(
                                r.get('Player',''),
                                r.get('LastTeam','')), axis=1)
        except Exception:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    # Build lookup from existing data
    if len(existing) > 0 and 'UniqueKey' in existing.columns:
        existing_lookup = {row['UniqueKey']: idx
                           for idx, row in existing.iterrows()}
    else:
        existing_lookup = {}

    new_rows    = []
    updated     = 0
    inserted    = 0
    unchanged   = 0

    for p in new_players:
        if not p.get('Player'):
            continue

        key = _player_key(p.get('Player',''), p.get('LastTeam',''))

        if key in existing_lookup:
            # ── UPDATE existing row ──
            idx = existing_lookup[key]
            changed = False

            # These fields update freely
            update_fields = {
                'Status':    p.get('Status',''),
                'On3Rating': p.get('On3Rating',''),
                'Pos':       p.get('Pos','') or existing.at[idx,'Pos'],
                'Elig':      p.get('Elig','') or existing.at[idx,'Elig'],
                'Height':    p.get('Height','') or existing.at[idx,'Height'],
                'Weight':    p.get('Weight','') or existing.at[idx,'Weight'],
            }

            # LastTeam: update if we now have one and didn't before
            new_last = str(p.get('LastTeam','')).strip()
            old_last  = str(existing.at[idx,'LastTeam']).strip()
            if new_last and new_last != old_last:
                update_fields['LastTeam'] = new_last

            # NewTeam: only fill in — never erase a committed destination
            new_dest = str(p.get('NewTeam','')).strip()
            old_dest  = str(existing.at[idx,'NewTeam']).strip()
            if new_dest and not old_dest:
                update_fields['NewTeam'] = new_dest
            elif new_dest and new_dest != old_dest:
                # Destination changed (e.g. decommit + recommit)
                update_fields['NewTeam'] = new_dest

            for field, new_val in update_fields.items():
                if field not in existing.columns:
                    continue
                old_val     = str(existing.at[idx, field]).strip()
                new_val_str = str(new_val).strip()
                if new_val_str and new_val_str != old_val:
                    existing.at[idx, field] = new_val_str
                    changed = True

            if changed:
                existing.at[idx, 'LastUpdated'] = now
                updated += 1
            else:
                unchanged += 1

        else:
            # ── INSERT new row ──
            new_row = {
                'Player':      p.get('Player', ''),
                'Pos':         p.get('Pos', ''),
                'Elig':        p.get('Elig', ''),
                'Height':      p.get('Height', ''),
                'Weight':      p.get('Weight', ''),
                'On3Rating':   p.get('On3Rating', ''),
                'Status':      p.get('Status', 'Expected'),
                'LastTeam':    p.get('LastTeam', ''),
                'NewTeam':     p.get('NewTeam', ''),
                'Hometown':    p.get('Hometown', ''),
                'Source':      'On3',
                'DateEntered': today,
                'LastUpdated': now,
                'UniqueKey':   key,
            }
            new_rows.append(new_row)
            inserted += 1

    # Append new rows
    if new_rows:
        new_df   = pd.DataFrame(new_rows)
        existing = pd.concat([existing, new_df],
                              ignore_index=True)

    # Final dedup safety net — keep highest rating per player name
    if len(existing) > 0:
        existing['_rating_num'] = pd.to_numeric(
            existing['On3Rating'], errors='coerce').fillna(0)
        existing = existing.sort_values('_rating_num', ascending=False)
        existing = existing.drop_duplicates(subset='Player', keep='first')
        existing = existing.drop(columns=['_rating_num'])

    # Sort by On3Rating desc
    if 'On3Rating' in existing.columns:
        existing['On3Rating'] = pd.to_numeric(
            existing['On3Rating'], errors='coerce')
        existing = existing.sort_values(
            'On3Rating', ascending=False, na_position='last')

    existing.to_csv(output_path, index=False)

    print(f"\n{'='*55}")
    print(f"  Portal Merge Summary  |  {now}")
    print(f"{'='*55}")
    print(f"  Scraped this run:  {len(new_players)}")
    print(f"  Inserted (new):    {inserted}")
    print(f"  Updated:           {updated}")
    print(f"  Unchanged:         {unchanged}")
    print(f"  Total in CSV:      {len(existing)}")
    print(f"  Saved to:          {output_path}")
    print(f"{'='*55}")

    return existing


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────

async def main():
    print("="*55)
    print("  On3 Transfer Portal Scraper  |  v1.0")
    print(f"  {datetime.now().strftime('%B %d, %Y  %H:%M')}")
    print("="*55)
    print()

    # Scrape
    players = await scrape_on3()

    if not players:
        print("[ERROR] No players scraped — check On3 page structure")
        sys.exit(1)

    # Merge with deduplication
    merge_portal_data(players, OUTPUT_FILE)


if __name__ == "__main__":
    asyncio.run(main())
