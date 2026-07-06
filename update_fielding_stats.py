"""Pull and store fielding stats (basic + OAA) for all active players.

Basic fielding comes from the MLB Stats API (one call per player).
OAA comes from a bulk Statcast pull per position (9 calls total for all players).
Rows are keyed by (player_id, stat_date, position) — safe to re-run daily.

Usage:
    source venv/bin/activate
    python update_fielding_stats.py [YYYY-MM-DD]
"""
import datetime as dt
import sys

from src import db, mlb_api
from src.fielding_metrics import build_oaa_lookup, get_player_basic_fielding


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    stat_date = dt.date.fromisoformat(date_str)
    season = stat_date.year

    con = db.get_connection()

    print(f"Building OAA lookup for {season}...")
    oaa_lookup = build_oaa_lookup(season)
    print(f"  OAA entries: {len(oaa_lookup)}")

    teams = mlb_api.get_teams(season)
    ok = skip = 0

    for team in teams:
        team_id = team["id"]
        team_name = team.get("name", str(team_id))
        try:
            roster = mlb_api.get_active_roster(team_id, season)
        except Exception as exc:
            print(f"  [{team_name}] roster failed: {exc}")
            continue

        print(f"[{team_name}] {len(roster)} players")
        for player in roster:
            pid = player["player_id"]
            pname = player["player_name"]
            positions = get_player_basic_fielding(pid, season)
            if not positions:
                skip += 1
                continue
            for pos in positions:
                oaa = oaa_lookup.get(pid)
                con.execute(
                    """
                    INSERT INTO fielding_stats
                        (player_id, stat_date, season, player_name, team_id, position,
                         innings_played, putouts, assists, errors, fielding_pct,
                         range_factor, oaa, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
                    ON CONFLICT (player_id, stat_date, position) DO UPDATE SET
                        season = excluded.season, player_name = excluded.player_name,
                        team_id = excluded.team_id, innings_played = excluded.innings_played,
                        putouts = excluded.putouts, assists = excluded.assists,
                        errors = excluded.errors, fielding_pct = excluded.fielding_pct,
                        range_factor = excluded.range_factor, oaa = excluded.oaa,
                        updated_at = excluded.updated_at
                    """,
                    [pid, stat_date, season, pname, team_id, pos["position"],
                     pos["innings_played"], pos["putouts"], pos["assists"],
                     pos["errors"], pos["fielding_pct"], pos["range_factor"], oaa],
                )
            ok += 1

    print(f"\nDone. {ok} players stored, {skip} skipped. DB: {db.DB_PATH}")
    con.close()


if __name__ == "__main__":
    main()
