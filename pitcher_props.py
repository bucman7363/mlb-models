"""Strikeout over/under props for today's starting pitchers.

Model: blended K% (60% pitcher, 40% opponent avg K%) × projected BF.
Projected BF = expected IP × 4.2 BF/IP. Expected IP estimated from
season workload. Whiff% used as a confidence boost to the projection.

Output: a 'K Props YYYY-MM-DD' tab written to the MLB Pipeline spreadsheet.

Usage:
    source venv/bin/activate
    python pitcher_props.py [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from src import db, mlb_api, sheets_sync

SPREADSHEET_ID = "1Zqb7ny6gg0Xa4gkYMnjZE5wkAkUEJBuzPc44DidttBk"
LG_K_PCT = 0.225  # approximate 2026 league-average K rate


def _expected_ip(innings_pitched: float | None) -> float:
    """Estimate IP for today's start based on season workload."""
    if not innings_pitched:
        return 5.0
    if innings_pitched >= 100:
        return 6.0
    if innings_pitched >= 60:
        return 5.5
    if innings_pitched >= 30:
        return 5.0
    return 4.5


def _opp_avg_k_pct(con, opp_team_id: int, stat_date: str) -> float | None:
    """Average K% of the opposing team's batters."""
    row = con.execute(
        "SELECT AVG(k_pct) FROM batter_stats "
        "WHERE team_id = ? AND stat_date = ? AND k_pct IS NOT NULL",
        [opp_team_id, stat_date],
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _project_ks(pitcher_k_pct: float, opp_k_pct: float, whiff_pct: float | None,
                exp_ip: float) -> float:
    blended = 0.6 * pitcher_k_pct + 0.4 * opp_k_pct
    # Whiff% adds a small boost: high whiff pitchers outperform K% alone
    if whiff_pct is not None:
        whiff_bonus = (whiff_pct - 0.25) * 0.5  # neutral at 25% whiff
        blended = blended + whiff_bonus
    blended = max(blended, 0.05)
    return blended * (exp_ip * 4.2)


def _grade(pitcher_k_pct: float, opp_k_pct: float, whiff_pct: float | None) -> str:
    w = whiff_pct or 0.0
    if pitcher_k_pct >= 0.28 and opp_k_pct >= 0.225 and w >= 0.30:
        return "A"
    if pitcher_k_pct >= 0.23 and opp_k_pct >= 0.210:
        return "B"
    return "C"


def _prop_line(proj_ks: float) -> float:
    """Suggest a half-integer prop line closest to the projection."""
    return round(proj_ks * 2) / 2 - 0.5


def build_props(date_str: str) -> list[list]:
    con = db.get_connection(read_only=True)

    # Use most recent stat date <= today
    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM pitcher_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])

    # Get team name map
    season = int(date_str[:4])
    teams = mlb_api.get_teams(season)
    team_map = {t["id"]: t["name"] for t in teams}

    # Get today's probable starters from MLB API
    games = mlb_api.get_schedule_with_starters(date_str)

    # Build opponent map: team_id -> opp_team_id
    opp_map: dict[int, int] = {}
    game_pks: dict[int, str] = {}  # team_id -> game label
    game_groups: dict[int, list] = {}
    for g in games:
        pk = g["game_pk"]
        if pk not in game_groups:
            game_groups[pk] = []
        game_groups[pk].append(g)
    for pk, sides in game_groups.items():
        if len(sides) == 2:
            t0, t1 = sides[0]["team_id"], sides[1]["team_id"]
            opp_map[t0] = t1
            opp_map[t1] = t0
            label = f"{team_map.get(sides[0]['team_id'], '?')} @ {team_map.get(sides[1]['team_id'], '?')}"
            game_pks[t0] = label
            game_pks[t1] = label

    rows = []
    for g in games:
        pid = g["probable_pitcher_id"]
        if not pid:
            continue
        pname = g["probable_pitcher_name"]
        team_id = g["team_id"]
        opp_team_id = opp_map.get(team_id)

        # Pitcher stats
        pstats = con.execute(
            "SELECT k_pct, bb_pct, whiff_pct, era, xfip, innings_pitched "
            "FROM pitcher_stats WHERE player_id = ? AND stat_date = ?",
            [pid, stat_date],
        ).fetchone()

        if not pstats or pstats[0] is None:
            continue

        k_pct, bb_pct, whiff_pct, era, xfip, ip = pstats
        opp_k_pct = _opp_avg_k_pct(con, opp_team_id, stat_date) if opp_team_id else None
        if opp_k_pct is None:
            opp_k_pct = LG_K_PCT

        exp_ip = _expected_ip(ip)
        proj_ks = _project_ks(k_pct, opp_k_pct, whiff_pct, exp_ip)
        line = _prop_line(proj_ks)
        edge = proj_ks - line
        grade = _grade(k_pct, opp_k_pct, whiff_pct)
        lean = "OVER" if edge > 0.3 else ("UNDER" if edge < -0.3 else "NEUTRAL")

        rows.append({
            "game": game_pks.get(team_id, ""),
            "pitcher": pname,
            "team": team_map.get(team_id, ""),
            "opponent": team_map.get(opp_team_id, "") if opp_team_id else "",
            "proj_ks": round(proj_ks, 2),
            "prop_line": line,
            "edge": round(edge, 2),
            "lean": lean,
            "grade": grade,
            "k_pct": f"{k_pct:.1%}",
            "opp_k_pct": f"{opp_k_pct:.1%}",
            "whiff_pct": f"{whiff_pct:.1%}" if whiff_pct else "N/A",
            "era": round(era, 2) if era else None,
            "xfip": round(xfip, 2) if xfip else None,
            "exp_ip": exp_ip,
        })

    con.close()
    rows.sort(key=lambda r: r["proj_ks"], reverse=True)
    return rows


def to_sheet_rows(props: list[dict], date_str: str, stat_date: str) -> list[list]:
    headers = [
        "Game", "Pitcher", "Team", "Opponent",
        "Proj Ks", "Prop Line", "Edge", "Lean", "Grade",
        "Pitcher K%", "Opp K%", "Whiff%", "ERA", "xFIP", "Exp IP",
    ]
    sheet_rows = [
        [f"STRIKEOUT PROPS  —  {date_str}  (stats as of {stat_date})"],
        [],
        headers,
    ]
    for r in props:
        sheet_rows.append([
            r["game"], r["pitcher"], r["team"], r["opponent"],
            r["proj_ks"], r["prop_line"], r["edge"], r["lean"], r["grade"],
            r["k_pct"], r["opp_k_pct"], r["whiff_pct"], r["era"], r["xfip"], r["exp_ip"],
        ])
    return sheet_rows


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()

    print(f"Building K props for {date_str}...")
    props = build_props(date_str)

    if not props:
        print("No props generated — check that starters are announced.")
        return

    con = db.get_connection(read_only=True)
    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM pitcher_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])
    con.close()

    gc = sheets_sync._get_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    tab_name = f"K Props {date_str}"
    sheets_sync._write_tab(sh, tab_name, to_sheet_rows(props, date_str, stat_date))
    print(f"Written to tab: {tab_name}")

    print(f"\n{'Pitcher':<25} {'Proj':>5} {'Line':>5} {'Edge':>5} {'Lean':<8} {'Grade'}")
    print("-" * 65)
    for r in props:
        print(f"{r['pitcher']:<25} {r['proj_ks']:>5.1f} {r['prop_line']:>5.1f} "
              f"{r['edge']:>+5.2f} {r['lean']:<8} {r['grade']}")

    print(f"\nhttps://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
