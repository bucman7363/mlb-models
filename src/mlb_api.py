"""Thin client over the public MLB Stats API (statsapi.mlb.com).

Used for official counting stats (IP, BF, BB, K, HBP, HR, PA) and today's
probable starters/lineups. No auth required.
"""
from __future__ import annotations

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"


def get_teams(season: int, sport_id: int = 1) -> list[dict]:
    r = requests.get(
        f"{BASE_URL}/teams",
        params={"sportId": sport_id, "activeStatus": "Y", "season": season},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["teams"]


def get_team_pitching_totals(team_id: int, season: int) -> dict | None:
    r = requests.get(
        f"{BASE_URL}/teams/{team_id}/stats",
        params={"stats": "season", "group": "pitching", "season": season},
        timeout=15,
    )
    r.raise_for_status()
    stats = r.json().get("stats", [])
    if not stats or not stats[0].get("splits"):
        return None
    return stats[0]["splits"][0]["stat"]


def get_player_season_stat(player_id: int, season: int, group: str) -> dict | None:
    """group is 'pitching' or 'hitting'."""
    r = requests.get(
        f"{BASE_URL}/people/{player_id}/stats",
        params={"stats": "season", "group": group, "season": season},
        timeout=15,
    )
    r.raise_for_status()
    stats = r.json().get("stats", [])
    if not stats or not stats[0].get("splits"):
        return None
    return stats[0]["splits"][0]["stat"]


def get_schedule_with_starters(date: str) -> list[dict]:
    """date is 'YYYY-MM-DD'. Returns one dict per game with home/away team id,
    probable pitcher (id/name) if announced, and confirmed lineup batter ids
    if posted (lineups are only available close to/at game time).
    """
    r = requests.get(
        f"{BASE_URL}/schedule",
        params={"sportId": 1, "date": date, "hydrate": "probablePitcher,lineups"},
        timeout=15,
    )
    r.raise_for_status()
    dates = r.json().get("dates", [])
    if not dates:
        return []

    games = []
    for game in dates[0]["games"]:
        lineups = game.get("lineups", {})
        for side in ("away", "home"):
            team_info = game["teams"][side]
            games.append(
                {
                    "game_pk": game["gamePk"],
                    "side": side,
                    "team_id": team_info["team"]["id"],
                    "team_name": team_info["team"]["name"],
                    "probable_pitcher_id": team_info.get("probablePitcher", {}).get("id"),
                    "probable_pitcher_name": team_info.get("probablePitcher", {}).get("fullName"),
                    "lineup": [
                        {"id": p["id"], "name": p["fullName"]}
                        for p in lineups.get(f"{side}Players", [])
                    ],
                }
            )
    return games


def get_active_roster(team_id: int, season: int) -> list[dict]:
    """Returns [{player_id, player_name, position_type}] for the 26-man active roster."""
    r = requests.get(
        f"{BASE_URL}/teams/{team_id}/roster",
        params={"rosterType": "active", "season": season},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {
            "player_id": entry["person"]["id"],
            "player_name": entry["person"]["fullName"],
            "position_type": entry["position"]["type"],
        }
        for entry in r.json().get("roster", [])
    ]


def get_player_split_stats(player_id: int, season: int, sit_code: str) -> dict | None:
    """sit_code: 'vl' (vs LHP) or 'vr' (vs RHP). Returns the stat dict or None."""
    r = requests.get(
        f"{BASE_URL}/people/{player_id}/stats",
        params={"stats": "statSplits", "group": "hitting", "season": season, "sitCodes": sit_code},
        timeout=15,
    )
    r.raise_for_status()
    for stat_group in r.json().get("stats", []):
        for split in stat_group.get("splits", []):
            if split.get("split", {}).get("code") == sit_code:
                return split["stat"]
    return None


def innings_pitched_to_outs(ip_value) -> int:
    """MLB's inningsPitched is a string/float like '88.2' meaning 88 innings
    + 2 thirds (NOT decimal tenths). Converts to total outs.
    """
    ip_str = str(ip_value)
    whole, _, frac = ip_str.partition(".")
    thirds = int(frac) if frac else 0
    return int(whole) * 3 + thirds
