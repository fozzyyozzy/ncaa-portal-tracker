"""
parse_on3_portal.py - On3 Transfer Portal CSV/XLSX Parser with dedup merge
"""
import pandas as pd
import numpy as np
import re
import os
from datetime import date, datetime

MONTH_TO_NUM  = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                 'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
MONTH_TO_FEET = {5:5, 6:6, 7:7}
POSITIONS = {'PG','SG','SF','PF','C','G','F','CG','WG','G/F','combo'}
ELIGS     = {'FR','SO','JR','SR','GR','RS-SO','RS-FR','RS-JR','RS-SR','5th'}
STATUSES  = {'Expected','Committed','Withdrawn','Graduate','Entered'}

def parse_height(raw):
    raw = str(raw).strip()
    # Excel full datetime: '2026-06-04 00:00:00'
    m = re.match(r'^\d{4}-(\d{2})-(\d{2})', raw)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if month in MONTH_TO_FEET and 0 <= day <= 11:
            return f"{MONTH_TO_FEET[month]}-{day}"
        return ""
    # Jun-04
    m = re.match(r'^([A-Za-z]+)-(\d+)$', raw)
    if m:
        feet = MONTH_TO_NUM.get(m.group(1), 0)
        inches = int(m.group(2))
        if 5 <= feet <= 7 and 0 <= inches <= 11:
            return f"{feet}-{inches}"
        return ""
    # 4-Jun or 11-Jun
    m = re.match(r'^(\d+)-([A-Za-z]+)$', raw)
    if m:
        a, b = int(m.group(1)), MONTH_TO_NUM.get(m.group(2), 0)
        if a <= 4: return f"{b}-{a}"
        if a <= 7: return f"{a}-{b}"
        if a >= 8: return f"{b}-{a}"
        return ""
    # plain 6-4 or inverted 8-6
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 5 <= a <= 7 and 0 <= b <= 11: return f"{a}-{b}"
        if 5 <= b <= 7 and 0 <= a <= 11: return f"{b}-{a}"
        return ""
    return ""

def is_avatar(v):  return 'Avatar' in str(v)
def is_pos(v):     return str(v).strip() in POSITIONS
def is_elig(v):    return str(v).strip() in ELIGS
def is_status(v):  return str(v).strip() in STATUSES
def is_height(v):  return bool(parse_height(v))

def is_nil(v):
    return bool(re.match(r'^\$[\d\.]+[KMBkm]?$', str(v).strip()))

def is_weight(v):
    try:
        w = int(float(str(v)))
        return 140 <= w <= 380
    except: return False

def is_rating(v):
    try:
        r = float(str(v))
        return 60.0 <= r <= 100.0
    except: return False

def is_skip(v):
    v = str(v).strip()
    # Don't skip Excel datetime heights
    if re.match(r'^\d{4}-\d{2}-\d{2}', v) and parse_height(v):
        return False
    return (is_avatar(v)
            or v.startswith('Update:')
            or v.startswith('Claim Profile')
            or re.match(r'^\d+/\d+/\d+$', v)
            or re.match(r'^\d+/\d+$', v)
            or is_nil(v)
            or v == '-'
            or len(v) > 80
            or v in ('Last Team','New Team','Status','Player',
                     'Pos','Rating','NIL Value','NIL'))

def _norm_name(name):
    n = str(name).lower().strip()
    n = re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b','',n)
    n = re.sub(r'[^a-z ]','',n)
    return re.sub(r'\s+',' ',n).strip()

def _player_key(name):
    return _norm_name(name)

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\.\s\-]+ [A-Za-z]")

def parse_on3_csv(input_path, output_path="portal_entries.csv"):
    if not os.path.exists(input_path):
        print(f"[ERROR] File not found: {input_path}")
        return pd.DataFrame()

    try:
        if input_path.lower().endswith('.xlsx'):
            raw = pd.read_excel(input_path, header=None)
        else:
            raw = pd.read_csv(input_path, encoding='utf-8-sig', header=None)
        values = raw.iloc[:,0].fillna('').astype(str).tolist()
    except Exception as e:
        print(f"[ERROR] Could not read file: {e}")
        return pd.DataFrame()

    today = str(date.today())
    now   = datetime.now().strftime('%Y-%m-%d %H:%M')
    players = []
    i = 0

    while i < len(values):
        v = values[i].strip()

        if is_skip(v) or not v or v in STATUSES:
            i += 1; continue

        if is_pos(v):
            pos = v
            # Find name
            j = i + 1
            while j < len(values) and (is_skip(values[j]) or not values[j].strip()):
                j += 1
            if j >= len(values): i += 1; continue

            name = values[j].strip()

            # Skip "Claim Profile" and other non-name junk
            if not NAME_RE.match(name) or name in STATUSES or is_skip(name):
                i += 1; continue

            # Scan forward for attributes — stop at next position marker
            elig=height=weight=rating=status=last_team=new_team=hometown=""
            found = {k:False for k in ['elig','height','weight','status',
                                        'rating','last','new']}
            k = j + 1
            while k < len(values) and k - j < 35:
                vk = values[k].strip()
                # Stop at next player's position marker
                if vk in POSITIONS and k > j + 3:
                    break
                if is_avatar(vk):
                    team = vk.replace(' Avatar','').replace('Default','').strip()
                    if team:
                        if not found['last']:
                            last_team = team; found['last'] = True
                        elif not found['new']:
                            new_team  = team; found['new']  = True
                    k += 1; continue
                if is_skip(vk) or not vk:
                    k += 1; continue
                if not found['elig']   and is_elig(vk):   elig   = vk; found['elig']   = True
                elif not found['height'] and is_height(vk): height = parse_height(vk); found['height'] = True
                elif not found['weight'] and is_weight(vk): weight = vk; found['weight'] = True
                elif not found['rating'] and is_rating(vk): rating = vk; found['rating'] = True
                elif not found['status'] and is_status(vk): status = vk; found['status'] = True
                elif re.match(r'^\(.*,.*\)$', vk) and not hometown:
                    hometown = vk.strip('()')
                k += 1
                if found['status'] and found['weight']: break

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

    new_df = pd.DataFrame(players)
    if len(new_df) == 0:
        print("[WARN] No players parsed")
        return new_df

    # ── Merge with existing ──
    return _merge(new_df.to_dict('records'), output_path, today, now)


def _merge(new_players, output_path, today, now):
    if os.path.exists(output_path):
        try:
            existing = pd.read_csv(output_path, encoding='utf-8-sig', dtype=str).fillna('')
            for col in ['DateEntered','LastUpdated','UniqueKey']:
                if col not in existing.columns:
                    if col == 'UniqueKey':
                        existing[col] = existing['Player'].apply(_player_key)
                    else:
                        existing[col] = today
        except Exception:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    lookup = {}
    if len(existing) > 0 and 'UniqueKey' in existing.columns:
        lookup = {r['UniqueKey']: idx for idx, r in existing.iterrows()}

    inserted = updated = unchanged = 0
    new_rows = []

    for p in new_players:
        if not p.get('Player'): continue
        key = _player_key(p['Player'])

        if key in lookup:
            idx = lookup[key]
            changed = False
            update_fields = {
                'Status':    p.get('Status',''),
                'On3Rating': p.get('On3Rating',''),
                'Pos':       p.get('Pos','') or existing.at[idx,'Pos'],
                'Elig':      p.get('Elig','') or existing.at[idx,'Elig'],
                'Height':    p.get('Height','') or existing.at[idx,'Height'],
                'Weight':    p.get('Weight','') or existing.at[idx,'Weight'],
                'LastTeam':  p.get('LastTeam','') or existing.at[idx,'LastTeam'],
            }
            # NewTeam: fill in or update if changed
            new_dest = str(p.get('NewTeam','')).strip()
            old_dest  = str(existing.at[idx,'NewTeam']).strip()
            if new_dest and new_dest != old_dest:
                update_fields['NewTeam'] = new_dest

            for field, val in update_fields.items():
                if field not in existing.columns: continue
                old = str(existing.at[idx, field]).strip()
                new = str(val).strip()
                if new and new != old:
                    existing.at[idx, field] = new
                    changed = True
            if changed:
                existing.at[idx,'LastUpdated'] = now
                updated += 1
            else:
                unchanged += 1
        else:
            new_rows.append({
                'Player':    p.get('Player',''),
                'Pos':       p.get('Pos',''),
                'Elig':      p.get('Elig',''),
                'Height':    p.get('Height',''),
                'Weight':    p.get('Weight',''),
                'On3Rating': p.get('On3Rating',''),
                'Status':    p.get('Status','Expected'),
                'LastTeam':  p.get('LastTeam',''),
                'NewTeam':   p.get('NewTeam',''),
                'Hometown':  p.get('Hometown',''),
                'Source':    'On3',
                'DateEntered': today,
                'LastUpdated': now,
                'UniqueKey':   key,
            })
            inserted += 1

    if new_rows:
        existing = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)

    # Dedup safety — keep highest rating per player
    if len(existing) > 0:
        existing['_r'] = pd.to_numeric(existing['On3Rating'], errors='coerce').fillna(0)
        existing = existing.sort_values('_r', ascending=False)
        existing = existing.drop_duplicates(subset='Player', keep='first')
        existing = existing.drop(columns=['_r'])

    # Sort
    existing['_s'] = pd.to_numeric(existing['On3Rating'], errors='coerce')
    existing = existing.sort_values('_s', ascending=False, na_position='last')
    existing = existing.drop(columns=['_s']).reset_index(drop=True)
    existing.to_csv(output_path, index=False)

    print(f"\n{'='*52}")
    print(f"  Portal Merge  |  {now}")
    print(f"{'='*52}")
    print(f"  Parsed this run:  {len(new_players)}")
    print(f"  Inserted (new):   {inserted}")
    print(f"  Updated:          {updated}")
    print(f"  Unchanged:        {unchanged}")
    print(f"  Total in CSV:     {len(existing)}")
    print(f"{'='*52}")
    return existing


if __name__ == "__main__":
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else "Portalers.csv"
    out = sys.argv[2] if len(sys.argv) > 2 else "portal_entries.csv"
    parse_on3_csv(inp, out)
