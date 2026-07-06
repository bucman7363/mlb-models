"""Build head-to-head game duel tabs in a dedicated Google Sheets spreadsheet.

Each of today's games gets its own tab showing both teams side-by-side:
  - Starting pitcher matchup
  - Full pitching roster (sorted by IP desc)
  - Full batting roster (sorted by wOBA desc)
  - Fielding (basic + OAA, sorted by innings played desc)

Spreadsheet ID is stored in credentials/duel_sheets_id.txt.
Share the sheet with: mlb-sheets-bot@mlb-models-500619.iam.gserviceaccount.com

Usage:
    source venv/bin/activate
    python game_duel_builder.py [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from src import db, mlb_api, sheets_sync

DUEL_ID_PATH = (
    __import__("pathlib").Path(__file__).resolve().parent
    / "credentials" / "duel_sheets_id.txt"
)

PITCH_COLS = ["player_name", "innings_pitched", "era", "whip", "xfip",
              "k_pct", "bb_pct", "whiff_pct", "strikeouts", "walks", "home_runs"]
PITCH_HDR  = ["Pitcher", "IP", "ERA", "WHIP", "xFIP",
              "K%", "BB%", "Whiff%", "K", "BB", "HR"]

BAT_COLS   = ["player_name", "plate_appearances", "avg", "obp", "slg", "woba",
              "k_pct", "bb_pct", "whiff_pct",
              "pa_vs_l", "woba_vs_l", "pa_vs_r", "woba_vs_r"]
BAT_HDR    = ["Batter", "PA", "AVG", "OBP", "SLG", "wOBA",
              "K%", "BB%", "Whiff%",
              "PA vs L", "wOBA vs L", "PA vs R", "wOBA vs R"]

FIELD_COLS = ["player_name", "position", "innings_played", "putouts",
              "assists", "errors", "fielding_pct", "oaa"]
FIELD_HDR  = ["Player", "Pos", "Inn", "PO", "A", "E", "FPCT", "OAA"]


def _fmt(v, pct=False):
    if v is None:
        return ""
    if pct and isinstance(v, float):
        return f"{v:.1%}"
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _team_pitchers(con, team_id, stat_date, starter_id=None):
    rows = con.execute(
        f"SELECT {', '.join(PITCH_COLS)} FROM pitcher_stats "
        "WHERE team_id = ? AND stat_date = ? ORDER BY innings_pitched DESC NULLS LAST",
        [team_id, stat_date],
    ).fetchall()

    result = []
    starter_row = None
    for r in rows:
        row = [_fmt(r[i], pct=(PITCH_COLS[i] in ("k_pct", "bb_pct", "whiff_pct")))
               for i in range(len(PITCH_COLS))]
        # Check if this is the starter by name match (starter_id not always in pitcher_stats)
        result.append(row)
        if starter_id:
            pid = con.execute(
                "SELECT player_id FROM pitcher_stats WHERE player_name = ? AND stat_date = ? AND team_id = ?",
                [r[0], stat_date, team_id],
            ).fetchone()
            if pid and pid[0] == starter_id:
                starter_row = row

    return result, starter_row


def _team_batters(con, team_id, stat_date):
    rows = con.execute(
        f"SELECT {', '.join(BAT_COLS)} FROM batter_stats "
        "WHERE team_id = ? AND stat_date = ? ORDER BY woba DESC NULLS LAST",
        [team_id, stat_date],
    ).fetchall()
    return [
        [_fmt(r[i], pct=(BAT_COLS[i] in ("k_pct", "bb_pct", "whiff_pct")))
         for i in range(len(BAT_COLS))]
        for r in rows
    ]


def _team_fielding(con, team_id, stat_date):
    rows = con.execute(
        f"SELECT {', '.join(FIELD_COLS)} FROM fielding_stats "
        "WHERE team_id = ? AND stat_date = ? ORDER BY innings_played DESC NULLS LAST",
        [team_id, stat_date],
    ).fetchall()
    return [[_fmt(v) for v in r] for r in rows]


def _side_by_side(label, away_hdr, away_rows, home_hdr, home_rows):
    """Merge away and home rows into a single side-by-side grid."""
    n_a = len(away_hdr)
    n_h = len(home_hdr)
    out = [
        [label] + [""] * (n_a - 1 + 1 + n_h),
        away_hdr + [""] + home_hdr,
    ]
    for i in range(max(len(away_rows), len(home_rows))):
        a = list(away_rows[i]) if i < len(away_rows) else [""] * n_a
        h = list(home_rows[i]) if i < len(home_rows) else [""] * n_h
        out.append(a + [""] + h)
    return out


def build_game_tab(con, away: dict, home: dict, stat_date: str, game_date: str) -> list[list]:
    away_id = away["team_id"]
    home_id = home["team_id"]
    away_name = away["team_name"]
    home_name = home["team_name"]
    away_sp = away.get("probable_pitcher_name") or "TBD"
    home_sp = home.get("probable_pitcher_name") or "TBD"
    away_sp_id = away.get("probable_pitcher_id")
    home_sp_id = home.get("probable_pitcher_id")

    rows = [
        [f"{away_name}  @  {home_name}   —   {game_date}  (stats as of {stat_date})"],
        [],
        [f"AWAY SP: {away_sp}", "", "", "", "", "", f"HOME SP: {home_sp}"],
        [],
    ]

    # Pitching
    away_pitchers, _ = _team_pitchers(con, away_id, stat_date, away_sp_id)
    home_pitchers, _ = _team_pitchers(con, home_id, stat_date, home_sp_id)
    rows += _side_by_side(
        f"PITCHING — {away_name}  vs  {home_name}",
        PITCH_HDR, away_pitchers, PITCH_HDR, home_pitchers,
    )
    rows.append([])

    # Batting
    away_batters = _team_batters(con, away_id, stat_date)
    home_batters = _team_batters(con, home_id, stat_date)
    rows += _side_by_side(
        f"BATTING — {away_name}  vs  {home_name}",
        BAT_HDR, away_batters, BAT_HDR, home_batters,
    )
    rows.append([])

    # Fielding
    away_fielding = _team_fielding(con, away_id, stat_date)
    home_fielding = _team_fielding(con, home_id, stat_date)
    if away_fielding or home_fielding:
        rows += _side_by_side(
            f"FIELDING — {away_name}  vs  {home_name}",
            FIELD_HDR, away_fielding, FIELD_HDR, home_fielding,
        )
        rows.append([])
    else:
        rows += [[f"FIELDING — run update_fielding_stats.py to populate"]]
        rows.append([])

    return rows


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()

    if not DUEL_ID_PATH.exists():
        print("No spreadsheet ID found.")
        print(f"Create a Google Sheet, share it with:")
        print(f"  mlb-sheets-bot@mlb-models-500619.iam.gserviceaccount.com")
        print(f"Then save the spreadsheet ID to: {DUEL_ID_PATH}")
        return

    con = db.get_connection(read_only=True)

    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM pitcher_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])
    if not stat_date or stat_date == "None":
        print(f"No stats in DB for or before {date_str}")
        con.close()
        return

    season = int(date_str[:4])
    teams = mlb_api.get_teams(season)
    team_map = {t["id"]: t["name"] for t in teams}

    games = mlb_api.get_schedule_with_starters(date_str)
    game_groups: dict[int, list] = {}
    for g in games:
        game_groups.setdefault(g["game_pk"], []).append(g)

    gc = sheets_sync._get_client()
    sh = gc.open_by_key(DUEL_ID_PATH.read_text().strip())

    matchups = [sides for sides in game_groups.values() if len(sides) == 2]
    print(f"Building {len(matchups)} game duel tabs for {date_str} (stats: {stat_date})...")

    for sides in matchups:
        away = next((s for s in sides if s["side"] == "away"), sides[0])
        home = next((s for s in sides if s["side"] == "home"), sides[1])
        away["team_name"] = team_map.get(away["team_id"], str(away["team_id"]))
        home["team_name"] = team_map.get(home["team_id"], str(home["team_id"]))

        tab_name = f"{away['team_name'][:12]} @ {home['team_name'][:12]}"
        tab_rows = build_game_tab(con, away, home, stat_date, date_str)

        sheets_sync._write_tab(sh, tab_name, tab_rows)
        print(f"  {tab_name}")
        time.sleep(2)

    con.close()
    print(f"\nDone: https://docs.google.com/spreadsheets/d/{DUEL_ID_PATH.read_text().strip()}")


if __name__ == "__main__":
    main()
