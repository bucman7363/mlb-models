"""Sports betting research dashboard — one tab per game.

Layout per tab (top to bottom):
  1. Game header: teams, records, streak, L10, days rest
  2. Starting pitcher matchup: name, W-L, IP, ERA, WHIP, (moneyline/O-U manual cells)
  3. Pitcher stats: ERA / K/G / Hits/G / BB/G / HR/9 — Season | L5 | L10 both SPs
  4. Team batting: AVG / R/G / H/G / K% / BB% — Season | L5 | L10
  5. Bullpen: ERA / K/G / WHIP — Season
  6. Confidence scores: ML lean / Run Line lean / Total lean (0–10 + PLAY/LEAN label)

Conditional formatting (green/yellow/red) applied via Sheets API batchUpdate.

Spreadsheet ID: credentials/duel_sheets_id.txt
Usage:
    source venv/bin/activate
    python game_duel_builder.py [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

from src import db, mlb_api, sheets_sync
from src.game_stats import (
    get_days_rest, get_pitcher_splits,
    get_team_batting_splits, get_team_record,
)

DUEL_ID_PATH = Path(__file__).resolve().parent / "credentials" / "duel_sheets_id.txt"

# ── Colors (Sheets API RGB 0-1 floats) ──────────────────────────────────────
_GREEN  = {"red": 0.18, "green": 0.65, "blue": 0.33}
_YELLOW = {"red": 1.00, "green": 0.90, "blue": 0.40}
_RED    = {"red": 0.92, "green": 0.26, "blue": 0.26}

# ── Gradient specs per stat ──────────────────────────────────────────────────
# invert=True  → higher value = green  (min=red, max=green)
# invert=False → lower value  = green  (min=green, max=red)
_GRAD = {
    "ERA":     dict(lo=2.5,   mid=4.0,  hi=6.0,   invert=False),
    "K/G":     dict(lo=4.0,   mid=6.5,  hi=10.0,  invert=True),
    "Hits/G":  dict(lo=5.0,   mid=7.5,  hi=11.0,  invert=False),
    "BB/G":    dict(lo=1.5,   mid=3.0,  hi=5.0,   invert=False),
    "HR/9":    dict(lo=0.5,   mid=1.2,  hi=2.5,   invert=False),
    "AVG":     dict(lo=0.220, mid=0.255,hi=0.305,  invert=True),
    "R/G":     dict(lo=2.5,   mid=4.5,  hi=7.0,   invert=True),
    "H/G":     dict(lo=6.0,   mid=8.0,  hi=11.0,  invert=True),
    "K%":      dict(lo=0.12,  mid=0.22, hi=0.32,  invert=False),
    "BB%":     dict(lo=0.06,  mid=0.10, hi=0.15,  invert=True),
    "BP_ERA":  dict(lo=2.5,   mid=4.0,  hi=6.0,   invert=False),
    "BP_K/G":  dict(lo=4.0,   mid=7.0,  hi=11.0,  invert=True),
    "BP_WHIP": dict(lo=1.0,   mid=1.35, hi=1.8,   invert=False),
    "CONF":    dict(lo=0.0,   mid=5.0,  hi=10.0,  invert=True),
}

# Column indices (0-based): A=0 … H=7
#  A: label | B: Away Season | C: Away L5 | D: Away L10 | E: gap | F: Home Season | G: Home L5 | H: Home L10
_NC = 9  # total columns

_COL_AWAY_SSN = 1
_COL_AWAY_L5  = 2
_COL_AWAY_L10 = 3
_COL_HOME_SSN = 5
_COL_HOME_L5  = 6
_COL_HOME_L10 = 7


def _r(*cells) -> list:
    row = list(cells)
    while len(row) < _NC:
        row.append("")
    return row[:_NC]


def _blank() -> list:
    return [""] * _NC


def _fmt(v, decimals=3, pct=False):
    if v is None:
        return "N/A"
    if pct and isinstance(v, float):
        return f"{v:.1%}"
    if isinstance(v, float):
        return round(v, decimals)
    return v


def _s(splits: dict, key: str, bucket: str = "season"):
    return splits.get(bucket, {}).get(key)


# ── Confidence score formula ─────────────────────────────────────────────────

def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def compute_confidence(
    away_sp_ssn: dict, home_sp_ssn: dict,
    away_batting_ssn: dict, home_batting_ssn: dict,
    away_batting_l5: dict, home_batting_l5: dict,
    away_bp: dict, home_bp: dict,
) -> dict:
    """
    Returns ml_score, rl_score, total_score (all 0–10) plus PLAY/LEAN labels.

    Weights:
      ML:  SP ERA 25% · SP xFIP 15% · Team R/G 30% · BP ERA 20% · home adj 10%
      RL:  same as ML but threshold shifted (needs bigger edge)
      TOT: combined R/G 50% · SP K% 30% · BP K/G 20%
    """
    def safe(d, k, default=None):
        v = (d or {}).get(k)
        return v if v is not None else default

    # ── Moneyline lean (positive = away edge) ────────────────────────────────
    lg_era  = 4.20
    sp_era_edge  = _clamp((safe(home_sp_ssn, "era",  lg_era) - safe(away_sp_ssn, "era",  lg_era)) / 3.0)
    sp_xfip_edge = _clamp((safe(home_sp_ssn, "xfip", lg_era) - safe(away_sp_ssn, "xfip", lg_era)) / 3.0)

    lg_rpg   = 4.50
    away_rpg = safe(away_batting_ssn, "rpg", lg_rpg)
    home_rpg = safe(home_batting_ssn, "rpg", lg_rpg)
    rpg_edge = _clamp((away_rpg - home_rpg) / 3.0)

    # L5 R/G tiebreaker (recent form)
    away_l5_rpg = safe(away_batting_l5, "rpg", away_rpg)
    home_l5_rpg = safe(home_batting_l5, "rpg", home_rpg)
    rpg_l5_edge = _clamp((away_l5_rpg - home_l5_rpg) / 3.0)

    away_bp_era = safe(away_bp, "era", lg_era)
    home_bp_era = safe(home_bp, "era", lg_era)
    bp_edge = _clamp((home_bp_era - away_bp_era) / 2.0)

    home_adj = -0.10  # home field factor

    ml_raw = (
        0.25 * sp_era_edge +
        0.15 * sp_xfip_edge +
        0.25 * rpg_edge +
        0.10 * rpg_l5_edge +
        0.15 * bp_edge +
        home_adj
    )
    ml_score = round(max(0.0, min(10.0, 5.0 + ml_raw * 5.0)), 1)

    if   ml_score >= 6.5: ml_label = f"PLAY AWAY"
    elif ml_score <= 3.5: ml_label = f"PLAY HOME"
    else:                  ml_label = "—"

    # Run line: needs a bigger edge (±1.5 runs)
    rl_score = ml_score
    if   rl_score >= 7.0: rl_label = "PLAY AWAY -1.5"
    elif rl_score <= 3.0: rl_label = "PLAY HOME -1.5"
    else:                  rl_label = "—"

    # ── Total lean ───────────────────────────────────────────────────────────
    lg_combined = 9.0
    combined_rpg = away_rpg + home_rpg
    scoring_edge = _clamp((combined_rpg - lg_combined) / 5.0)

    lg_k_pct = 0.22
    away_kpct = safe(away_sp_ssn, "k_pct", lg_k_pct)
    home_kpct = safe(home_sp_ssn, "k_pct", lg_k_pct)
    avg_k_pct = (away_kpct + home_kpct) / 2
    k_edge = _clamp(-(avg_k_pct - lg_k_pct) * 4.0)  # high K% → under

    away_bp_kpg = safe(away_bp, "k_per_g", 7.5)
    home_bp_kpg = safe(home_bp, "k_per_g", 7.5)
    avg_bp_kpg = (away_bp_kpg + home_bp_kpg) / 2
    bp_k_edge = _clamp(-(avg_bp_kpg - 7.5) / 4.0)   # high BP K/G → under

    tot_raw = 0.50 * scoring_edge + 0.30 * k_edge + 0.20 * bp_k_edge
    total_score = round(max(0.0, min(10.0, 5.0 + tot_raw * 5.0)), 1)

    if   total_score >= 6.5: total_label = "LEAN OVER"
    elif total_score <= 3.5: total_label = "LEAN UNDER"
    else:                     total_label = "—"

    return {
        "ml_score": ml_score, "ml_label": ml_label,
        "rl_score": rl_score, "rl_label": rl_label,
        "total_score": total_score, "total_label": total_label,
    }


# ── Tab row builder ──────────────────────────────────────────────────────────

def build_dashboard_tab(
    con, away: dict, home: dict, stat_date: str, game_date: str
) -> tuple[list[list], list[dict]]:
    """
    Returns (rows, format_specs).
    rows: list of lists ready to write to gspread.
    format_specs: list of {row, col, stat_key} for conditional formatting.
    """
    season = int(game_date[:4])
    away_id   = away["team_id"]
    home_id   = home["team_id"]
    away_name = away["team_name"]
    home_name = home["team_name"]
    away_sp_id   = away.get("probable_pitcher_id")
    home_sp_id   = home.get("probable_pitcher_id")
    away_sp_name = away.get("probable_pitcher_name") or "TBD"
    home_sp_name = home.get("probable_pitcher_name") or "TBD"

    # ── Fetch data ──────────────────────────────────────────────────────────
    away_rec  = get_team_record(away_id, game_date)
    home_rec  = get_team_record(home_id, game_date)
    away_rest = get_days_rest(away_id, game_date)
    home_rest = get_days_rest(home_id, game_date)

    away_sp_splits = get_pitcher_splits(away_sp_id, season) if away_sp_id else {}
    home_sp_splits = get_pitcher_splits(home_sp_id, season) if home_sp_id else {}

    away_bat = get_team_batting_splits(away_id, season, game_date)
    home_bat = get_team_batting_splits(home_id, season, game_date)

    # SP season stats from DB
    def _sp_row(pid):
        if not pid:
            return [None] * 8
        return (con.execute(
            "SELECT era, whip, xfip, k_pct, bb_pct, innings_pitched, strikeouts, walks "
            "FROM pitcher_stats WHERE player_id = ? AND stat_date <= ? "
            "ORDER BY stat_date DESC LIMIT 1", [pid, stat_date]
        ).fetchone() or [None] * 8)

    away_sp_db = _sp_row(away_sp_id)
    home_sp_db = _sp_row(home_sp_id)

    def sp_db(row, i): return row[i] if row else None
    AWAY, HOME = away_sp_db, home_sp_db

    # Bullpen: relievers (IP < 40 for season) aggregated per team
    def team_bullpen(team_id):
        latest = con.execute(
            "SELECT MAX(stat_date) FROM pitcher_stats WHERE team_id = ? AND stat_date <= ?",
            [team_id, stat_date]
        ).fetchone()[0]
        if not latest:
            return {}
        rows = con.execute(
            "SELECT era, innings_pitched, whiff_pct, strikeouts, walks FROM pitcher_stats "
            "WHERE team_id = ? AND stat_date = ? AND innings_pitched < 40",
            [team_id, latest],
        ).fetchall()
        if not rows:
            return {}
        total_ip = sum(r[1] or 0 for r in rows)
        total_er = sum(((r[0] or 4.5) * (r[1] or 0) / 9) for r in rows)
        total_k  = sum(r[3] or 0 for r in rows)
        n = len(rows)
        return {
            "era":     round(total_er / total_ip * 9, 2) if total_ip else None,
            "k_per_g": round(total_k / n, 1) if n else None,
            "whip":    None,  # not easily aggregated
        }

    away_bp = team_bullpen(away_id)
    home_bp = team_bullpen(home_id)

    # SP ssn dict for confidence
    away_sp_ssn = {
        "era":   sp_db(AWAY, 0), "whip": sp_db(AWAY, 1), "xfip": sp_db(AWAY, 2),
        "k_pct": sp_db(AWAY, 3), "bb_pct": sp_db(AWAY, 4),
    }
    home_sp_ssn = {
        "era":   sp_db(HOME, 0), "whip": sp_db(HOME, 1), "xfip": sp_db(HOME, 2),
        "k_pct": sp_db(HOME, 3), "bb_pct": sp_db(HOME, 4),
    }

    conf = compute_confidence(
        away_sp_ssn, home_sp_ssn,
        away_bat.get("season", {}), home_bat.get("season", {}),
        away_bat.get("l5", {}),     home_bat.get("l5", {}),
        away_bp, home_bp,
    )

    # ── Build rows ──────────────────────────────────────────────────────────
    rows: list[list] = []
    fmt:  list[dict] = []  # {row, col, stat_key}

    def row_idx():
        return len(rows)

    def add(row):
        rows.append(row)

    def add_stat_row(label, away_ssn, away_l5, away_l10, home_ssn, home_l5, home_l10, stat_key=None):
        ri = row_idx()
        add(_r(label,
               _fmt(away_ssn), _fmt(away_l5), _fmt(away_l10), "",
               _fmt(home_ssn), _fmt(home_l5), _fmt(home_l10)))
        if stat_key:
            for col in (_COL_AWAY_SSN, _COL_AWAY_L5, _COL_AWAY_L10,
                        _COL_HOME_SSN, _COL_HOME_L5, _COL_HOME_L10):
                fmt.append({"row": ri, "col": col, "stat_key": stat_key})

    # ── Section 1: GAME HEADER ───────────────────────────────────────────────
    add(_r(f"{'━'*60}"))
    add(_r(
        "",
        f"  {away_name}",
        f"  {away_rec['wins']}-{away_rec['losses']}",
        f"  {away_rec['streak']}",
        f"  L10: {away_rec['l10']}",
        f"  Rest: {away_rest}d",
        f"  {home_name}",
        f"  {home_rec['wins']}-{home_rec['losses']}",
        f"  {home_rec['streak']}"
    ))
    add(_r("", "", "", "", "", f"  L10: {home_rec['l10']}", "", f"  Rest: {home_rest}d"))
    add(_r(f"  {game_date}  |  stats as of {stat_date}"))
    add(_blank())

    # ── Section 2: STARTING PITCHER MATCHUP ─────────────────────────────────
    add(_r("━━━ STARTING PITCHERS ━━━"))
    add(_r("",         f"  {away_name}", "", "", "", f"  {home_name}"))
    add(_r("Name",     away_sp_name,  "", "", "", home_sp_name))
    away_games = away_sp_splits.get("season", {}).get("games", "?")
    home_games = home_sp_splits.get("season", {}).get("games", "?")
    add(_r("GS (season)", away_games, "", "", "", home_games))
    add(_r("Season IP", _fmt(sp_db(AWAY,5)), "", "", "", _fmt(sp_db(HOME,5))))
    add(_r("ERA",       _fmt(sp_db(AWAY,0)), "", "", "", _fmt(sp_db(HOME,0))))
    add(_r("WHIP",      _fmt(sp_db(AWAY,1)), "", "", "", _fmt(sp_db(HOME,1))))
    add(_r("xFIP",      _fmt(sp_db(AWAY,2)), "", "", "", _fmt(sp_db(HOME,2))))
    add(_r("Moneyline", "[enter]",           "", "", "", "[enter]",  "← enter odds manually"))
    add(_r("O/U Line",  "[enter]",           "", "", "", "[enter]",  "← enter O/U manually"))
    add(_blank())

    # ── Section 3: PITCHER STATS ─────────────────────────────────────────────
    add(_r("━━━ PITCHER STATS ━━━"))
    add(_r("",
           f"  {away_sp_name}",  "", "", "",
           f"  {home_sp_name}"))
    add(_r("STAT",
           "Away Season", "Away L5", "Away L10", "",
           "Home Season", "Home L5", "Home L10"))

    def sp_stat(splits, key, bucket):
        return _s(splits, key, bucket)

    add_stat_row("ERA",
        sp_stat(away_sp_splits,"era","season"), sp_stat(away_sp_splits,"era","l5"), sp_stat(away_sp_splits,"era","l10"),
        sp_stat(home_sp_splits,"era","season"), sp_stat(home_sp_splits,"era","l5"), sp_stat(home_sp_splits,"era","l10"),
        "ERA")
    add_stat_row("K/G",
        sp_stat(away_sp_splits,"k_per_g","season"), sp_stat(away_sp_splits,"k_per_g","l5"), sp_stat(away_sp_splits,"k_per_g","l10"),
        sp_stat(home_sp_splits,"k_per_g","season"), sp_stat(home_sp_splits,"k_per_g","l5"), sp_stat(home_sp_splits,"k_per_g","l10"),
        "K/G")
    add_stat_row("Hits/G",
        sp_stat(away_sp_splits,"hits_per_g","season"), sp_stat(away_sp_splits,"hits_per_g","l5"), sp_stat(away_sp_splits,"hits_per_g","l10"),
        sp_stat(home_sp_splits,"hits_per_g","season"), sp_stat(home_sp_splits,"hits_per_g","l5"), sp_stat(home_sp_splits,"hits_per_g","l10"),
        "Hits/G")
    add_stat_row("BB/G",
        sp_stat(away_sp_splits,"bb_per_g","season"), sp_stat(away_sp_splits,"bb_per_g","l5"), sp_stat(away_sp_splits,"bb_per_g","l10"),
        sp_stat(home_sp_splits,"bb_per_g","season"), sp_stat(home_sp_splits,"bb_per_g","l5"), sp_stat(home_sp_splits,"bb_per_g","l10"),
        "BB/G")
    add_stat_row("HR/9",
        sp_stat(away_sp_splits,"hr_per_9","season"), sp_stat(away_sp_splits,"hr_per_9","l5"), sp_stat(away_sp_splits,"hr_per_9","l10"),
        sp_stat(home_sp_splits,"hr_per_9","season"), sp_stat(home_sp_splits,"hr_per_9","l5"), sp_stat(home_sp_splits,"hr_per_9","l10"),
        "HR/9")
    add(_blank())

    # ── Section 4: TEAM BATTING ──────────────────────────────────────────────
    add(_r("━━━ TEAM BATTING ━━━"))
    add(_r("",
           f"  {away_name}", "", "", "",
           f"  {home_name}"))
    add(_r("STAT", "Away Season", "Away L5", "Away L10", "", "Home Season", "Home L5", "Home L10"))

    add_stat_row("AVG",
        _s(away_bat,"avg","season"), _s(away_bat,"avg","l5"), _s(away_bat,"avg","l10"),
        _s(home_bat,"avg","season"), _s(home_bat,"avg","l5"), _s(home_bat,"avg","l10"),
        "AVG")
    add_stat_row("R/G",
        _s(away_bat,"rpg","season"), _s(away_bat,"rpg","l5"), _s(away_bat,"rpg","l10"),
        _s(home_bat,"rpg","season"), _s(home_bat,"rpg","l5"), _s(home_bat,"rpg","l10"),
        "R/G")
    add_stat_row("H/G",
        _s(away_bat,"hpg","season"), _s(away_bat,"hpg","l5"), _s(away_bat,"hpg","l10"),
        _s(home_bat,"hpg","season"), _s(home_bat,"hpg","l5"), _s(home_bat,"hpg","l10"),
        "H/G")
    add_stat_row("K%",
        _s(away_bat,"k_pct","season"), _s(away_bat,"k_pct","l5"), _s(away_bat,"k_pct","l10"),
        _s(home_bat,"k_pct","season"), _s(home_bat,"k_pct","l5"), _s(home_bat,"k_pct","l10"),
        "K%")
    add_stat_row("BB%",
        _s(away_bat,"bb_pct","season"), _s(away_bat,"bb_pct","l5"), _s(away_bat,"bb_pct","l10"),
        _s(home_bat,"bb_pct","season"), _s(home_bat,"bb_pct","l5"), _s(home_bat,"bb_pct","l10"),
        "BB%")
    add(_blank())

    # ── Section 5: BULLPEN ───────────────────────────────────────────────────
    add(_r("━━━ BULLPEN (season) ━━━"))
    add(_r("STAT", f"  {away_name}", "", "", "", f"  {home_name}"))
    add(_r("ERA",   _fmt(away_bp.get("era")),     "", "", "", _fmt(home_bp.get("era"))))
    add(_r("K/G",   _fmt(away_bp.get("k_per_g")), "", "", "", _fmt(home_bp.get("k_per_g"))))

    # color the ERA cells
    bp_era_row = len(rows) - 2
    for col in (_COL_AWAY_SSN, _COL_HOME_SSN):
        fmt.append({"row": bp_era_row, "col": col, "stat_key": "BP_ERA"})
    bp_kg_row = len(rows) - 1
    for col in (_COL_AWAY_SSN, _COL_HOME_SSN):
        fmt.append({"row": bp_kg_row, "col": col, "stat_key": "BP_K/G"})
    add(_blank())

    # ── Section 6: CONFIDENCE SCORES ────────────────────────────────────────
    add(_r("━━━ CONFIDENCE SCORES ━━━"))
    add(_r("", "Score /10", "Play / Lean", "", "", "  Methodology",
           "SP ERA 25% · xFIP 15% · R/G 30%+10%L5 · BP ERA 20% · HFA -10%"))

    def add_conf_row(label, score, play_label):
        ri = row_idx()
        add(_r(label, score, play_label))
        fmt.append({"row": ri, "col": 1, "stat_key": "CONF"})

    add_conf_row("Moneyline Lean", conf["ml_score"],    conf["ml_label"])
    add_conf_row("Run Line Lean",  conf["rl_score"],    conf["rl_label"])
    add_conf_row("Total Lean",     conf["total_score"], conf["total_label"])
    add(_blank())

    return rows, fmt


# ── Conditional formatting ───────────────────────────────────────────────────

def _gradient_rule(sheet_id: int, row: int, col: int, stat_key: str) -> dict | None:
    spec = _GRAD.get(stat_key)
    if not spec:
        return None
    lo, mid, hi = spec["lo"], spec["mid"], spec["hi"]
    invert = spec["invert"]
    min_color = _RED   if invert else _GREEN
    max_color  = _GREEN if invert else _RED
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": row, "endRowIndex": row + 1,
                    "startColumnIndex": col, "endColumnIndex": col + 1,
                }],
                "gradientRule": {
                    "minpoint": {"colorStyle": {"rgbColor": min_color},
                                 "type": "NUMBER", "value": str(lo)},
                    "midpoint": {"colorStyle": {"rgbColor": _YELLOW},
                                 "type": "NUMBER", "value": str(mid)},
                    "maxpoint": {"colorStyle": {"rgbColor": max_color},
                                 "type": "NUMBER", "value": str(hi)},
                },
            },
            "index": 0,
        }
    }


def apply_formatting(sh, tab_title: str, fmt_specs: list[dict]) -> None:
    ws = sh.worksheet(tab_title)
    requests_body = []
    for spec in fmt_specs:
        rule = _gradient_rule(ws.id, spec["row"], spec["col"], spec["stat_key"])
        if rule:
            requests_body.append(rule)
    if requests_body:
        sh.batch_update({"requests": requests_body})


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()

    if not DUEL_ID_PATH.exists():
        print(f"Save spreadsheet ID to: {DUEL_ID_PATH}")
        return

    con = db.get_connection(read_only=True)

    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM pitcher_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])
    if not stat_date or stat_date == "None":
        print(f"No pitcher stats in DB for or before {date_str}")
        con.close()
        return

    season = int(date_str[:4])
    teams  = mlb_api.get_teams(season)
    team_map = {t["id"]: t["name"] for t in teams}
    abbr_map = {t["id"]: t.get("abbreviation", t["name"][:3].upper()) for t in teams}

    games = mlb_api.get_schedule_with_starters(date_str)
    game_groups: dict[int, list] = {}
    for g in games:
        game_groups.setdefault(g["game_pk"], []).append(g)

    gc = sheets_sync._get_client()
    sh = gc.open_by_key(DUEL_ID_PATH.read_text().strip())

    matchups = [s for s in game_groups.values() if len(s) == 2]
    print(f"Building {len(matchups)} dashboard tabs for {date_str} (stats: {stat_date})...")

    # Track tab names for doubleheaders
    seen: dict[str, int] = {}

    for sides in matchups:
        away = next((s for s in sides if s["side"] == "away"), sides[0])
        home = next((s for s in sides if s["side"] == "home"), sides[1])
        away["team_name"] = team_map.get(away["team_id"], str(away["team_id"]))
        home["team_name"] = team_map.get(home["team_id"], str(home["team_id"]))

        base_name = f"{abbr_map.get(away['team_id'],'???')} @ {abbr_map.get(home['team_id'],'???')}"
        count = seen.get(base_name, 0) + 1
        seen[base_name] = count
        tab_name = base_name if count == 1 else f"{base_name} G{count}"

        try:
            tab_rows, fmt_specs = build_dashboard_tab(con, away, home, stat_date, date_str)
            sheets_sync._write_tab(sh, tab_name, tab_rows)
            apply_formatting(sh, tab_name, fmt_specs)
            print(f"  ✓ {tab_name}")
        except Exception as exc:
            print(f"  ✗ {tab_name}: {exc}")

        time.sleep(3)

    con.close()
    print(f"\nDone: https://docs.google.com/spreadsheets/d/{DUEL_ID_PATH.read_text().strip()}")


if __name__ == "__main__":
    main()
