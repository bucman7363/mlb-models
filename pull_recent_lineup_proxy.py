"""One-off: pull season batter stats for each opposing team's most recent
posted lineup (proxy for today's lineup, since today's hasn't posted yet).
Reads recent_lineups.pkl (team_name -> (date, [{'id','fullName',...}, ...])).
"""
import pickle

from src import db, season_stats

TEAM_IDS = {
    "Astros": 117, "Red Sox": 111, "Rangers": 140, "Pirates": 134,
    "Royals": 118, "Nationals": 120, "Mariners": 136, "Rockies": 115,
    "Cubs": 112, "Marlins": 146, "Padres": 135, "Dodgers": 119,
    "Giants": 137, "Athletics": 133,
}

SEASON = 2026

with open("/home/bucman7363/mlb-models/recent_lineups.pkl", "rb") as f:
    lineups = pickle.load(f)

con = db.get_connection()

for team_name, found in lineups.items():
    if not found:
        print(f"{team_name}: no lineup, skipping")
        continue
    date, players = found
    team_id = TEAM_IDS[team_name]
    print(f"=== {team_name} (lineup from {date}) ===")
    for p in players:
        row = season_stats.pull_batter_season_stats(con, p["id"], p["fullName"], team_id, SEASON)
        if row:
            print(
                f"  {p['fullName']:22s} PA={row['plate_appearances']:4d}  "
                f"K%={row['k_pct']:.1%}  BB%={row['bb_pct']:.1%}  Whiff%={row['whiff_pct']:.1%}"
            )
        else:
            print(f"  {p['fullName']:22s} no season stats")

print("DONE")
