"""
=============================================================
  NCAA D-I Men's Basketball Transfer Portal Tracker — GUI
  portal_gui.py  |  v2.0  — CBBD Native
=============================================================
  Run with:  python portal_gui.py
             %run portal_gui.py  (Jupyter)

  Requires:  pandas, numpy, cbbd
             portal_tracker_cbbd.py in the same folder

  Changes from v1.0:
    - Loads from CBBD API (live) instead of CSV
    - PORPAG replaces PER in leaderboard + weight sliders
    - Conference filter dropdown added
    - Position filter checkboxes added
    - Team search matches full team name
    - By Conference tab added
    - API status indicator in top bar
    - Force Fresh Pull button bypasses cache
=============================================================
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pandas as pd
    import numpy as np
except ImportError:
    messagebox.showerror("Missing Package",
        "pandas and numpy are required.\nRun: pip install pandas numpy")
    sys.exit(1)

try:
    from portal_tracker_cbbd import (
        load_data, apply_filters, compute_composite,
        WEIGHTS, MIN_GAMES, MIN_TOTAL_MINUTES, CBBD_API_KEY
    )
    import portal_tracker_cbbd as pt
except ImportError:
    messagebox.showerror("Missing File",
        "portal_tracker_cbbd.py must be in the same folder as portal_gui.py")
    sys.exit(1)

try:
    from portal_features import PortalEntriesTab, TeamRosterTab, DestinationTab
    FEATURES_AVAILABLE = True
except ImportError:
    FEATURES_AVAILABLE = False

try:
    from formula_engine import (density_colour, build_shade_map,
                                SHADE_COL_MAP_HIGH, SHADE_COL_MAP_LOW)
    SHADING_AVAILABLE = True
except ImportError:
    SHADING_AVAILABLE = False

# ──────────────────────────────────────────────
#  COLOURS
# ──────────────────────────────────────────────

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

TIER_COLOURS = {
    "ELITE":  "#58a6ff",
    "HIGH":   "#3fb950",
    "SOLID":  "#d29922",
    "FRINGE": "#e3b341",
    "DEPTH":  "#8b949e",
}

def tier_for_score(score):
    if score >= 85: return "ELITE"
    if score >= 70: return "HIGH"
    if score >= 55: return "SOLID"
    if score >= 40: return "FRINGE"
    return "DEPTH"

# display_name, df_column, width, anchor
# ── System 1 cols (Portal Score 0-100) marked with S1
# ── System 2 cols (Custom Score, your formula) marked with S2
LB_COLS = [
    # Identity
    ("Rank",      "Rank",        45,  "center"),
    ("Player",    "Player",     170,  "w"),
    ("Team",      "Team",       115,  "center"),
    ("Pos",       "Position",    38,  "center"),
    ("Elig",      "Elig",        38,  "center"),
    ("G",         "G",           32,  "center"),
    # Raw stats
    ("PTS",       "PTS",         45,  "center"),
    ("TS%",       "TS%",         48,  "center"),
    ("eFG%",      "eFGPct",      48,  "center"),
    ("USG%",      "USG%",        48,  "center"),
    # ── System 1 inputs ──
    ("PER",       "PER",         52,  "center"),   # computed, avg=15
    ("PORPAG",    "PORPAG",      62,  "center"),   # CBBD native
    ("WS/40",     "WS40",        55,  "center"),
    ("DEF",       "DEF_comp",    48,  "center"),
    # ── System 1 output ──
    ("S1: Score", "PortalScore", 68,  "center"),   # 0-100, Elite≥85
    ("S1: Tier",  "Tier",        55,  "center"),
    # ── System 2 inputs ──
    ("KP Rank",   "KenPomRank",  58,  "center"),
    ("KP ×",      "KenPomMult",  48,  "center"),
    ("Base",      "BaseScore",   52,  "center"),
    ("PRA",       "PRACombo",    52,  "center"),
    # ── System 2 output ──
    ("S2: Final", "FinalScore",  62,  "center"),   # raw formula score
    ("S2: NIL",   "NILValue",    82,  "center"),   # NIL estimate
]

# ──────────────────────────────────────────────
#  MAIN APP
# ──────────────────────────────────────────────

class PortalTrackerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("NCAA D-I Transfer Portal Tracker  |  v2.0  CBBD")
        self.geometry("1380x860")
        self.minsize(1100, 700)
        self.configure(bg=BG)

        self.df_full    = None
        self.df_display = None
        self.sort_col   = "PortalScore"
        self.sort_asc   = False
        self.shade_map  = {}        # { df_col: { row_idx: hex_colour } }
        self.density_on = tk.BooleanVar(value=True)

        self.w_porpag = tk.DoubleVar(value=25)
        self.w_ts     = tk.DoubleVar(value=20)
        self.w_ws40   = tk.DoubleVar(value=15)
        self.w_usg    = tk.DoubleVar(value=10)
        self.w_def    = tk.DoubleVar(value=15)
        self.w_conf   = tk.DoubleVar(value=15)

        self._build_ui()
        self.after(300, self._run_scores)

    # ──────────────────────────────────────────
    #  UI
    # ──────────────────────────────────────────

    def _build_ui(self):
        topbar = tk.Frame(self, bg=PANEL, height=56)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        tk.Label(topbar, text="⛹  NCAA Transfer Portal Tracker",
                 font=("Segoe UI", 15, "bold"),
                 bg=PANEL, fg=TEXT).pack(side="left", padx=18, pady=12)

        self.api_dot = tk.Label(topbar, text="●", font=("Segoe UI", 11),
                                bg=PANEL, fg=SUBTEXT)
        self.api_dot.pack(side="right", padx=6)
        tk.Label(topbar, text="CBBD API", font=("Segoe UI", 9),
                 bg=PANEL, fg=SUBTEXT).pack(side="right")
        tk.Label(topbar, text=f"v2.0  •  {datetime.now().strftime('%Y')}",
                 font=("Segoe UI", 9), bg=PANEL, fg=SUBTEXT
                 ).pack(side="right", padx=24)

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)

        sidebar_outer = tk.Frame(main, bg=PANEL, width=285)
        sidebar_outer.pack(side="left", fill="y", padx=(8, 0), pady=8)
        sidebar_outer.pack_propagate(False)

        # Scrollable sidebar canvas
        sb_canvas = tk.Canvas(sidebar_outer, bg=PANEL,
                              highlightthickness=0, width=268)
        sb_vsb    = ttk.Scrollbar(sidebar_outer, orient="vertical",
                                   command=sb_canvas.yview)
        sb_canvas.configure(yscrollcommand=sb_vsb.set)
        sb_vsb.pack(side="right", fill="y")
        sb_canvas.pack(side="left", fill="both", expand=True)

        sidebar = tk.Frame(sb_canvas, bg=PANEL)
        sb_win  = sb_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        sidebar.bind("<Configure>",
            lambda e: sb_canvas.configure(
                scrollregion=sb_canvas.bbox("all")))
        sb_canvas.bind("<Configure>",
            lambda e: sb_canvas.itemconfig(sb_win, width=e.width))

        # Mouse-wheel scroll on sidebar
        def _sb_scroll(event):
            sb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        sb_canvas.bind_all("<MouseWheel>", _sb_scroll)

        content = tk.Frame(main, bg=BG)
        content.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        self._build_sidebar(sidebar)
        self._build_content(content)

    def _build_sidebar(self, parent):
        pad = {"padx": 14, "pady": 3}

        self._section_label(parent, "DATA SOURCE")
        self._accent_btn(parent, "▶  Fetch / Refresh from CBBD",
                         self._run_scores, color=ACCENT)
        self._accent_btn(parent, "⟳  Force Fresh API Pull",
                         self._force_refresh, color=ACCENT3)
        self._accent_btn(parent, "📊  Import KenPom CSV",
                         self._import_kenpom, color=ACCENT2)

        # KenPom status indicator
        self.kenpom_status = tk.StringVar(value="KenPom: not loaded")
        tk.Label(parent, textvariable=self.kenpom_status,
                 bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=14, pady=(0,4))

        ttk.Separator(parent, orient="horizontal").pack(
            fill="x", padx=14, pady=8)

        self._section_label(parent, "FILTERS")

        # GR / Gone Pro toggles
        toggle_row = tk.Frame(parent, bg=PANEL)
        toggle_row.pack(fill="x", padx=14, pady=(2,4))
        self.exclude_gr  = tk.BooleanVar(value=True)
        self.exclude_pro = tk.BooleanVar(value=True)
        tk.Checkbutton(toggle_row, text="Exclude GR",
                       variable=self.exclude_gr, bg=PANEL, fg=TEXT,
                       selectcolor=BG, activebackground=PANEL,
                       activeforeground=TEXT,
                       command=self._apply_filters).pack(side="left")
        tk.Checkbutton(toggle_row, text="Exclude Gone Pro",
                       variable=self.exclude_pro, bg=PANEL, fg=TEXT,
                       selectcolor=BG, activebackground=PANEL,
                       activeforeground=TEXT,
                       command=self._apply_filters).pack(side="left", padx=(8,0))

        tk.Label(parent, text="Eligibility:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.elig_vars = {}
        elig_row = tk.Frame(parent, bg=PANEL)
        elig_row.pack(anchor="w", padx=14, pady=2)
        for lbl in ["FR", "SO", "JR", "SR", "GR"]:
            v = tk.BooleanVar(value=True)
            self.elig_vars[lbl] = v
            tk.Checkbutton(elig_row, text=lbl, variable=v,
                           bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, activeforeground=TEXT,
                           command=self._apply_filters).pack(side="left")

        tk.Label(parent, text="Position:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.pos_vars = {}
        pos_row = tk.Frame(parent, bg=PANEL)
        pos_row.pack(anchor="w", padx=14, pady=2)
        for lbl in ["G", "F", "C"]:
            v = tk.BooleanVar(value=True)
            self.pos_vars[lbl] = v
            tk.Checkbutton(pos_row, text=lbl, variable=v,
                           bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, activeforeground=TEXT,
                           command=self._apply_filters).pack(side="left")

        tk.Label(parent, text="Min Portal Score:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.min_score = tk.DoubleVar(value=0)
        score_row = tk.Frame(parent, bg=PANEL)
        score_row.pack(fill="x", padx=14)
        tk.Scale(score_row, from_=0, to=100, orient="horizontal",
                 variable=self.min_score, bg=PANEL, fg=TEXT,
                 highlightthickness=0, troughcolor=BORDER,
                 command=lambda _: self._apply_filters()
                 ).pack(side="left", fill="x", expand=True)
        tk.Label(score_row, textvariable=self.min_score,
                 bg=PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                 width=4).pack(side="left")

        tk.Label(parent, text="Team search:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.team_filter = tk.StringVar()
        self.team_filter.trace_add("write", lambda *_: self._apply_filters())
        tk.Entry(parent, textvariable=self.team_filter,
                 bg=BORDER, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=("Segoe UI", 9)
                 ).pack(fill="x", padx=14, pady=2)

        tk.Label(parent, text="Player search:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.player_filter = tk.StringVar()
        self.player_filter.trace_add("write", lambda *_: self._apply_filters())
        tk.Entry(parent, textvariable=self.player_filter,
                 bg=BORDER, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=("Segoe UI", 9)
                 ).pack(fill="x", padx=14, pady=2)

        ttk.Separator(parent, orient="horizontal").pack(
            fill="x", padx=14, pady=8)

        self._section_label(parent, "ALGORITHM WEIGHTS")
        weight_def = [
            ("PORPAG", self.w_porpag),
            ("TS%",    self.w_ts),
            ("WS/40",  self.w_ws40),
            ("USG%",   self.w_usg),
            ("DEF",    self.w_def),
            ("CONF",   self.w_conf),
        ]
        for label, var in weight_def:
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", padx=14, pady=1)
            tk.Label(row, text=f"{label:<7}", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 8, "bold"), width=7,
                     anchor="w").pack(side="left")
            tk.Scale(row, from_=0, to=50, orient="horizontal",
                     variable=var, bg=PANEL, fg=TEXT,
                     highlightthickness=0, troughcolor=BORDER,
                     length=115).pack(side="left")
            tk.Label(row, textvariable=var,
                     bg=PANEL, fg=ACCENT, font=("Segoe UI", 8),
                     width=3).pack(side="left")

        self._accent_btn(parent, "↻  Recalculate with Weights",
                         self._recalculate, color=GOLD)

        ttk.Separator(parent, orient="horizontal").pack(
            fill="x", padx=14, pady=8)

        self._section_label(parent, "DISPLAY")
        toggle_row2 = tk.Frame(parent, bg=PANEL)
        toggle_row2.pack(fill="x", padx=14, pady=(2, 4))
        tk.Checkbutton(toggle_row2, text="Density Shading",
                       variable=self.density_on, bg=PANEL, fg=TEXT,
                       selectcolor=BG, activebackground=PANEL,
                       activeforeground=TEXT,
                       command=self._refresh_lb).pack(side="left")
        tk.Label(toggle_row2, text="(Red→Yellow→Green per column)",
                 bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 7)).pack(side="left", padx=4)

        ttk.Separator(parent, orient="horizontal").pack(
            fill="x", padx=14, pady=8)

        self._section_label(parent, "EXPORT")
        self._accent_btn(parent, "💾  Export Current View to CSV",
                         self._export_csv)

    def _build_content(self, parent):
        # ── Conference / quick-sort filter bar (no scroll conflict) ──
        fbar = tk.Frame(parent, bg=PANEL, height=38)
        fbar.pack(fill="x", pady=(0, 4))
        fbar.pack_propagate(False)

        tk.Label(fbar, text="Conference:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(12, 4), pady=8)
        self.conf_filter = tk.StringVar(value="All")
        self.conf_combo  = ttk.Combobox(fbar, textvariable=self.conf_filter,
                                         state="readonly", width=18,
                                         font=("Segoe UI", 9))
        self.conf_combo["values"] = ["All"]
        self.conf_combo.set("All")
        self.conf_combo.pack(side="left", pady=6)
        self.conf_combo.bind("<<ComboboxSelected>>",
                             lambda _: self._apply_filters())

        tk.Label(fbar, text="Quick sort:", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 4))
        for label, col in [("S1 Score", "PortalScore"),
                            ("S2 Final", "FinalScore"),
                            ("NIL",      "NILValue"),
                            ("PER",      "PER"),
                            ("PORPAG",   "PORPAG")]:
            tk.Button(fbar, text=label, bg=BORDER, fg=TEXT,
                      activebackground=ACCENT2, activeforeground=TEXT,
                      relief="flat", cursor="hand2",
                      font=("Segoe UI", 8), padx=6, pady=2,
                      command=lambda c=col: self._quick_sort(c)
                      ).pack(side="left", padx=2, pady=6)

        # ── Stats bar ──
        self.stats_bar = tk.Frame(parent, bg=PANEL, height=54)
        self.stats_bar.pack(fill="x", pady=(0, 6))
        self.stats_bar.pack_propagate(False)
        self._build_stats_bar()

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook",     background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=SUBTEXT,
                        padding=[14, 6], font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", BG)],
                  foreground=[("selected", TEXT)])
        style.configure("Portal.Treeview",
                        background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=24, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Portal.Treeview.Heading",
                        background=BG, foreground=SUBTEXT,
                        relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Portal.Treeview",
                  background=[("selected", ACCENT2)],
                  foreground=[("selected", TEXT)])

        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)

        self.tab_lb   = tk.Frame(self.notebook, bg=BG)
        self.tab_team = tk.Frame(self.notebook, bg=BG)
        self.tab_conf = tk.Frame(self.notebook, bg=BG)
        self.tab_tier = tk.Frame(self.notebook, bg=BG)

        self.notebook.add(self.tab_lb,   text="  🏆 Leaderboard  ")
        self.notebook.add(self.tab_team, text="  🏫 By Team  ")
        self.notebook.add(self.tab_conf, text="  🗺 By Conference  ")
        self.notebook.add(self.tab_tier, text="  📊 Tier View  ")

        self._build_lb_tab()
        self._build_team_tab()
        self._build_conf_tab()
        self._build_tier_tab()

        # ── Feature tabs (portal_features.py) ──
        if FEATURES_AVAILABLE:
            self.tab_entries = tk.Frame(self.notebook, bg=BG)
            self.tab_roster  = tk.Frame(self.notebook, bg=BG)
            self.tab_dest    = tk.Frame(self.notebook, bg=BG)
            self.notebook.add(self.tab_entries, text="  🚪 Portal Entries  ")
            self.notebook.add(self.tab_roster,  text="  📋 Team Roster  ")
            self.notebook.add(self.tab_dest,    text="  🔀 Destinations  ")
            self.entries_tab = PortalEntriesTab(self.tab_entries, self)
            self.roster_tab  = TeamRosterTab(self.tab_roster,  self)
            self.dest_tab    = DestinationTab(self.tab_dest,   self)

        self.status_var = tk.StringVar(value="Connecting to CBBD API…")
        tk.Label(parent, textvariable=self.status_var,
                 bg=BG, fg=SUBTEXT, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", pady=(4, 0))

    def _build_stats_bar(self):
        for w in self.stats_bar.winfo_children():
            w.destroy()
        for title, attr in [
            ("Total Players", "stat_total"), ("Shown",        "stat_shown"),
            ("Avg Score",     "stat_avg"),   ("Top Score",    "stat_top"),
            ("Elite (85+)",   "stat_elite"), ("High (70–84)", "stat_high"),
            ("Teams",         "stat_teams"),
        ]:
            f = tk.Frame(self.stats_bar, bg=PANEL)
            f.pack(side="left", padx=18, pady=8)
            tk.Label(f, text=title, bg=PANEL, fg=SUBTEXT,
                     font=("Segoe UI", 8)).pack(anchor="w")
            lbl = tk.Label(f, text="—", bg=PANEL, fg=TEXT,
                           font=("Segoe UI", 13, "bold"))
            lbl.pack(anchor="w")
            setattr(self, attr, lbl)

    def _build_lb_tab(self):
        style = ttk.Style()
        style.configure("Portal.Treeview",
                        background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=24, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Portal.Treeview.Heading",
                        background=BG, foreground=SUBTEXT,
                        relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Portal.Treeview",
                  background=[("selected", ACCENT2)],
                  foreground=[("selected", TEXT)])

        # Main treeview frame
        frame = tk.Frame(self.tab_lb, bg=BG)
        frame.pack(fill="both", expand=True, side="top")
        disp_names = [c[0] for c in LB_COLS]
        self.tree = ttk.Treeview(frame, columns=disp_names, show="headings",
                                  style="Portal.Treeview", selectmode="browse")
        for disp, _, width, anchor in LB_COLS:
            self.tree.heading(disp, text=disp,
                              command=lambda c=disp: self._sort_by(c))
            self.tree.column(disp, width=width, anchor=anchor,
                             stretch=(disp == "Player"))
        vsb = ttk.Scrollbar(frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for tier, colour in TIER_COLOURS.items():
            self.tree.tag_configure(tier, foreground=colour)

        # Heatmap canvas (below treeview, shown when density shading on)
        self.heatmap_frame = tk.Frame(self.tab_lb, bg=BG, height=0)
        self.heatmap_frame.pack(fill="x", side="bottom")
        self.heatmap_canvas = tk.Canvas(self.heatmap_frame, bg=PANEL,
                                         height=0, highlightthickness=0)
        self.heatmap_canvas.pack(fill="x")

    def _build_team_tab(self):
        frame = tk.Frame(self.tab_team, bg=BG)
        frame.pack(fill="both", expand=True)
        cols = ("Team", "Conference", "Players", "Avg Score",
                "Best Player", "Best Score")
        self.team_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                       style="Portal.Treeview")
        widths = {"Team": 160, "Conference": 100, "Players": 65,
                  "Avg Score": 90, "Best Player": 200, "Best Score": 90}
        for col in cols:
            self.team_tree.heading(col, text=col)
            self.team_tree.column(col, width=widths.get(col, 120),
                                   anchor="center",
                                   stretch=(col == "Best Player"))
        vsb = ttk.Scrollbar(frame, orient="vertical",
                            command=self.team_tree.yview)
        self.team_tree.configure(yscrollcommand=vsb.set)
        self.team_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_conf_tab(self):
        frame = tk.Frame(self.tab_conf, bg=BG)
        frame.pack(fill="both", expand=True)
        cols = ("Conference", "Players", "Avg Score",
                "Elite", "High", "Top Player")
        self.conf_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                       style="Portal.Treeview")
        widths = {"Conference": 140, "Players": 70, "Avg Score": 90,
                  "Elite": 55, "High": 55, "Top Player": 220}
        for col in cols:
            self.conf_tree.heading(col, text=col)
            self.conf_tree.column(col, width=widths.get(col, 100),
                                   anchor="center",
                                   stretch=(col == "Top Player"))
        vsb = ttk.Scrollbar(frame, orient="vertical",
                            command=self.conf_tree.yview)
        self.conf_tree.configure(yscrollcommand=vsb.set)
        self.conf_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_tier_tab(self):
        self.tier_canvas = tk.Canvas(self.tab_tier, bg=BG,
                                      highlightthickness=0)
        vsb = ttk.Scrollbar(self.tab_tier, orient="vertical",
                            command=self.tier_canvas.yview)
        self.tier_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tier_canvas.pack(side="left", fill="both", expand=True)
        self.tier_frame = tk.Frame(self.tier_canvas, bg=BG)
        self._tier_win = self.tier_canvas.create_window(
            (0, 0), window=self.tier_frame, anchor="nw")
        self.tier_frame.bind("<Configure>",
            lambda e: self.tier_canvas.configure(
                scrollregion=self.tier_canvas.bbox("all")))
        self.tier_canvas.bind("<Configure>",
            lambda e: self.tier_canvas.itemconfig(
                self._tier_win, width=e.width))

    # ──────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────

    def _section_label(self, parent, text):
        tk.Label(parent, text=text, bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 8, "bold")
                 ).pack(anchor="w", padx=14, pady=(10, 2))

    def _accent_btn(self, parent, text, cmd, color=None):
        tk.Button(parent, text=text, bg=color or ACCENT, fg=TEXT,
                  activebackground=color or ACCENT, activeforeground=TEXT,
                  relief="flat", cursor="hand2",
                  font=("Segoe UI", 9, "bold"),
                  padx=10, pady=6, command=cmd
                  ).pack(fill="x", padx=14, pady=3)

    def _get_weights(self):
        total = (self.w_porpag.get() + self.w_ts.get() + self.w_ws40.get() +
                 self.w_usg.get()   + self.w_def.get() + self.w_conf.get())
        if total == 0:
            messagebox.showwarning("Weights", "All weights are zero.")
            return None
        return {
            "PORPAG": self.w_porpag.get() / total,
            "TS":     self.w_ts.get()     / total,
            "WS40":   self.w_ws40.get()   / total,
            "USG":    self.w_usg.get()    / total,
            "DEF":    self.w_def.get()    / total,
            "CONF":   self.w_conf.get()   / total,
        }

    # ──────────────────────────────────────────
    #  DATA
    # ──────────────────────────────────────────

    def _run_scores(self, force=False):
        self.status_var.set("⏳  Fetching from CBBD API…")
        self.api_dot.config(fg=GOLD)
        self.update_idletasks()
        threading.Thread(target=self._run_thread,
                         args=(force,), daemon=True).start()

    def _force_refresh(self):
        cache = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "cbbd_cache.json")
        if os.path.exists(cache):
            os.remove(cache)
        self._run_scores(force=True)

    def _run_thread(self, force=False):
        try:
            weights = self._get_weights()
            if weights is None:
                return
            df_raw = load_data(use_cache=(not force))
            df     = apply_filters(df_raw,
                                   exclude_gr=self.exclude_gr.get())
            pt.WEIGHTS.update(weights)
            df = compute_composite(df)
            self.df_full = df
            self.after(0, self._post_load)
            self.after(0, lambda: self.status_var.set(
                f"✓  {len(df):,} qualifying players  •  Season 2025-26  •  CBBD"))
            self.after(0, lambda: self.api_dot.config(fg=ACCENT))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"✗  Error: {err}"))
            self.after(0, lambda: self.api_dot.config(fg=RED))
            self.after(0, lambda: messagebox.showerror("Load Error", err))

    def _post_load(self):
        if self.df_full is None:
            return
        if "Conference" in self.df_full.columns:
            confs = sorted(self.df_full["Conference"].dropna().unique())
            self.conf_combo["values"] = ["All"] + confs
            if not self.conf_filter.get():
                self.conf_combo.set("All")
                self.conf_filter.set("All")
        # Build shade map for density colouring
        if SHADING_AVAILABLE:
            self.shade_map = build_shade_map(
                self.df_full,
                cols_high=list(SHADE_COL_MAP_HIGH.values()),
                cols_low=list(SHADE_COL_MAP_LOW.values()))
        self._apply_filters()
        if FEATURES_AVAILABLE:
            self.entries_tab.on_data_loaded()
            self.roster_tab.on_data_loaded()
            self.dest_tab.on_data_loaded()

    def _recalculate(self):
        if self.df_full is None:
            messagebox.showinfo("No Data", "Fetch data first.")
            return
        self.status_var.set("⏳  Recalculating…")
        self.update_idletasks()
        threading.Thread(target=self._recalc_thread, daemon=True).start()

    def _recalc_thread(self):
        try:
            weights = self._get_weights()
            if weights is None:
                return
            pt.WEIGHTS.update(weights)
            drop = [c for c in self.df_full.columns
                    if c.endswith("_score") or c.endswith("_raw")
                    or c in ("TS%", "WS40", "USG%", "DEF_comp",
                             "ConfMult", "PortalScore", "PORPAG_raw",
                             "BaseScore", "PRACombo", "ComboScore",
                             "FinalScore", "NILValue", "NILDisplay",
                             "KenPomRank", "KenPomMult",
                             "kp_rank", "kp_mult")]
            df = compute_composite(
                self.df_full.drop(columns=drop, errors="ignore"))
            self.df_full = df
            self.after(0, self._apply_filters)
            self.after(0, lambda: self.status_var.set(
                "✓  Scores recalculated."))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"✗  {err}"))

    # ──────────────────────────────────────────
    #  FILTERS
    # ──────────────────────────────────────────

    def _apply_filters(self):
        if self.df_full is None:
            return
        df = self.df_full.copy()

        # GR exclusion
        if self.exclude_gr.get() and "Elig" in df.columns:
            df = df[df["Elig"].str.upper() != "GR"]

        # Gone Pro exclusion (flag set via portal entries tab)
        if self.exclude_pro.get() and "GonePro" in df.columns:
            df = df[df["GonePro"] != True]

        # eligibility checkboxes
        if "Elig" in df.columns:
            allowed = [k for k, v in self.elig_vars.items() if v.get()]
            df = df[df["Elig"].isin(allowed + ["UNK", ""])]

        # position
        if "Position" in df.columns:
            allowed_pos = [k for k, v in self.pos_vars.items() if v.get()]
            df = df[df["Position"].isin(allowed_pos) |
                    df["Position"].isin(["", None]) |
                    df["Position"].isna()]

        # conference
        conf_q = self.conf_filter.get()
        if conf_q and conf_q != "All" and "Conference" in df.columns:
            df = df[df["Conference"] == conf_q]

        # min score
        df = df[df["PortalScore"] >= self.min_score.get()]

        # team search
        team_q = self.team_filter.get().strip().lower()
        if team_q:
            df = df[df["Team"].str.lower().str.contains(team_q, na=False)]

        # player search
        player_q = self.player_filter.get().strip().lower()
        if player_q:
            df = df[df["Player"].str.lower().str.contains(player_q, na=False)]

        # sort
        sort_col = self.sort_col
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=self.sort_asc,
                                na_position="last")
        else:
            df = df.sort_values("PortalScore", ascending=False)

        self.df_display = df.reset_index(drop=True)

        self.after(0, self._refresh_lb)
        self.after(0, self._refresh_team)
        self.after(0, self._refresh_conf)
        self.after(0, self._refresh_tier)
        self.after(0, self._refresh_stats)

    # ──────────────────────────────────────────
    #  REFRESH
    # ──────────────────────────────────────────

    def _refresh_lb(self):
        self.tree.delete(*self.tree.get_children())
        if self.df_display is None or len(self.df_display) == 0:
            return

        for rank, (row_idx, row) in enumerate(self.df_display.iterrows(), 1):
            score = float(row.get("PortalScore", 0))
            tier  = tier_for_score(score)
            vals  = []
            for disp, df_col, _, _ in LB_COLS:
                if disp == "Rank":
                    vals.append(rank); continue
                if disp == "Tier":
                    vals.append(tier); continue
                if df_col not in row.index:
                    vals.append(""); continue
                v = row[df_col]
                try:
                    if pd.isna(v):
                        vals.append("—"); continue
                    if disp == "G":
                        vals.append(int(float(v)))
                    elif disp == "S2: NIL":
                        fv = float(v)
                        if fv >= 1_000_000:
                            vals.append(f"${fv/1_000_000:.2f}M")
                        elif fv >= 1_000:
                            vals.append(f"${fv/1_000:.0f}K")
                        else:
                            vals.append(f"${fv:,.0f}")
                    elif disp == "KP Rank":
                        vals.append(f"{int(float(v))}")
                    elif disp in ("S1: Score", "S2: Final", "Base",
                                  "PRA", "PER", "PORPAG", "KP ×"):
                        vals.append(f"{float(v):.2f}")
                    elif disp in ("TS%", "USG%", "eFG%", "PTS",
                                  "WS/40", "DEF"):
                        vals.append(f"{float(v):.1f}")
                    elif disp == "S1: Tier":
                        vals.append(str(v))
                    else:
                        vals.append(str(v))
                except (ValueError, TypeError):
                    vals.append(str(v) if pd.notna(v) else "")
            self.tree.insert("", "end", tags=(tier,), values=vals)

        # Rebuild heatmap if shading is on
        if self.density_on.get() and SHADING_AVAILABLE:
            self.after(10, self._draw_heatmap)
        else:
            self.heatmap_canvas.config(height=0)
            self.heatmap_frame.config(height=0)

    def _draw_heatmap(self):
        """
        Draw heatmap panel below the leaderboard.
        Shows top-50 players × key numeric columns as coloured cells.
        Red → Yellow → Green per column, scrollable horizontally.
        """
        if not SHADING_AVAILABLE or self.df_display is None:
            return
        try:
            from formula_engine import (build_shade_map,
                SHADE_COL_MAP_HIGH, SHADE_COL_MAP_LOW)
        except ImportError:
            return

        df = self.df_display.head(50)
        if len(df) == 0:
            return

        hm_cols = [
            ("TS%",    "TS%"),
            ("eFG%",   "eFGPct"),
            ("PORPAG", "PORPAG"),
            ("WS",     "WS_total"),
            ("USG%",   "USG%"),
            ("KP Rank","KenPomRank"),
            ("Base",   "BaseScore"),
            ("PRA",    "PRACombo"),
            ("Final",  "FinalScore"),
            ("NIL",    "NILValue"),
            ("Score",  "PortalScore"),
        ]
        invert_cols = {"KenPomRank"}

        shade = build_shade_map(
            df,
            cols_high=[c for _, c in hm_cols if c not in invert_cols],
            cols_low= [c for _, c in hm_cols if c in invert_cols])

        c       = self.heatmap_canvas
        cell_w  = 68    # wider cells — readable values
        cell_h  = 22    # taller rows — matches treeview row height
        label_h = 22    # header row height
        name_w  = 160   # player name column width
        n_rows  = len(df)
        n_cols  = len(hm_cols)
        total_h = label_h + n_rows * cell_h + 6
        total_w = name_w + n_cols * cell_w + 20

        c.config(height=total_h, scrollregion=(0, 0, total_w, total_h))
        self.heatmap_frame.config(height=total_h)
        c.delete("all")

        # Background
        c.create_rectangle(0, 0, total_w, total_h, fill=PANEL, outline="")

        # Column headers
        c.create_text(8, label_h // 2,
                      text="▦ Heatmap  |  Top 50 Players",
                      fill=TEXT, font=("Segoe UI", 8, "bold"), anchor="w")
        for ci, (lbl, _) in enumerate(hm_cols):
            x = name_w + ci * cell_w + cell_w // 2
            c.create_rectangle(name_w + ci * cell_w, 0,
                               name_w + (ci + 1) * cell_w, label_h,
                               fill=BORDER, outline="")
            c.create_text(x, label_h // 2, text=lbl,
                          fill=TEXT, font=("Segoe UI", 8, "bold"),
                          anchor="center")

        # Rows
        for ri, (_, row) in enumerate(df.iterrows()):
            y    = label_h + ri * cell_h
            bg   = PANEL if ri % 2 == 0 else "#1c2128"

            # Player name cell
            c.create_rectangle(0, y, name_w, y + cell_h,
                               fill=bg, outline="")
            name = str(row.get("Player", ""))
            team = str(row.get("Team",   ""))
            c.create_text(8, y + cell_h // 2,
                          text=f"{ri+1:>2}. {name[:22]}",
                          fill=TEXT, font=("Segoe UI", 8), anchor="w")

            # Data cells
            for ci, (lbl, df_col) in enumerate(hm_cols):
                x      = name_w + ci * cell_w
                colour = shade.get(df_col, {}).get(row.name, BORDER)

                c.create_rectangle(x + 1, y + 1,
                                   x + cell_w - 1, y + cell_h - 1,
                                   fill=colour, outline="")

                # Value text — black on coloured bg
                try:
                    v = row.get(df_col)
                    if pd.notna(v):
                        fv = float(v)
                        if df_col == "NILValue":
                            txt = (f"${fv/1e6:.1f}M" if fv >= 1e6
                                   else f"${fv/1000:.0f}K")
                        elif df_col == "KenPomRank":
                            txt = f"#{int(fv)}"
                        elif df_col in ("BaseScore","PRACombo",
                                        "FinalScore","PortalScore"):
                            txt = f"{fv:.1f}"
                        else:
                            txt = f"{fv:.1f}"
                        c.create_text(x + cell_w // 2, y + cell_h // 2,
                                      text=txt, fill="#0d1117",
                                      font=("Segoe UI", 8, "bold"),
                                      anchor="center")
                except Exception:
                    pass

        # Legend strip at bottom
        ly = total_h - 5
        c.create_text(name_w, ly, text="Low",
                      fill="#da3633", font=("Segoe UI", 7), anchor="w")
        c.create_text(name_w + 30, ly, text="◀──",
                      fill=SUBTEXT, font=("Segoe UI", 7), anchor="w")
        c.create_text(name_w + 60, ly, text="Mid",
                      fill="#d29922", font=("Segoe UI", 7), anchor="w")
        c.create_text(name_w + 88, ly, text="──▶",
                      fill=SUBTEXT, font=("Segoe UI", 7), anchor="w")
        c.create_text(name_w + 116, ly, text="High",
                      fill="#3fb950", font=("Segoe UI", 7), anchor="w")
        legend_x = total_w - 110
        c.create_text(legend_x, 8, text="Low", fill="#da3633",
                      font=("Segoe UI", 7), anchor="w")
        c.create_text(legend_x + 28, 8, text="Mid", fill="#d29922",
                      font=("Segoe UI", 7), anchor="w")
        c.create_text(legend_x + 56, 8, text="High", fill="#3fb950",
                      font=("Segoe UI", 7), anchor="w")

    def _refresh_team(self):
        self.team_tree.delete(*self.team_tree.get_children())
        if self.df_display is None or len(self.df_display) == 0:
            return
        df = self.df_display
        grp = (df.groupby("Team")
                 .apply(lambda g: pd.Series({
                     "Conference": g["Conference"].iloc[0]
                                  if "Conference" in g.columns else "",
                     "Players":    len(g),
                     "AvgScore":   g["PortalScore"].mean(),
                     "BestPlayer": g.loc[g["PortalScore"].idxmax(), "Player"],
                     "BestScore":  g["PortalScore"].max(),
                 }))
                 .reset_index()
                 .sort_values("AvgScore", ascending=False))
        for _, row in grp.iterrows():
            self.team_tree.insert("", "end", values=(
                row["Team"], row["Conference"], int(row["Players"]),
                f"{row['AvgScore']:.2f}", row["BestPlayer"],
                f"{row['BestScore']:.2f}"))

    def _refresh_conf(self):
        self.conf_tree.delete(*self.conf_tree.get_children())
        if self.df_display is None or "Conference" not in self.df_display.columns:
            return
        df   = self.df_display
        rows = []
        for conf, grp in df.groupby("Conference"):
            top = grp.loc[grp["PortalScore"].idxmax()]
            rows.append((
                conf, len(grp),
                grp["PortalScore"].mean(),
                len(grp[grp["PortalScore"] >= 85]),
                len(grp[(grp["PortalScore"] >= 70) & (grp["PortalScore"] < 85)]),
                f"{top['Player']} ({top['PortalScore']:.1f})",
            ))
        rows.sort(key=lambda r: r[2], reverse=True)
        for r in rows:
            self.conf_tree.insert("", "end", values=(
                r[0], int(r[1]), f"{r[2]:.2f}",
                int(r[3]), int(r[4]), r[5]))

    def _refresh_tier(self):
        for w in self.tier_frame.winfo_children():
            w.destroy()
        if self.df_display is None:
            return
        tiers = [
            ("🔵 ELITE",  85, 101, TIER_COLOURS["ELITE"]),
            ("🟢 HIGH",   70,  85, TIER_COLOURS["HIGH"]),
            ("🟡 SOLID",  55,  70, TIER_COLOURS["SOLID"]),
            ("🟠 FRINGE", 40,  55, TIER_COLOURS["FRINGE"]),
            ("⚫ DEPTH",   0,  40, TIER_COLOURS["DEPTH"]),
        ]
        df = self.df_display
        for label, lo, hi, colour in tiers:
            tier_df = df[(df["PortalScore"] >= lo) & (df["PortalScore"] < hi)]
            card = tk.Frame(self.tier_frame, bg=PANEL, relief="flat")
            card.pack(fill="x", pady=5, padx=4)
            hdr = tk.Frame(card, bg=PANEL)
            hdr.pack(fill="x", padx=14, pady=(8, 4))
            tk.Label(hdr, text=label, bg=PANEL, fg=colour,
                     font=("Segoe UI", 12, "bold")).pack(side="left")
            tk.Label(hdr, text=f"{len(tier_df):,} players",
                     bg=PANEL, fg=SUBTEXT,
                     font=("Segoe UI", 10)).pack(side="left", padx=10)
            if len(tier_df) > 0:
                top10 = tier_df.sort_values(
                    "PortalScore", ascending=False).head(10)
                for _, r in top10.iterrows():
                    pos  = r.get("Position", "")
                    conf = r.get("Conference", "")
                    line = (f"  {str(r['Player']):<28}  "
                            f"{str(r['Team']):<22}  "
                            f"{str(conf):<14}  {str(pos):<3}  "
                            f"{float(r['PortalScore']):.1f}")
                    tk.Label(card, text=line, bg=PANEL, fg=TEXT,
                             font=("Courier New", 9),
                             anchor="w").pack(fill="x", padx=14)
            tk.Label(card, text="", bg=PANEL).pack(pady=3)

    def _refresh_stats(self):
        if self.df_display is None:
            return
        df  = self.df_display
        tot = self.df_full
        self.stat_total.config(text=f"{len(tot):,}" if tot is not None else "—")
        self.stat_shown.config(text=f"{len(df):,}")
        self.stat_avg.config(
            text=f"{df['PortalScore'].mean():.1f}" if len(df) else "—")
        self.stat_top.config(
            text=f"{df['PortalScore'].max():.1f}" if len(df) else "—")
        elite = len(df[df["PortalScore"] >= 85])
        high  = len(df[(df["PortalScore"] >= 70) & (df["PortalScore"] < 85)])
        teams = df["Team"].nunique() if "Team" in df.columns else 0
        self.stat_elite.config(text=str(elite),
                               fg=TIER_COLOURS["ELITE"] if elite else SUBTEXT)
        self.stat_high.config(text=str(high),
                              fg=TIER_COLOURS["HIGH"] if high else SUBTEXT)
        self.stat_teams.config(text=str(teams))

    def _quick_sort(self, df_col):
        """Sort by a df column directly (from quick-sort buttons)."""
        if self.sort_col == df_col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = df_col
            self.sort_asc = False
        if self.df_display is not None:
            self._apply_filters()

    def _sort_by(self, disp_col):
        df_col = next((c[1] for c in LB_COLS if c[0] == disp_col), disp_col)
        if self.sort_col == df_col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = df_col
            self.sort_asc = False
        # Update heading arrows
        for d, _, _, _ in LB_COLS:
            arrow = ""
            mapped = next((c[1] for c in LB_COLS if c[0] == d), d)
            if mapped == self.sort_col:
                arrow = " ▲" if self.sort_asc else " ▼"
            self.tree.heading(d, text=d + arrow)
        if self.df_display is not None:
            self._apply_filters()

    def _import_kenpom(self):
        """Let user select their KenPom CSV export."""
        path = filedialog.askopenfilename(
            title="Select KenPom Rankings CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            from formula_engine import load_kenpom, compute_custom_score
            df_kp, msg = load_kenpom(path)
            if df_kp is None:
                messagebox.showerror("KenPom Import", msg)
                return
            # Copy to working directory so it auto-loads next time
            import shutil
            dest = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "kenpom_rankings.csv")
            shutil.copy2(path, dest)
            self.kenpom_status.set(f"KenPom: {len(df_kp)} teams ✓")
            # Re-score with KenPom if data is loaded
            if self.df_full is not None:
                self.status_var.set("⏳ Applying KenPom multipliers…")
                self.update_idletasks()
                def _apply():
                    try:
                        df = compute_custom_score(self.df_full, df_kp)
                        self.df_full = df
                        self.after(0, self._apply_filters)
                        self.after(0, lambda: self.status_var.set(
                            f"✓ KenPom applied — {len(df_kp)} teams matched"))
                    except Exception as e:
                        self.after(0, lambda: self.status_var.set(
                            f"✗ KenPom error: {e}"))
                threading.Thread(target=_apply, daemon=True).start()
            else:
                messagebox.showinfo("KenPom Imported",
                    f"{msg}\nLoad CBBD data first to apply multipliers.")
        except ImportError:
            messagebox.showerror("Missing File",
                "formula_engine.py must be in the same folder.")

    def _export_csv(self):
        if self.df_display is None or len(self.df_display) == 0:
            messagebox.showinfo("Export", "No data to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"portal_scores_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
        if not path:
            return
        export_cols = ["Player", "Team", "Conference", "Position",
                       "G", "PTS", "TS%", "eFGPct", "PORPAG", "WS_total",
                       "USG%", "DEF_comp", "ASTtoTO",
                       "OrtgPlayer", "DrtgPlayer", "PortalScore",
                       "BaseScore", "PRACombo", "ComboScore",
                       "KenPomRank", "KenPomMult", "FinalScore", "NILValue"]
        if "Elig" in self.df_display.columns:
            export_cols.insert(3, "Elig")
        avail = [c for c in export_cols if c in self.df_display.columns]
        self.df_display.sort_values(
            "PortalScore", ascending=False)[avail].to_csv(path, index=False)
        messagebox.showinfo("Exported",
            f"Saved {len(self.df_display):,} players to:\n{path}")
        self.status_var.set(
            f"✓  Exported {len(self.df_display):,} players → "
            f"{os.path.basename(path)}")


if __name__ == "__main__":
    if CBBD_API_KEY == "YOUR_API_KEY_HERE":
        messagebox.showerror("API Key Missing",
            "Set CBBD_API_KEY in portal_tracker_cbbd.py before launching.")
        sys.exit(1)
    app = PortalTrackerApp()
    app.mainloop()
