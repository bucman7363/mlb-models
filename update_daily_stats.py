"""Store a dated snapshot of full-season stats for every active MLB player.

Tables written: pitcher_stats and batter_stats, keyed by (player_id, stat_date).
Re-running on the same date overwrites rather than duplicating rows.

Statcast calls are one per player (~780 total), so expect 20-40 minutes runtime.
Run overnight or as a background job.

Flags:
    --sheets            push results to Google Sheets after DB write
    --sheets-email X    share the sheet with email X on first creation

Usage:
    source venv/bin/activate
    python update_daily_stats.py [YYYY-MM-DD] [--sheets] [--sheets-email you@gmail.com]
"""
import datetime as dt
import sys

from src import db, mlb_api, season_stats


def main():
    args = sys.argv[1:]
    push_sheets = "--sheets" in args
    owner_email = None
    if "--sheets-email" in args:
        idx = args.index("--sheets-email")
        owner_email = args[idx + 1]
    plain_args = [a for a in args if not a.startswith("--") and a != owner_email]
    date_str = plain_args[0] if plain_args else dt.date.today().isoformat()
    stat_date = dt.date.fromisoformat(date_str)
    season = stat_date.year

    con = db.get_connection()
    teams = mlb_api.get_teams(season)
    print(f"=== {date_str}: {len(teams)} teams ===\n")

    p_ok = p_skip = b_ok = b_skip = 0

    for team in teams:
        team_id = team["id"]
        team_name = team.get("name", str(team_id))
        try:
            roster = mlb_api.get_active_roster(team_id, season)
        except Exception as exc:
            print(f"  [{team_name}] roster fetch failed: {exc}")
            continue

        print(f"[{team_name}] {len(roster)} players")
        for player in roster:
            pid = player["player_id"]
            pname = player["player_name"]
            is_pitcher = player["position_type"] == "Pitcher"

            if is_pitcher:
                try:
                    row = season_stats.pull_pitcher_daily_stats(
                        con, pid, pname, team_id, season, stat_date
                    )
                    if row:
                        p_ok += 1
                    else:
                        p_skip += 1
                except Exception as exc:
                    print(f"    WARN pitcher {pname} ({pid}): {exc}")
                    p_skip += 1
            else:
                try:
                    row = season_stats.pull_batter_daily_stats(
                        con, pid, pname, team_id, season, stat_date
                    )
                    if row:
                        b_ok += 1
                    else:
                        b_skip += 1
                except Exception as exc:
                    print(f"    WARN batter {pname} ({pid}): {exc}")
                    b_skip += 1

    print(
        f"\nDone. Pitchers: {p_ok} stored, {p_skip} skipped. "
        f"Batters: {b_ok} stored, {b_skip} skipped.\n"
        f"DB: {db.DB_PATH}"
    )

    if push_sheets:
        from src import sheets_sync
        print("\nPushing to Google Sheets...")
        url = sheets_sync.push_daily_stats(con, date_str, owner_email)
        print(f"Spreadsheet: {url}")


if __name__ == "__main__":
    main()
