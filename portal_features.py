"""
=============================================================
  portal_features.py  |  v1.0
  Three add-on feature tabs for portal_gui.py

  Tab A: 🚪 Portal Entries   — On3 scraper + CSV upload
  Tab B: 🏫 Team Roster      — Power rating, top 8 rotation
  Tab C: 🔀 Destination      — Roster upgrade tracker

  HOW TO ADD TO portal_gui.py:
    1. Add at top of portal_gui.py:
         from portal_features import PortalEntriesTab, TeamRosterTab, DestinationTab
    2. In _build_content(), after existing notebook.add() calls, add:
         self.tab_entries = tk.Frame(self.notebook, bg=BG)
         self.tab_roster  = tk.Frame(self.notebook, bg=BG)
         self.tab_dest    = tk.Frame(self.notebook, bg=BG)
         self.notebook.add(self.tab_entries, text="  🚪 Portal Entries  ")
         self.notebook.add(self.tab_roster,  text="  📋 Team Roster  ")
         self.notebook.add(self.tab_dest,    text="  🔀 Destinations  ")
         self.entries_tab = PortalEntriesTab(self.tab_entries, self)
         self.roster_tab  = TeamRosterTab(self.tab_roster,  self)
         self.dest_tab    = DestinationTab(self.tab_dest,   self)
    3. At end of _post_load(), add:
         self.entries_tab.on_data_loaded()
         self.roster_tab.on_data_loaded()
         self.dest_tab.on_data_loaded()
=============================================================
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import json
import re
from datetime import datetime
from difflib import SequenceMatcher

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pass

# ── Colours (match portal_gui.py) ──
BG      = "#0d1117"
PANEL   = "#161b22"
ACCENT  = "#238636"
ACCENT2 = "#1f6feb"
ACCENT3 = "#8957e5"
TEXT    = "#e6edf3"
SUBTEXT = "#8b949e"
BORDER  = "#30363d"
GOLD    = "#d29922"
RED     = "#da3633"
ORANGE  = "#e3b341"

TIER_COLOURS = {
    "ELITE":  "#58a6ff",
    "HIGH":   "#3fb950",
    "SOLID":  "#d29922",
    "FRINGE": "#e3b341",
    "DEPTH":  "#8b949e",
}

PORTAL_ENTRIES_FILE = "portal_entries.json"

# ──────────────────────────────────────────────
#  SHARED HELPERS
# ──────────────────────────────────────────────

def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r"\b(jr\.?|sr\.?|ii|iii|iv)\b", "", name)
    name = re.sub(r"[^a-z ]", "", name)
    return re.sub(r"\s+", " ", name).strip()

def fuzzy_match(name_a, name_b):
    return SequenceMatcher(None,
        normalize_name(name_a),
        normalize_name(name_b)).ratio()

def find_player_in_df(name, df, threshold=0.82):
    """Find best matching player row in CBBD dataframe."""
    if df is None or len(df) == 0:
        return None
    best_score = 0
    best_row   = None
    for _, row in df.iterrows():
        score = fuzzy_match(name, str(row.get("Player", "")))
        if score > best_score:
            best_score = score
            best_row   = row
    return best_row if best_score >= threshold else None

def tier_for_score(score):
    if score >= 85: return "ELITE"
    if score >= 70: return "HIGH"
    if score >= 55: return "SOLID"
    if score >= 40: return "FRINGE"
    return "DEPTH"

def make_treeview(parent, cols, widths, stretch_col=None, height=None):
    """Helper to build a styled Treeview with scrollbar."""
    frame = tk.Frame(parent, bg=BG)
    frame.pack(fill="both", expand=True)
    kw = {"columns": cols, "show": "headings", "style": "Portal.Treeview"}
    if height:
        kw["height"] = height
    tree = ttk.Treeview(frame, **kw)
    for col in cols:
        tree.heading(col, text=col)
        tree.column(col, width=widths.get(col, 100),
                    anchor="center" if col != stretch_col else "w",
                    stretch=(col == stretch_col))
    for tier, colour in TIER_COLOURS.items():
        tree.tag_configure(tier, foreground=colour)
    tree.tag_configure("GAIN",  foreground=ACCENT)
    tree.tag_configure("LOSS",  foreground=RED)
    tree.tag_configure("NEUT",  foreground=SUBTEXT)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    return tree

def section_label(parent, text):
    tk.Label(parent, text=text, bg=PANEL, fg=SUBTEXT,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(8,2))

def accent_btn(parent, text, cmd, color=None, side="top"):
    tk.Button(parent, text=text, bg=color or ACCENT, fg=TEXT,
              activebackground=color or ACCENT, activeforeground=TEXT,
              relief="flat", cursor="hand2",
              font=("Segoe UI", 9, "bold"),
              padx=8, pady=5, command=cmd).pack(
                  fill="x", padx=12, pady=3, side=side)

# ──────────────────────────────────────────────
#  ON3 PORTAL SCRAPER
#  On3's wire is JS-rendered so we fall back to
#  their public /transfer-portal/wire/ RSS/JSON
#  feed if available, otherwise guide user to
#  CSV upload. We try the undocumented API first.
# ──────────────────────────────────────────────

ON3_API_URLS = [
    "https://on3.com/api/v1_0/transfer-portal/players/?sport=basketball&year=2026&limit=500",
    "https://www.on3.com/transfer-portal/wire/basketball/2026/",
]

def scrape_on3_portal():
    """
    Attempt to pull On3 portal data.
    Returns list of dicts: {player, from_team, to_team, position,
                             elig, entered_date, status, on3_rating}
    """
    try:
        import requests
    except ImportError:
        return None, "requests not installed — run: pip install requests"

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
        "Accept": "application/json, text/html, */*",
        "Referer": "https://www.on3.com/",
    }

    # Try undocumented JSON endpoints
    json_endpoints = [
        "https://on3.com/api/v1_0/transfer-portal/?sport=basketball&year=2026&limit=500&offset=0",
        "https://www.on3.com/_next/data/transfer-portal/wire/basketball/2026.json",
    ]

    for url in json_endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200 and "application/json" in r.headers.get("Content-Type",""):
                data = r.json()
                players = _parse_on3_json(data)
                if players:
                    return players, f"Fetched {len(players)} entries from On3"
        except Exception:
            continue

    # If JSON fails, return helpful message
    return [], ("On3 uses JavaScript rendering — automatic scraping is blocked.\n"
                "Use 'Upload Portal CSV' to import entries manually.\n"
                "See instructions below for the CSV format.")


def _parse_on3_json(data):
    """Parse On3 API JSON response into standardized list."""
    players = []
    # Try common response shapes
    items = (data.get("results") or data.get("players") or
             data.get("data", {}).get("players") or
             (data if isinstance(data, list) else []))
    for item in items:
        try:
            players.append({
                "player":       (item.get("name") or item.get("fullName") or
                                 item.get("athleteName") or ""),
                "from_team":    (item.get("previousSchool") or
                                 item.get("fromSchool") or ""),
                "to_team":      (item.get("committedSchool") or
                                 item.get("toSchool") or ""),
                "position":     item.get("position") or "",
                "elig":         item.get("year") or item.get("eligibility") or "",
                "entered_date": item.get("enteredDate") or item.get("date") or "",
                "status":       item.get("status") or "In Portal",
                "on3_rating":   item.get("rating") or item.get("on3Rating") or 0,
            })
        except Exception:
            continue
    return players


def load_portal_entries_from_csv(path):
    """
    Load portal entries from a user-supplied CSV.
    Expected columns (flexible): Player, From, To, Position, Elig,
                                  Date, Status
    """
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]

        col_map = {
            "player":       ["Player", "Name", "Athlete"],
            "from_team":    ["From", "Previous School", "Old Team", "PreviousSchool"],
            "to_team":      ["To", "Destination", "New Team", "Committed", "NewTeam"],
            "position":     ["Position", "Pos"],
            "elig":         ["Elig", "Eligibility", "Year", "Class"],
            "entered_date": ["Date", "Entered", "EnteredDate"],
            "status":       ["Status"],
            "on3_rating":   ["Rating", "On3Rating", "On3"],
        }

        records = []
        for _, row in df.iterrows():
            rec = {}
            for key, candidates in col_map.items():
                for c in candidates:
                    if c in df.columns:
                        rec[key] = str(row[c]).strip() if pd.notna(row[c]) else ""
                        break
                if key not in rec:
                    rec[key] = ""
            if rec.get("player"):
                records.append(rec)
        return records, f"Loaded {len(records)} entries from CSV"
    except Exception as e:
        return [], f"CSV load error: {e}"


def save_portal_entries(entries):
    with open(PORTAL_ENTRIES_FILE, "w") as f:
        json.dump(entries, f, indent=2)

def load_portal_entries():
    if os.path.exists(PORTAL_ENTRIES_FILE):
        with open(PORTAL_ENTRIES_FILE) as f:
            return json.load(f)
    return []


# ══════════════════════════════════════════════
#  TAB A: 🚪 PORTAL ENTRIES
# ══════════════════════════════════════════════

class PortalEntriesTab:
    """
    Shows all players who have entered the portal,
    matched to their CBBD Portal Score where available.
    """

    def __init__(self, parent, app):
        self.app     = app       # reference to main PortalTrackerApp
        self.parent  = parent
        self.entries = load_portal_entries()   # list of dicts
        self._build()

    def _build(self):
        # ── control bar ──
        ctrl = tk.Frame(self.parent, bg=PANEL, height=48)
        ctrl.pack(fill="x", pady=(0, 4))
        ctrl.pack_propagate(False)

        accent_btn(ctrl, "⟳ Scrape On3", self._scrape, color=ACCENT2, side="left")
        accent_btn(ctrl, "📂 Upload CSV", self._upload_csv, color=ACCENT3, side="left")
        accent_btn(ctrl, "➕ Add Player", self._add_player_dialog, color=GOLD, side="left")
        accent_btn(ctrl, "🗑 Clear All", self._clear_all, color=RED, side="left")

        # status
        self.status_var = tk.StringVar(value=f"{len(self.entries)} entries loaded")
        tk.Label(ctrl, textvariable=self.status_var, bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 8)).pack(side="left", padx=12)

        # ── filters ──
        fbar = tk.Frame(self.parent, bg=BG)
        fbar.pack(fill="x", pady=2)

        tk.Label(fbar, text="Status:", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(8,2))
        self.status_filter = tk.StringVar(value="All")
        status_combo = ttk.Combobox(fbar, textvariable=self.status_filter,
                                     values=["All", "In Portal", "Committed",
                                             "Withdrawn"],
                                     state="readonly", width=12,
                                     font=("Segoe UI", 9))
        status_combo.pack(side="left", padx=4)
        status_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh())

        tk.Label(fbar, text="Search:", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(12,2))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh())
        tk.Entry(fbar, textvariable=self.search_var, bg=BORDER, fg=TEXT,
                 insertbackground=TEXT, relief="flat",
                 font=("Segoe UI", 9), width=20).pack(side="left", padx=4)

        # ── treeview ──
        cols = ("Player", "From", "To", "Pos", "Elig",
                "Entered", "Status", "PortalScore", "Tier")
        widths = {
            "Player": 180, "From": 150, "To": 150,
            "Pos": 45, "Elig": 45, "Entered": 90,
            "Status": 90, "PortalScore": 90, "Tier": 65,
        }
        self.tree = make_treeview(self.parent, cols, widths, stretch_col="Player")

        # CSV format hint
        hint = ("CSV format: Player, From, To, Position, Elig, Date, Status  "
                "— columns are flexible, partial matches accepted")
        tk.Label(self.parent, text=hint, bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 7), anchor="w").pack(fill="x", padx=8)

        self._refresh()

    def on_data_loaded(self):
        """Called by app after CBBD data loads — re-match scores."""
        self._refresh()

    def _get_portal_score(self, player_name, from_team):
        """Look up Portal Score from CBBD data."""
        df = getattr(self.app, "df_full", None)
        if df is None:
            return None
        # Try name match, optionally constrained to from_team
        row = find_player_in_df(player_name, df)
        if row is not None:
            return float(row.get("PortalScore", 0))
        return None

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        entries = self.entries

        # filter by status
        sf = self.status_filter.get()
        if sf != "All":
            entries = [e for e in entries
                       if e.get("status", "").lower() == sf.lower()]

        # filter by search
        sq = self.search_var.get().strip().lower()
        if sq:
            entries = [e for e in entries
                       if sq in e.get("player","").lower()
                       or sq in e.get("from_team","").lower()
                       or sq in e.get("to_team","").lower()]

        for e in sorted(entries,
                        key=lambda x: x.get("portal_score") or 0,
                        reverse=True):
            score = e.get("portal_score")
            tier  = tier_for_score(score) if score else "DEPTH"
            score_str = f"{score:.1f}" if score else "—"

            status = e.get("status", "In Portal")
            tag = tier
            if status.lower() == "committed":  tag = "GAIN"
            if status.lower() == "withdrawn":  tag = "NEUT"

            self.tree.insert("", "end", tags=(tag,), values=(
                e.get("player", ""),
                e.get("from_team", ""),
                e.get("to_team", "") or "—",
                e.get("position", ""),
                e.get("elig", ""),
                e.get("entered_date", ""),
                status,
                score_str,
                tier if score else "?",
            ))
        self.status_var.set(f"{len(entries)} entries shown  •  "
                            f"{len(self.entries)} total")

    def _enrich_scores(self, entries):
        """Attach Portal Scores to entries list."""
        for e in entries:
            score = self._get_portal_score(
                e.get("player",""), e.get("from_team",""))
            e["portal_score"] = score
        return entries

    def _scrape(self):
        self.status_var.set("⏳ Contacting On3…")
        self.parent.update_idletasks()
        threading.Thread(target=self._scrape_thread, daemon=True).start()

    def _scrape_thread(self):
        entries, msg = scrape_on3_portal()
        if entries:
            entries = self._enrich_scores(entries)
            # merge with existing (avoid dupes by name)
            existing_names = {normalize_name(e["player"])
                              for e in self.entries}
            new = [e for e in entries
                   if normalize_name(e["player"]) not in existing_names]
            self.entries.extend(new)
            save_portal_entries(self.entries)
        self.parent.after(0, lambda: self.status_var.set(msg))
        self.parent.after(0, self._refresh)
        if not entries:
            self.parent.after(0, lambda: messagebox.showinfo(
                "On3 Scraper", msg))

    def _upload_csv(self):
        path = filedialog.askopenfilename(
            title="Select Portal Entries CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        entries, msg = load_portal_entries_from_csv(path)
        if entries:
            entries = self._enrich_scores(entries)
            existing = {normalize_name(e["player"]) for e in self.entries}
            new = [e for e in entries
                   if normalize_name(e["player"]) not in existing]
            self.entries.extend(new)
            save_portal_entries(self.entries)
            self._refresh()
        messagebox.showinfo("CSV Import", msg)

    def _add_player_dialog(self):
        """Simple dialog to manually add a single portal entry."""
        win = tk.Toplevel(self.parent)
        win.title("Add Portal Entry")
        win.configure(bg=PANEL)
        win.geometry("380x340")
        win.resizable(False, False)

        fields = [
            ("Player Name *", "player"),
            ("Previous School *", "from_team"),
            ("Destination (if known)", "to_team"),
            ("Position", "position"),
            ("Eligibility (FR/SO/JR/SR/GR)", "elig"),
            ("Date Entered (MM/DD/YY)", "entered_date"),
        ]
        vars_ = {}
        for i, (label, key) in enumerate(fields):
            tk.Label(win, text=label, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 9)).grid(
                         row=i, column=0, sticky="w", padx=14, pady=4)
            v = tk.StringVar()
            vars_[key] = v
            tk.Entry(win, textvariable=v, bg=BORDER, fg=TEXT,
                     insertbackground=TEXT, relief="flat",
                     font=("Segoe UI", 9), width=22
                     ).grid(row=i, column=1, padx=8, pady=4)

        # status radio
        tk.Label(win, text="Status", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9)).grid(
                     row=len(fields), column=0, sticky="w", padx=14, pady=4)
        status_var = tk.StringVar(value="In Portal")
        srow = tk.Frame(win, bg=PANEL)
        srow.grid(row=len(fields), column=1, sticky="w", padx=8)
        for s in ["In Portal", "Committed", "Withdrawn"]:
            tk.Radiobutton(srow, text=s, variable=status_var, value=s,
                           bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL).pack(side="left")

        def save():
            name = vars_["player"].get().strip()
            if not name:
                messagebox.showwarning("Missing", "Player name is required.",
                                       parent=win)
                return
            entry = {k: v.get().strip() for k, v in vars_.items()}
            entry["status"] = status_var.get()
            entry["portal_score"] = self._get_portal_score(
                name, vars_["from_team"].get())
            self.entries.append(entry)
            save_portal_entries(self.entries)
            self._refresh()
            win.destroy()

        tk.Button(win, text="Add Entry", bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2",
                  font=("Segoe UI", 9, "bold"),
                  command=save).grid(
                      row=len(fields)+1, column=0, columnspan=2,
                      pady=12, padx=14, sticky="ew")

    def _clear_all(self):
        if messagebox.askyesno("Clear All",
                               "Remove all portal entries?"):
            self.entries = []
            save_portal_entries(self.entries)
            self._refresh()


# ══════════════════════════════════════════════
#  TAB B: 📋 TEAM ROSTER POWER RATING
# ══════════════════════════════════════════════

class TeamRosterTab:
    """
    Select any team → see their full roster ranked by Portal Score,
    top-8 rotation rating, and projected lineup strength.
    """

    def __init__(self, parent, app):
        self.app    = app
        self.parent = parent
        self._build()

    def _build(self):
        # ── top controls ──
        ctrl = tk.Frame(self.parent, bg=PANEL, height=44)
        ctrl.pack(fill="x", pady=(0, 4))
        ctrl.pack_propagate(False)

        tk.Label(ctrl, text="Select Team:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12,4),
                                                     pady=10)
        self.team_var = tk.StringVar()
        self.team_combo = ttk.Combobox(ctrl, textvariable=self.team_var,
                                        state="readonly", width=28,
                                        font=("Segoe UI", 9))
        self.team_combo["values"] = []
        self.team_combo.pack(side="left", padx=4, pady=8)
        self.team_combo.bind("<<ComboboxSelected>>",
                             lambda _: self._load_team())

        # compare team
        tk.Label(ctrl, text="Compare vs:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(16,4))
        self.compare_var = tk.StringVar()
        self.compare_combo = ttk.Combobox(ctrl, textvariable=self.compare_var,
                                           state="readonly", width=24,
                                           font=("Segoe UI", 9))
        self.compare_combo["values"] = ["(none)"]
        self.compare_combo.pack(side="left", padx=4, pady=8)
        self.compare_combo.bind("<<ComboboxSelected>>",
                                lambda _: self._load_team())

        # ── power rating banner ──
        self.rating_bar = tk.Frame(self.parent, bg=PANEL, height=64)
        self.rating_bar.pack(fill="x", pady=(0, 6))
        self.rating_bar.pack_propagate(False)
        self._build_rating_bar()

        # ── roster treeview ──
        cols = ("Rank", "Player", "Pos", "Elig", "G",
                "PTS", "TS%", "PORPAG", "USG%", "WS40",
                "PortalScore", "Tier", "Role")
        widths = {
            "Rank": 40, "Player": 180, "Pos": 40, "Elig": 40,
            "G": 35, "PTS": 48, "TS%": 50, "PORPAG": 65,
            "USG%": 50, "WS40": 58, "PortalScore": 80,
            "Tier": 58, "Role": 80,
        }
        self.tree = make_treeview(self.parent, cols, widths, stretch_col="Player")

    def _build_rating_bar(self):
        for w in self.rating_bar.winfo_children():
            w.destroy()
        stats = [
            ("Team",          "bar_team"),
            ("Conference",    "bar_conf"),
            ("Power Rating",  "bar_rating"),
            ("Rotation (8)",  "bar_rotation"),
            ("Bench Depth",   "bar_bench"),
            ("Avg TS%",       "bar_ts"),
            ("Avg PORPAG",    "bar_por"),
            ("Compare",       "bar_compare"),
        ]
        for title, attr in stats:
            f = tk.Frame(self.rating_bar, bg=PANEL)
            f.pack(side="left", padx=14, pady=8)
            tk.Label(f, text=title, bg=PANEL, fg=SUBTEXT,
                     font=("Segoe UI", 8)).pack(anchor="w")
            lbl = tk.Label(f, text="—", bg=PANEL, fg=TEXT,
                           font=("Segoe UI", 12, "bold"))
            lbl.pack(anchor="w")
            setattr(self, attr, lbl)

    def on_data_loaded(self):
        """Called after CBBD data loads."""
        df = getattr(self.app, "df_full", None)
        if df is None:
            return
        teams = sorted(df["Team"].dropna().unique())
        self.team_combo["values"]   = teams
        self.compare_combo["values"] = ["(none)"] + teams
        self.compare_var.set("(none)")

    def _load_team(self):
        team = self.team_var.get()
        if not team:
            return
        df = getattr(self.app, "df_full", None)
        if df is None:
            return

        roster = df[df["Team"] == team].copy()
        roster = roster.sort_values("PortalScore", ascending=False)

        self._refresh_roster(roster)
        self._refresh_rating_bar(team, roster, df)

    def _refresh_roster(self, roster):
        self.tree.delete(*self.tree.get_children())
        for rank, (_, row) in enumerate(roster.iterrows(), 1):
            score = float(row.get("PortalScore", 0))
            tier  = tier_for_score(score)
            role  = "Starter" if rank <= 5 else ("Rotation" if rank <= 8
                                                  else "Bench")
            role_colour = {"Starter": ACCENT, "Rotation": GOLD,
                           "Bench": SUBTEXT}
            self.tree.insert("", "end", tags=(tier,), values=(
                rank,
                row.get("Player", ""),
                row.get("Position", ""),
                row.get("Elig", ""),
                int(row.get("G", 0)),
                f"{float(row.get('PTS', 0)):.1f}",
                f"{float(row.get('TS%', 0)):.1f}",
                f"{float(row.get('PORPAG', 0)):.2f}",
                f"{float(row.get('USG%', 0)):.1f}",
                f"{float(row.get('WS40', 0)):.3f}",
                f"{score:.2f}",
                tier,
                role,
            ))

    def _calc_power_rating(self, roster):
        """
        Power Rating = weighted avg Portal Score of top 8 by minutes.
        Minutes weight: starter minutes count 1.5x bench minutes.
        """
        if len(roster) == 0:
            return 0, 0, 0
        by_min = roster.sort_values("Min", ascending=False)
        top8   = by_min.head(8)
        bench  = by_min.iloc[8:] if len(by_min) > 8 else pd.DataFrame()

        # Weight by minutes within top 8
        mins   = pd.to_numeric(top8["Min"], errors="coerce").fillna(0)
        scores = pd.to_numeric(top8["PortalScore"], errors="coerce").fillna(0)
        total_min = mins.sum()
        if total_min > 0:
            rotation_rating = (scores * mins).sum() / total_min
        else:
            rotation_rating = scores.mean()

        bench_rating = (pd.to_numeric(bench["PortalScore"],
                                       errors="coerce").fillna(0).mean()
                        if len(bench) > 0 else 0)

        # Final power rating: 80% rotation, 20% bench depth
        power = rotation_rating * 0.80 + bench_rating * 0.20
        return round(power, 2), round(rotation_rating, 2), round(bench_rating, 2)

    def _refresh_rating_bar(self, team, roster, df):
        power, rot, bench = self._calc_power_rating(roster)
        conf = roster["Conference"].iloc[0] if len(roster) and "Conference" in roster.columns else "—"
        avg_ts   = pd.to_numeric(roster["TS%"], errors="coerce").mean()
        avg_por  = pd.to_numeric(roster["PORPAG"], errors="coerce").mean()

        self.bar_team.config(text=team)
        self.bar_conf.config(text=conf)
        self.bar_rating.config(
            text=f"{power:.1f}",
            fg=(ACCENT  if power >= 70 else
                GOLD    if power >= 55 else
                SUBTEXT))
        self.bar_rotation.config(text=f"{rot:.1f}")
        self.bar_bench.config(text=f"{bench:.1f}")
        self.bar_ts.config(text=f"{avg_ts:.1f}%" if not np.isnan(avg_ts) else "—")
        self.bar_por.config(text=f"{avg_por:.2f}" if not np.isnan(avg_por) else "—")

        # compare team
        compare = self.compare_var.get()
        if compare and compare != "(none)":
            cmp_roster = df[df["Team"] == compare].copy()
            cmp_power, _, _ = self._calc_power_rating(cmp_roster)
            delta = power - cmp_power
            sign  = "+" if delta > 0 else ""
            colour = ACCENT if delta > 0 else RED if delta < 0 else SUBTEXT
            self.bar_compare.config(
                text=f"{compare}: {cmp_power:.1f}  ({sign}{delta:.1f})",
                fg=colour)
        else:
            self.bar_compare.config(text="—", fg=SUBTEXT)


# ══════════════════════════════════════════════
#  TAB C: 🔀 DESTINATION TRACKER
# ══════════════════════════════════════════════

class DestinationTab:
    """
    Track where portal players commit and see the net
    impact on team Power Ratings.

    Two input modes:
      - Manual: type player name + destination
      - Auto:   reads committed entries from PortalEntriesTab
    """

    MOVES_FILE = "portal_moves.json"

    def __init__(self, parent, app):
        self.app    = app
        self.parent = parent
        self.moves  = self._load_moves()
        self._build()

    def _load_moves(self):
        if os.path.exists(self.MOVES_FILE):
            with open(self.MOVES_FILE) as f:
                return json.load(f)
        return []

    def _save_moves(self):
        with open(self.MOVES_FILE, "w") as f:
            json.dump(self.moves, f, indent=2)

    def _build(self):
        # ── control bar ──
        ctrl = tk.Frame(self.parent, bg=PANEL, height=44)
        ctrl.pack(fill="x", pady=(0,4))
        ctrl.pack_propagate(False)

        accent_btn(ctrl, "➕ Add Commitment",
                   self._add_move_dialog, color=ACCENT,  side="left")
        accent_btn(ctrl, "⟳ Sync from Portal Tab",
                   self._sync_from_entries, color=ACCENT2, side="left")
        accent_btn(ctrl, "🗑 Clear All",
                   self._clear_moves, color=RED, side="left")

        self.status_var = tk.StringVar(value=f"{len(self.moves)} commitments tracked")
        tk.Label(ctrl, textvariable=self.status_var, bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 8)).pack(side="left", padx=12)

        # ── moves treeview ──
        cols = ("Player", "From", "To", "Score",
                "From Δ Rating", "To Δ Rating", "Net Impact")
        widths = {
            "Player": 180, "From": 150, "To": 150,
            "Score": 70, "From Δ Rating": 100,
            "To Δ Rating": 100, "Net Impact": 90,
        }
        self.tree = make_treeview(self.parent, cols, widths, stretch_col="Player")

        # ── team impact summary below ──
        tk.Label(self.parent, text="TEAM PORTAL NET IMPACT",
                 bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 8, "bold")).pack(
                     anchor="w", padx=8, pady=(8,2))

        cols2   = ("Team", "Players In", "Players Out",
                   "Score In", "Score Out", "Net Δ Rating", "Grade")
        widths2 = {
            "Team": 160, "Players In": 80, "Players Out": 80,
            "Score In": 80, "Score Out": 80,
            "Net Δ Rating": 90, "Grade": 60,
        }
        self.impact_tree = make_treeview(self.parent, cols2, widths2,
                                          stretch_col="Team", height=8)

        self._refresh()

    def on_data_loaded(self):
        self._refresh()

    def _get_player_score(self, name, from_team=""):
        df = getattr(self.app, "df_full", None)
        if df is None:
            return None
        row = find_player_in_df(name, df)
        return float(row["PortalScore"]) if row is not None else None

    def _calc_team_power(self, team):
        """Return team power rating using TeamRosterTab logic."""
        df = getattr(self.app, "df_full", None)
        if df is None:
            return 0
        roster = df[df["Team"] == team].copy()
        if len(roster) == 0:
            return 0
        by_min = roster.sort_values("Min", ascending=False)
        top8   = by_min.head(8)
        mins   = pd.to_numeric(top8["Min"], errors="coerce").fillna(0)
        scores = pd.to_numeric(top8["PortalScore"], errors="coerce").fillna(0)
        t = mins.sum()
        return float((scores * mins).sum() / t) if t > 0 else float(scores.mean())

    def _estimate_delta(self, player_score, team, direction):
        """
        Estimate change in team power rating when a player
        joins (direction='in') or leaves (direction='out').
        Simplified: compares player score to team's #8 man score.
        """
        df = getattr(self.app, "df_full", None)
        if df is None or player_score is None:
            return 0
        roster = df[df["Team"] == team].sort_values(
            "PortalScore", ascending=False)
        if len(roster) == 0:
            return 0
        # approximate displaced/added player as #8 or bottom
        ref_score = float(
            roster.iloc[min(7, len(roster)-1)]["PortalScore"])
        delta = (player_score - ref_score) * 0.08   # scaled to rating units
        return round(delta if direction == "in" else -delta, 2)

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())

        for m in sorted(self.moves,
                        key=lambda x: x.get("score") or 0, reverse=True):
            score    = m.get("score")
            from_d   = m.get("from_delta", 0)
            to_d     = m.get("to_delta", 0)
            net      = (to_d or 0) - abs(from_d or 0)
            net_tag  = "GAIN" if net > 0 else "LOSS" if net < 0 else "NEUT"

            self.tree.insert("", "end", tags=(net_tag,), values=(
                m.get("player", ""),
                m.get("from_team", ""),
                m.get("to_team", "") or "TBD",
                f"{score:.1f}" if score else "—",
                f"{from_d:+.2f}" if from_d else "—",
                f"{to_d:+.2f}"  if to_d  else "—",
                f"{net:+.2f}"   if (from_d or to_d) else "—",
            ))

        self._refresh_impact_summary()
        self.status_var.set(f"{len(self.moves)} commitments tracked")

    def _refresh_impact_summary(self):
        self.impact_tree.delete(*self.impact_tree.get_children())
        if not self.moves:
            return

        teams = {}
        for m in self.moves:
            score = m.get("score") or 0
            ft    = m.get("from_team", "")
            tt    = m.get("to_team", "")
            if ft:
                t = teams.setdefault(ft, {"in": [], "out": [], "in_score": 0, "out_score": 0})
                t["out"].append(m["player"])
                t["out_score"] += score
                t["delta"] = t.get("delta", 0) + (m.get("from_delta") or 0)
            if tt and tt not in ("TBD", "—", ""):
                t = teams.setdefault(tt, {"in": [], "out": [], "in_score": 0, "out_score": 0})
                t["in"].append(m["player"])
                t["in_score"] += score
                t["delta"] = t.get("delta", 0) + (m.get("to_delta") or 0)

        rows = []
        for team, data in teams.items():
            net   = data.get("delta", 0)
            grade = ("A" if net >= 2 else "B" if net >= 0.5 else
                     "C" if net >= -0.5 else "D" if net >= -2 else "F")
            rows.append((team, data, net, grade))
        rows.sort(key=lambda x: x[2], reverse=True)

        for team, data, net, grade in rows:
            tag   = "GAIN" if net > 0 else "LOSS" if net < 0 else "NEUT"
            grade_colour = {
                "A": ACCENT, "B": GOLD, "C": SUBTEXT,
                "D": ORANGE, "F": RED,
            }.get(grade, SUBTEXT)
            self.impact_tree.insert("", "end", tags=(tag,), values=(
                team,
                len(data["in"]),
                len(data["out"]),
                f"{data['in_score']:.1f}"  if data["in_score"]  else "—",
                f"{data['out_score']:.1f}" if data["out_score"] else "—",
                f"{net:+.2f}",
                grade,
            ))

    def _add_move_dialog(self):
        win = tk.Toplevel(self.parent)
        win.title("Add Commitment")
        win.configure(bg=PANEL)
        win.geometry("360x280")
        win.resizable(False, False)

        df    = getattr(self.app, "df_full", None)
        teams = sorted(df["Team"].dropna().unique()) if df is not None else []

        fields = [
            ("Player Name *",    "player",    "entry"),
            ("Previous School *","from_team",  "combo"),
            ("New School *",     "to_team",    "combo"),
        ]
        vars_ = {}
        for i, (label, key, kind) in enumerate(fields):
            tk.Label(win, text=label, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 9)).grid(
                         row=i, column=0, sticky="w", padx=14, pady=6)
            v = tk.StringVar()
            vars_[key] = v
            if kind == "combo":
                w = ttk.Combobox(win, textvariable=v,
                                  values=teams, width=22,
                                  font=("Segoe UI", 9))
            else:
                w = tk.Entry(win, textvariable=v, bg=BORDER, fg=TEXT,
                             insertbackground=TEXT, relief="flat",
                             font=("Segoe UI", 9), width=24)
            w.grid(row=i, column=1, padx=8, pady=6)

        def save():
            name      = vars_["player"].get().strip()
            from_team = vars_["from_team"].get().strip()
            to_team   = vars_["to_team"].get().strip()
            if not name or not from_team:
                messagebox.showwarning("Missing",
                    "Player name and previous school are required.",
                    parent=win)
                return
            score      = self._get_player_score(name, from_team)
            from_delta = self._estimate_delta(score, from_team, "out")
            to_delta   = self._estimate_delta(score, to_team, "in") if to_team else 0

            move = {
                "player":     name,
                "from_team":  from_team,
                "to_team":    to_team,
                "score":      score,
                "from_delta": from_delta,
                "to_delta":   to_delta,
                "date":       datetime.now().strftime("%m/%d/%y"),
            }
            self.moves.append(move)
            self._save_moves()
            self._refresh()
            win.destroy()

        tk.Button(win, text="Save Commitment", bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2",
                  font=("Segoe UI", 9, "bold"),
                  command=save).grid(
                      row=len(fields), column=0, columnspan=2,
                      pady=14, padx=14, sticky="ew")

    def _sync_from_entries(self):
        """Pull committed entries from PortalEntriesTab automatically."""
        entries_tab = getattr(self.app, "entries_tab", None)
        if entries_tab is None:
            messagebox.showinfo("Sync", "Portal Entries tab not available.")
            return

        committed = [e for e in entries_tab.entries
                     if e.get("status","").lower() == "committed"
                     and e.get("to_team","")]

        existing = {normalize_name(m["player"]) for m in self.moves}
        added = 0
        for e in committed:
            if normalize_name(e["player"]) in existing:
                continue
            score      = e.get("portal_score") or self._get_player_score(
                e["player"], e.get("from_team",""))
            from_delta = self._estimate_delta(score, e["from_team"], "out")
            to_delta   = self._estimate_delta(score, e["to_team"],   "in")
            self.moves.append({
                "player":     e["player"],
                "from_team":  e.get("from_team",""),
                "to_team":    e.get("to_team",""),
                "score":      score,
                "from_delta": from_delta,
                "to_delta":   to_delta,
                "date":       e.get("entered_date",""),
            })
            added += 1

        self._save_moves()
        self._refresh()
        messagebox.showinfo("Sync Complete",
            f"Added {added} new commitments from Portal Entries tab.")

    def _clear_moves(self):
        if messagebox.askyesno("Clear All",
                               "Remove all tracked commitments?"):
            self.moves = []
            self._save_moves()
            self._refresh()
