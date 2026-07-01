"""Build 30 team breakdown tabs + a daily matchup tab in Google Sheets.

Each team tab shows that team's pitchers (ERA/WHIP/xFIP/K%/BB%/Whiff%)
and batters (AVG/OBP/SLG/wOBA/K%/BB%/Whiff% + platoon splits vs L/R)
using the most recent stats available for the given date.

The matchup tab shows today's probable starters side-by-side with their
season stats. Run pull_todays_starters.py first to populate it.

Usage:
    source venv/bin/activate
    python team_sheets_builder.py [YYYY-MM-DD]

Date defaults to today; falls back to most recent date in the DB.
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import gspread

from src import db, mlb_api, sheets_sync

SPREADSHEET_ID = "1Zqb7ny6gg0Xa4gkYMnjZE5wkAkUEJBuzPc44DidttBk"

PITCHER_COLS = [
    "player_name", "innings_pitched", "era", "whip", "xfip",
    "k_pct", "bb_pct", "whiff_pct", "batters_faced", "strikeouts",
    "walks", "home_runs",
]
PITCHER_HEADERS = [
    "Name", "IP", "ERA", "WHIP", "xFIP",
    "K%", "BB%", "Whiff%", "BF", "K", "BB", "HR",
]

BATTER_COLS = [
    "player_name", "plate_appearances", "avg", "obp", "slg", "woba",
    "k_pct", "bb_pct", "whiff_pct",
    "pa_vs_l", "avg_vs_l", "obp_vs_l", "slg_vs_l", "woba_vs_l",
    "pa_vs_r", "avg_vs_r", "obp_vs_r", "slg_vs_r", "woba_vs_r",
]
BATTER_HEADERS = [
    "Name", "PA", "AVG", "OBP", "SLG", "wOBA",
    "K%", "BB%", "Whiff%",
    "PA vs L", "AVG vs L", "OBP vs L", "SLG vs L", "wOBA vs L",
    "PA vs R", "AVG vs R", "OBP vs R", "SLG vs R", "wOBA vs R",
]


def _fmt(v) -> str | float | int | None:
    """Round floats for display; leave everything else as-is."""
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _pct(v) -> str | None:
    if v is None:
        return None
    return f"{v:.1%}"


def _build_team_tab(con, team_id: int, team_name: str, stat_date: str) -> list[list]:
    rows = [[f"{team_name}  —  Stats as of {stat_date}"], []]

    # Pitchers
    pitchers = con.execute(
        f"SELECT {', '.join(PITCHER_COLS)} FROM pitcher_stats "
        f"WHERE team_id = ? AND stat_date = ? ORDER BY era NULLS LAST",
        [team_id, stat_date],
    ).fetchall()
    rows.append(["PITCHERS"])
    rows.append(PITCHER_HEADERS)
    for p in pitchers:
        row = list(p)
        for i, col in enumerate(PITCHER_COLS):
            if col in ("k_pct", "bb_pct", "whiff_pct"):
                row[i] = _pct(row[i])
            else:
                row[i] = _fmt(row[i])
        rows.append(row)
    if not pitchers:
        rows.append(["No pitcher data"])

    rows.append([])

    # Batters
    batters = con.execute(
        f"SELECT {', '.join(BATTER_COLS)} FROM batter_stats "
        f"WHERE team_id = ? AND stat_date = ? ORDER BY woba DESC NULLS LAST",
        [team_id, stat_date],
    ).fetchall()
    rows.append(["BATTERS"])
    rows.append(BATTER_HEADERS)
    for b in batters:
        row = list(b)
        for i, col in enumerate(BATTER_COLS):
            if col in ("k_pct", "bb_pct", "whiff_pct"):
                row[i] = _pct(row[i])
            else:
                row[i] = _fmt(row[i])
        rows.append(row)
    if not batters:
        rows.append(["No batter data"])

    return rows


def _build_matchup_tab(con, game_date: str) -> list[list]:
    games = con.execute(
        "SELECT game_pk, team_id, is_home, pitcher_id, pitcher_name "
        "FROM todays_starters WHERE game_date = ? ORDER BY game_pk, is_home",
        [game_date],
    ).fetchall()

    if not games:
        return [[f"No starters found for {game_date}"],
                ["Run: python pull_todays_starters.py to populate"]]

    # Group by game_pk
    game_map: dict[int, dict] = {}
    for game_pk, team_id, is_home, pitcher_id, pitcher_name in games:
        if game_pk not in game_map:
            game_map[game_pk] = {}
        side = "home" if is_home else "away"
        game_map[game_pk][side] = {"team_id": team_id, "pitcher_id": pitcher_id, "pitcher_name": pitcher_name}

    def _pitcher_stats(pitcher_id):
        if not pitcher_id:
            return [None] * 6
        row = con.execute(
            "SELECT era, whip, xfip, k_pct, bb_pct, whiff_pct "
            "FROM pitcher_stats WHERE player_id = ? "
            "ORDER BY stat_date DESC LIMIT 1",
            [pitcher_id],
        ).fetchone()
        if not row:
            return [None] * 6
        era, whip, xfip, k_pct, bb_pct, whiff_pct = row
        return [_fmt(era), _fmt(whip), _fmt(xfip), _pct(k_pct), _pct(bb_pct), _pct(whiff_pct)]

    headers = [
        "Game",
        "Away Pitcher", "ERA", "WHIP", "xFIP", "K%", "BB%", "Whiff%",
        "vs",
        "Home Pitcher", "ERA", "WHIP", "xFIP", "K%", "BB%", "Whiff%",
    ]
    rows = [[f"DAILY MATCHUPS  —  {game_date}"], [], headers]

    for i, (game_pk, sides) in enumerate(game_map.items(), 1):
        away = sides.get("away", {})
        home = sides.get("home", {})
        away_stats = _pitcher_stats(away.get("pitcher_id"))
        home_stats = _pitcher_stats(home.get("pitcher_id"))
        rows.append([
            f"Game {i}",
            away.get("pitcher_name", "TBD"), *away_stats,
            "@",
            home.get("pitcher_name", "TBD"), *home_stats,
        ])

    return rows


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()

    con = db.get_connection(read_only=True)

    # Fall back to most recent date in DB if no data for requested date
    available = con.execute(
        "SELECT MAX(stat_date) FROM pitcher_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0]
    if not available:
        print(f"No data in DB for or before {date_str}")
        return
    stat_date = str(available)
    if stat_date != date_str:
        print(f"No data for {date_str}, using most recent: {stat_date}")

    gc = sheets_sync._get_client()
    sh = gc.open_by_key(SPREADSHEET_ID)

    # Team name mapping
    teams = mlb_api.get_teams(int(stat_date[:4]))
    team_map = {t["id"]: t["name"] for t in teams}

    # Get all team_ids that have data for this date
    team_ids = [
        r[0] for r in con.execute(
            "SELECT DISTINCT team_id FROM pitcher_stats WHERE stat_date = ? "
            "UNION SELECT DISTINCT team_id FROM batter_stats WHERE stat_date = ? "
            "ORDER BY 1",
            [stat_date, stat_date],
        ).fetchall()
    ]

    print(f"Building {len(team_ids)} team tabs + matchup tab for {stat_date}...")

    for team_id in team_ids:
        team_name = team_map.get(team_id, f"Team {team_id}")
        tab_name = team_name[:31]  # Sheets tab name limit
        rows = _build_team_tab(con, team_id, team_name, stat_date)
        sheets_sync._write_tab(sh, tab_name, rows)
        print(f"  {team_name}")
        time.sleep(2)  # stay under 60 writes/min quota

    # Matchup tab
    matchup_rows = _build_matchup_tab(con, date_str)
    sheets_sync._write_tab(sh, f"Matchups {date_str}", matchup_rows)
    print(f"  Matchup tab: Matchups {date_str}")

    con.close()
    print(f"\nDone: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
