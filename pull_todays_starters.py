"""First test of the pipeline: pull today's confirmed/probable starting
pitchers (and, where lineups are already posted, today's starting batters),
fetch their season stats, and store everything in the local duckdb file.

By default only probable/confirmed starting pitchers are pulled (fast: one
Statcast call per pitcher). Pass --with-lineups to also pull season stats for
every batter in posted lineups -- lineups are usually only posted a couple
hours before game time, and pulling a full slate of lineups means one
Statcast call per batter (slow: can be 100+ calls).

Usage:
    source venv/bin/activate
    python pull_todays_starters.py [YYYY-MM-DD] [--with-lineups]
"""
import sys

from src import db, mlb_api, season_stats


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_lineups = "--with-lineups" in sys.argv
    date = args[0] if args else None
    if date is None:
        import datetime as dt

        date = dt.date.today().isoformat()
    season = int(date[:4])

    games = mlb_api.get_schedule_with_starters(date)
    if not games:
        print(f"No games found for {date}")
        return

    con = db.get_connection()

    print(f"=== {date}: {len(games)} team-game entries ===\n")

    pitcher_rows = []
    batter_rows = []

    for g in games:
        pid, pname = g["probable_pitcher_id"], g["probable_pitcher_name"]
        if pid:
            con.execute(
                """
                INSERT INTO todays_starters (game_date, game_pk, team_id, is_home, pitcher_id, pitcher_name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, now())
                ON CONFLICT (game_date, game_pk, team_id) DO UPDATE SET
                    pitcher_id = excluded.pitcher_id, pitcher_name = excluded.pitcher_name,
                    updated_at = excluded.updated_at
                """,
                [date, g["game_pk"], g["team_id"], g["side"] == "home", pid, pname],
            )
            print(f"[PITCHER] {pname} ({g['team_name']})")
            row = season_stats.pull_pitcher_season_stats(con, pid, pname, g["team_id"], season)
            if row:
                print(
                    f"    IP={row['innings_pitched']:.1f}  K%={row['k_pct']:.1%}  "
                    f"BB%={row['bb_pct']:.1%}  Whiff%={row['whiff_pct']:.1%}  xFIP={row['xfip']:.2f}"
                )
                pitcher_rows.append(row)
            else:
                print("    no season stats yet")

        if not with_lineups:
            continue
        for batter in g["lineup"]:
            print(f"  [LINEUP] {batter['name']} ({g['team_name']})")
            row = season_stats.pull_batter_season_stats(con, batter["id"], batter["name"], g["team_id"], season)
            if row:
                print(
                    f"      PA={row['plate_appearances']}  K%={row['k_pct']:.1%}  "
                    f"BB%={row['bb_pct']:.1%}  Whiff%={row['whiff_pct']:.1%}"
                )
                batter_rows.append(row)

    print(f"\nStored {len(pitcher_rows)} pitcher rows and {len(batter_rows)} batter rows in {db.DB_PATH}")


if __name__ == "__main__":
    main()
