import pandas as pd, re, os
from datetime import date, datetime

MONTH_TO_NUM  = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                 'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
MONTH_TO_FEET = {5:5,6:6,7:7}
POSITIONS = {'PG','SG','SF','PF','C','G','F','CG','WG','G/F','combo'}
ELIGS     = {'FR','SO','JR','SR','GR','RS-SO','RS-FR','RS-JR','RS-SR','5th'}
STATUSES  = {'Expected','Committed','Withdrawn','Graduate','Entered'}
NAME_RE   = re.compile(r"^[A-Za-z][A-Za-z'\.\s\-]+ [A-Za-z]")

def parse_height(raw):
    raw = str(raw).strip()
    m = re.match(r'^\d{4}-(\d{2})-(\d{2})', raw)
    if m:
        mo,day = int(m.group(1)),int(m.group(2))
        if mo in MONTH_TO_FEET and 0<=day<=11: return f"{MONTH_TO_FEET[mo]}-{day}"
        return ""
    m = re.match(r'^([A-Za-z]+)-(\d+)$', raw)
    if m:
        ft=MONTH_TO_NUM.get(m.group(1),0); ins=int(m.group(2))
        return f"{ft}-{ins}" if 5<=ft<=7 and 0<=ins<=11 else ""
    m = re.match(r'^(\d+)-([A-Za-z]+)$', raw)
    if m:
        a,b=int(m.group(1)),MONTH_TO_NUM.get(m.group(2),0)
        if a<=4: return f"{b}-{a}"
        if a<=7: return f"{a}-{b}"
        if a>=8: return f"{b}-{a}"
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        a,b=int(m.group(1)),int(m.group(2))
        if 5<=a<=7 and 0<=b<=11: return f"{a}-{b}"
        if 5<=b<=7 and 0<=a<=11: return f"{b}-{a}"
    return ""

def is_avatar(v):  return 'Avatar' in str(v)
def is_height(v):  return bool(parse_height(v))
def is_weight(v):
    try: w=int(float(str(v))); return 140<=w<=380
    except: return False
def is_rating(v):
    try: r=float(str(v)); return 60<=r<=100
    except: return False
def is_nil(v):     return bool(re.match(r'^\$[\d\.]+[KMBkm]?$',str(v).strip()))
def is_skip(v):
    v=str(v).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}',v) and parse_height(v): return False
    return (is_avatar(v) or v.startswith('Update:') or v.startswith('Claim Profile')
            or re.match(r'^\d+/\d+/\d+$',v) or re.match(r'^\d+/\d+$',v)
            or is_nil(v) or v=='-' or len(v)>80
            or v in ('Last Team','New Team','Status','Player','Pos','Rating','NIL Value','NIL'))

def _norm(n):
    n=str(n).lower().strip()
    n=re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b','',n)
    n=re.sub(r'[^a-z ]','',n)
    return re.sub(r'\s+',' ',n).strip()

def parse_file(input_path):
    if input_path.lower().endswith('.xlsx'):
        raw = pd.read_excel(input_path, header=None)
    else:
        raw = pd.read_csv(input_path, encoding='utf-8-sig', header=None)
    values = raw.iloc[:,0].fillna('').astype(str).tolist()
    players=[]; i=0
    while i < len(values):
        v=values[i].strip()
        if is_skip(v) or not v or v in STATUSES: i+=1; continue
        if v in POSITIONS:
            pos=v; j=i+1
            while j<len(values) and (is_skip(values[j]) or not values[j].strip()): j+=1
            if j>=len(values): i+=1; continue
            name=values[j].strip()
            if not NAME_RE.match(name) or name in STATUSES or is_skip(name): i+=1; continue
            elig=height=weight=rating=status=last_team=new_team=hometown=""
            found={k:False for k in ['elig','height','weight','status','rating','last','new']}
            k=j+1
            while k<len(values) and k-j<35:
                vk=values[k].strip()
                if vk in POSITIONS and k>j+3: break
                if is_avatar(vk):
                    team=vk.replace(' Avatar','').replace('Default','').strip()
                    if team:
                        if not found['last']: last_team=team; found['last']=True
                        elif not found['new']: new_team=team; found['new']=True
                    k+=1; continue
                if is_skip(vk) or not vk: k+=1; continue
                if   not found['elig']   and vk in ELIGS:      elig=vk;            found['elig']=True
                elif not found['height'] and is_height(vk):    height=parse_height(vk); found['height']=True
                elif not found['weight'] and is_weight(vk):    weight=vk;          found['weight']=True
                elif not found['rating'] and is_rating(vk):    rating=vk;          found['rating']=True
                elif not found['status'] and vk in STATUSES:   status=vk;          found['status']=True
                elif re.match(r'^\(.*,.*\)$',vk) and not hometown: hometown=vk.strip('()')
                k+=1
                if found['status'] and found['weight']: break
            players.append({'Player':name,'Pos':pos,'Elig':elig,'Height':height,
                            'Weight':weight,'On3Rating':rating,'Status':status or 'Expected',
                            'LastTeam':last_team,'NewTeam':new_team,'Hometown':hometown,'Source':'On3'})
            i=k
        else: i+=1
    return players

def merge(new_players, output_path="portal_entries.csv"):
    today=str(date.today()); now=datetime.now().strftime('%Y-%m-%d %H:%M')
    if os.path.exists(output_path):
        try:
            ex=pd.read_csv(output_path,encoding='utf-8-sig',dtype=str).fillna('')
            if 'UniqueKey' not in ex.columns:
                ex['UniqueKey']=ex['Player'].apply(_norm)
            if 'DateEntered' not in ex.columns: ex['DateEntered']=today
            if 'LastUpdated' not in ex.columns: ex['LastUpdated']=today
        except: ex=pd.DataFrame()
    else: ex=pd.DataFrame()

    lookup={r['UniqueKey']:idx for idx,r in ex.iterrows()} if len(ex) and 'UniqueKey' in ex.columns else {}
    ins=upd=unch=0; new_rows=[]

    for p in new_players:
        if not p.get('Player'): continue
        key=_norm(p['Player'])
        if key in lookup:
            idx=lookup[key]; changed=False
            fields={'Status':p.get('Status',''),'On3Rating':p.get('On3Rating',''),
                    'Pos':p.get('Pos','') or ex.at[idx,'Pos'],
                    'Elig':p.get('Elig','') or ex.at[idx,'Elig'],
                    'Height':p.get('Height','') or ex.at[idx,'Height'],
                    'Weight':p.get('Weight','') or ex.at[idx,'Weight'],
                    'LastTeam':p.get('LastTeam','') or ex.at[idx,'LastTeam']}
            nd=str(p.get('NewTeam','')).strip(); od=str(ex.at[idx,'NewTeam']).strip()
            if nd and nd!=od: fields['NewTeam']=nd
            for f,val in fields.items():
                if f not in ex.columns: continue
                if str(val).strip() and str(val).strip()!=str(ex.at[idx,f]).strip():
                    ex.at[idx,f]=str(val).strip(); changed=True
            if changed: ex.at[idx,'LastUpdated']=now; upd+=1
            else: unch+=1
        else:
            new_rows.append({'Player':p.get('Player',''),'Pos':p.get('Pos',''),
                'Elig':p.get('Elig',''),'Height':p.get('Height',''),
                'Weight':p.get('Weight',''),'On3Rating':p.get('On3Rating',''),
                'Status':p.get('Status','Expected'),'LastTeam':p.get('LastTeam',''),
                'NewTeam':p.get('NewTeam',''),'Hometown':p.get('Hometown',''),
                'Source':'On3','DateEntered':today,'LastUpdated':now,'UniqueKey':key})
            ins+=1

    if new_rows: ex=pd.concat([ex,pd.DataFrame(new_rows)],ignore_index=True)

    # Dedup — keep highest rating
    ex['_r']=pd.to_numeric(ex['On3Rating'],errors='coerce').fillna(0)
    ex=ex.sort_values('_r',ascending=False).drop_duplicates(subset='Player',keep='first').drop(columns=['_r'])
    ex['_s']=pd.to_numeric(ex['On3Rating'],errors='coerce')
    ex=ex.sort_values('_s',ascending=False,na_position='last').drop(columns=['_s']).reset_index(drop=True)
    ex.to_csv(output_path,index=False)

    print(f"\n{'='*50}")
    print(f"  Portal Merge  |  {now}")
    print(f"{'='*50}")
    print(f"  Parsed:    {len(new_players)}")
    print(f"  New:       {ins}")
    print(f"  Updated:   {upd}")
    print(f"  Unchanged: {unch}")
    print(f"  Total:     {len(ex)}")
    print(f"{'='*50}")
    return ex

def parse_on3_csv(input_path, output_path="portal_entries.csv"):
    if not os.path.exists(input_path):
        print(f"[ERROR] Not found: {input_path}"); return pd.DataFrame()
    players=parse_file(input_path)
    if not players:
        print("[WARN] No players parsed"); return pd.DataFrame()
    return merge(players, output_path)

if __name__=="__main__":
    import sys
    inp=sys.argv[1] if len(sys.argv)>1 else "Portalers.csv"
    out=sys.argv[2] if len(sys.argv)>2 else "portal_entries.csv"
    parse_on3_csv(inp,out)
