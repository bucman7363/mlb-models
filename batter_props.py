"""Hits, total bases, and DraftKings fantasy score props for today's batters.

Model:
- Platoon-adjusted AVG/OBP/SLG using vs-L or vs-R splits based on opposing SP hand
- xFIP-based pitcher quality adjustment (each 0.1 xFIP vs 4.20 avg ≈ ±0.5%)
- Expected PA = 4.0 per game
- DK scoring: 1B=3, 2B=5, 3B=8, HR=10, BB=2 (R/RBI/SB excluded — floor estimate)

Usage:
    source venv/bin/activate
    python batter_props.py [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from src import db, mlb_api, sheets_sync

SPREADSHEET_ID = "1Zqb7ny6gg0Xa4gkYMnjZE5wkAkUEJBuzPc44DidttBk"
LG_AVG_XFIP = 4.20
EST_PA = 4.0


def _pitcher_adj(xfip: float | None) -> float:
    """Multiplier on batter output based on opposing pitcher quality."""
    if xfip is None:
        return 1.0
    return 1.0 + (xfip - LG_AVG_XFIP) * 0.05


def _platoon_stats(row: dict, hand: str | None) -> dict:
    """Return platoon-adjusted avg/obp/slg/woba or fall back to overall."""
    if hand == "L" and row["avg_vs_l"] is not None:
        return {
            "avg": row["avg_vs_l"], "obp": row["obp_vs_l"],
            "slg": row["slg_vs_l"], "woba": row["woba_vs_l"],
            "pa": row["pa_vs_l"], "split": "vs L",
        }
    if hand == "R" and row["avg_vs_r"] is not None:
        return {
            "avg": row["avg_vs_r"], "obp": row["obp_vs_r"],
            "slg": row["slg_vs_r"], "woba": row["woba_vs_r"],
            "pa": row["pa_vs_r"], "split": "vs R",
        }
    return {
        "avg": row["avg"], "obp": row["obp"],
        "slg": row["slg"], "woba": row["woba"],
        "pa": row["plate_appearances"], "split": "overall",
    }


def _project(row: dict, hand: str | None, adj: float, est_pa: float = EST_PA) -> dict:
    plat = _platoon_stats(row, hand)
    avg = (plat["avg"] or 0) * adj
    slg = (plat["slg"] or 0) * adj
    bb_pct = row["bb_pct"] or 0

    proj_bb = bb_pct * est_pa
    est_ab = est_pa - proj_bb - 0.01 * est_pa  # subtract BB and ~1% HBP

    proj_h = avg * est_ab

    season_ab = row["at_bats"] or 1
    proj_2b = (row["doubles"] / season_ab) * est_ab * adj
    proj_3b = (row["triples"] / season_ab) * est_ab * adj
    proj_hr = (row["home_runs"] / season_ab) * est_ab * adj
    proj_1b = max(proj_h - proj_2b - proj_3b - proj_hr, 0)

    proj_tb = proj_1b + proj_2b * 2 + proj_3b * 3 + proj_hr * 4
    proj_dk = proj_1b * 3 + proj_2b * 5 + proj_3b * 8 + proj_hr * 10 + proj_bb * 2

    return {
        "proj_h": round(proj_h, 2),
        "proj_tb": round(proj_tb, 2),
        "proj_dk": round(proj_dk, 2),
        "h_line": round(proj_h * 2) / 2 - 0.5,
        "tb_line": round(proj_tb * 2) / 2 - 0.5,
        "dk_line": round(proj_dk * 2) / 2 - 0.5,
        "h_edge": round(proj_h - (round(proj_h * 2) / 2 - 0.5), 2),
        "tb_edge": round(proj_tb - (round(proj_tb * 2) / 2 - 0.5), 2),
        "dk_edge": round(proj_dk - (round(proj_dk * 2) / 2 - 0.5), 2),
        "split_used": plat["split"],
        "plat_avg": plat["avg"],
        "plat_slg": plat["slg"],
        "plat_woba": plat["woba"],
    }


def build_props(date_str: str) -> list[dict]:
    con = db.get_connection(read_only=True)

    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM batter_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])

    season = int(date_str[:4])
    teams = mlb_api.get_teams(season)
    team_map = {t["id"]: t["name"] for t in teams}

    games = mlb_api.get_schedule_with_starters(date_str)

    # Map team_id -> {opp_team_id, pitcher_id, pitcher_name, is_home}
    game_groups: dict[int, list] = {}
    for g in games:
        pk = g["game_pk"]
        game_groups.setdefault(pk, []).append(g)

    matchups: dict[int, dict] = {}  # team_id -> matchup info
    for pk, sides in game_groups.items():
        if len(sides) != 2:
            continue
        for i, side in enumerate(sides):
            opp = sides[1 - i]
            matchups[side["team_id"]] = {
                "opp_team_id": opp["team_id"],
                "opp_pitcher_id": opp["probable_pitcher_id"],
                "opp_pitcher_name": opp["probable_pitcher_name"],
                "game_label": (
                    f"{team_map.get(sides[0]['team_id'], '?')} @ "
                    f"{team_map.get(sides[1]['team_id'], '?')}"
                ),
            }

    rows = []
    seen_pitchers: dict[int, str | None] = {}  # pitcher_id -> hand cache

    for team_id, matchup in matchups.items():
        pid = matchup["opp_pitcher_id"]
        if not pid:
            continue

        # Pitcher hand (cached)
        if pid not in seen_pitchers:
            try:
                seen_pitchers[pid] = mlb_api.get_pitcher_hand(pid)
            except Exception:
                seen_pitchers[pid] = None
        hand = seen_pitchers[pid]

        # Pitcher stats for quality adjustment
        pstats = con.execute(
            "SELECT xfip, era FROM pitcher_stats WHERE player_id = ? AND stat_date = ?",
            [pid, stat_date],
        ).fetchone()
        adj = _pitcher_adj(pstats[0] if pstats else None)
        opp_era = round(pstats[1], 2) if pstats and pstats[1] else None
        opp_xfip = round(pstats[0], 2) if pstats and pstats[0] else None

        # All batters on this team
        batters = con.execute(
            "SELECT player_name, plate_appearances, at_bats, hits, doubles, triples, "
            "home_runs, walks, strikeouts, avg, obp, slg, woba, k_pct, bb_pct, whiff_pct, "
            "pa_vs_l, avg_vs_l, obp_vs_l, slg_vs_l, woba_vs_l, "
            "pa_vs_r, avg_vs_r, obp_vs_r, slg_vs_r, woba_vs_r "
            "FROM batter_stats WHERE team_id = ? AND stat_date = ? "
            "AND plate_appearances >= 50",
            [team_id, stat_date],
        ).fetchall()

        cols = [
            "player_name", "plate_appearances", "at_bats", "hits", "doubles", "triples",
            "home_runs", "walks", "strikeouts", "avg", "obp", "slg", "woba",
            "k_pct", "bb_pct", "whiff_pct",
            "pa_vs_l", "avg_vs_l", "obp_vs_l", "slg_vs_l", "woba_vs_l",
            "pa_vs_r", "avg_vs_r", "obp_vs_r", "slg_vs_r", "woba_vs_r",
        ]
        for b in batters:
            brow = dict(zip(cols, b))
            proj = _project(brow, hand, adj)
            rows.append({
                "game": matchup["game_label"],
                "batter": brow["player_name"],
                "team": team_map.get(team_id, ""),
                "opp_sp": matchup["opp_pitcher_name"] or "TBD",
                "sp_hand": hand or "?",
                "opp_era": opp_era,
                "opp_xfip": opp_xfip,
                "split": proj["split_used"],
                "proj_h": proj["proj_h"],
                "h_line": proj["h_line"],
                "h_edge": proj["h_edge"],
                "h_lean": "OVER" if proj["h_edge"] > 0.1 else ("UNDER" if proj["h_edge"] < -0.1 else "—"),
                "proj_tb": proj["proj_tb"],
                "tb_line": proj["tb_line"],
                "tb_edge": proj["tb_edge"],
                "tb_lean": "OVER" if proj["tb_edge"] > 0.15 else ("UNDER" if proj["tb_edge"] < -0.15 else "—"),
                "proj_dk": proj["proj_dk"],
                "dk_line": proj["dk_line"],
                "dk_edge": proj["dk_edge"],
                "avg": brow["avg"],
                "slg": brow["slg"],
                "woba": brow["woba"],
                "plat_avg": proj["plat_avg"],
                "plat_slg": proj["plat_slg"],
                "plat_woba": proj["plat_woba"],
            })

    con.close()
    rows.sort(key=lambda r: r["proj_dk"], reverse=True)
    return rows


def to_sheet_rows(props: list[dict], date_str: str, stat_date: str) -> list[list]:
    headers = [
        "Game", "Batter", "Team", "Opp SP", "SP Hand", "Split",
        "Proj H", "H Line", "H Edge", "H Lean",
        "Proj TB", "TB Line", "TB Edge", "TB Lean",
        "Proj DK Pts", "DK Line", "DK Edge",
        "AVG", "SLG", "wOBA", "Plat AVG", "Plat SLG", "Plat wOBA",
        "Opp ERA", "Opp xFIP",
    ]
    sheet_rows = [
        [f"BATTER PROPS  —  {date_str}  (stats as of {stat_date}) — DK scoring excl. R/RBI/SB"],
        [],
        headers,
    ]
    for r in props:
        sheet_rows.append([
            r["game"], r["batter"], r["team"], r["opp_sp"], r["sp_hand"], r["split"],
            r["proj_h"], r["h_line"], r["h_edge"], r["h_lean"],
            r["proj_tb"], r["tb_line"], r["tb_edge"], r["tb_lean"],
            r["proj_dk"], r["dk_line"], r["dk_edge"],
            r["avg"], r["slg"], r["woba"], r["plat_avg"], r["plat_slg"], r["plat_woba"],
            r["opp_era"], r["opp_xfip"],
        ])
    return sheet_rows


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    print(f"Building batter props for {date_str}...")

    props = build_props(date_str)
    if not props:
        print("No props generated — no lineup/starter data available yet.")
        return

    con = db.get_connection(read_only=True)
    stat_date = str(con.execute(
        "SELECT MAX(stat_date) FROM batter_stats WHERE stat_date <= ?", [date_str]
    ).fetchone()[0])
    con.close()

    gc = sheets_sync._get_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    tab_name = f"Batter Props {date_str}"
    sheets_sync._write_tab(sh, tab_name, to_sheet_rows(props, date_str, stat_date))
    print(f"Written to tab: {tab_name}\n")

    print(f"{'Batter':<22} {'Opp SP':<18} {'H':>5} {'H Ln':>5} {'TB':>5} {'TB Ln':>5} {'DK':>6} {'Split'}")
    print("-" * 85)
    for r in props[:20]:
        print(f"{r['batter']:<22} {r['opp_sp']:<18} {r['proj_h']:>5.2f} {r['h_line']:>5.1f} "
              f"{r['proj_tb']:>5.2f} {r['tb_line']:>5.1f} {r['proj_dk']:>6.2f}  {r['split']}")

    print(f"\nhttps://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
