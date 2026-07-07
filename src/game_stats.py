"""Game-level stats: team records, pitcher game logs, team batting splits, days rest.

Used by game_duel_builder.py to populate L5/L10 columns and header info.
"""
from __future__ import annotations

import requests
from datetime import datetime, timedelta

from . import mlb_api

BASE_URL = mlb_api.BASE_URL


def get_team_record(team_id: int, date_str: str) -> dict:
    """W-L record, streak, L10 from standings as of date_str."""
    try:
        r = requests.get(
            f"{BASE_URL}/standings",
            params={"leagueId": "103,104", "season": date_str[:4],
                    "date": date_str, "standingsTypes": "regularSeason"},
            timeout=15,
        )
        r.raise_for_status()
        for group in r.json().get("records", []):
            for tr in group.get("teamRecords", []):
                if tr["team"]["id"] != team_id:
                    continue
                streak = tr.get("streak", {}).get("streakCode", "")
                l10 = next(
                    (f"{s['wins']}-{s['losses']}" for s in
                     tr.get("records", {}).get("splitRecords", [])
                     if s.get("type") == "lastTen"),
                    "",
                )
                return {
                    "wins": tr["wins"],
                    "losses": tr["losses"],
                    "pct": tr.get("winningPercentage", ""),
                    "streak": streak,
                    "l10": l10,
                }
    except Exception:
        pass
    return {"wins": "?", "losses": "?", "pct": "", "streak": "", "l10": ""}


def get_days_rest(team_id: int, game_date_str: str) -> int:
    """Days since team's last completed game."""
    try:
        end = (datetime.strptime(game_date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        start = (datetime.strptime(game_date_str, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
        r = requests.get(
            f"{BASE_URL}/schedule",
            params={"teamId": team_id, "season": game_date_str[:4], "sportId": 1,
                    "gameType": "R", "startDate": start, "endDate": end},
            timeout=15,
        )
        r.raise_for_status()
        dates = [d["date"] for d in r.json().get("dates", [])]
        if not dates:
            return 1
        last = datetime.strptime(sorted(dates)[-1], "%Y-%m-%d")
        game = datetime.strptime(game_date_str, "%Y-%m-%d")
        return max(1, (game - last).days)
    except Exception:
        return 1


def _parse_ip(val) -> float:
    try:
        s = str(val)
        w, _, f = s.partition(".")
        return int(w or 0) + int(f or 0) / 3
    except Exception:
        return 0.0


def get_pitcher_splits(player_id: int, season: int) -> dict:
    """Season / L5 / L10 aggregated from game log.
    Each bucket: era, k_per_g, hits_per_g, bb_per_g, hr_per_9, ip, games
    """
    try:
        r = requests.get(
            f"{BASE_URL}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": season},
            timeout=15,
        )
        r.raise_for_status()
        splits = []
        for sg in r.json().get("stats", []):
            splits.extend(sg.get("splits", []))
        splits.sort(key=lambda x: x.get("date", ""), reverse=True)
    except Exception:
        return {"season": {}, "l5": {}, "l10": {}}

    def agg(games: list) -> dict:
        if not games:
            return {}
        ip = sum(_parse_ip(g["stat"].get("inningsPitched", 0)) for g in games)
        er = sum(g["stat"].get("earnedRuns", 0) for g in games)
        k  = sum(g["stat"].get("strikeOuts", 0) for g in games)
        h  = sum(g["stat"].get("hits", 0) for g in games)
        bb = sum(g["stat"].get("baseOnBalls", 0) for g in games)
        hr = sum(g["stat"].get("homeRuns", 0) for g in games)
        n  = len(games)
        return {
            "era":        round(er / ip * 9, 2) if ip else None,
            "k_per_g":    round(k / n, 1)       if n  else None,
            "hits_per_g": round(h / n, 1)        if n  else None,
            "bb_per_g":   round(bb / n, 1)       if n  else None,
            "hr_per_9":   round(hr / ip * 9, 2) if ip else None,
            "ip": round(ip, 1),
            "games": n,
        }

    return {"season": agg(splits), "l5": agg(splits[:5]), "l10": agg(splits[:10])}


def _get_last_n_game_dates(team_id: int, season: int, before_date: str, n: int) -> list[str]:
    end = (datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{BASE_URL}/schedule",
            params={"teamId": team_id, "season": season, "sportId": 1,
                    "gameType": "R", "startDate": start, "endDate": end},
            timeout=15,
        )
        r.raise_for_status()
        dates = sorted(
            d["date"] for d in r.json().get("dates", [])
            if any(g.get("status", {}).get("abstractGameState") == "Final"
                   for g in d.get("games", []))
        )
        return dates[-n:]
    except Exception:
        return []


def get_team_batting_splits(team_id: int, season: int, date_str: str) -> dict:
    """Season / L5 / L10 batting stats: avg, rpg, hpg, k_pct, bb_pct."""
    game_dates = _get_last_n_game_dates(team_id, season, date_str, 10)

    def fetch(start_date: str) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/teams/{team_id}/stats",
                params={"stats": "byDateRange", "group": "hitting",
                        "season": season, "startDate": start_date,
                        "endDate": date_str, "gameType": "R"},
                timeout=15,
            )
            r.raise_for_status()
            for sg in r.json().get("stats", []):
                for split in sg.get("splits", []):
                    s = split.get("stat", {})
                    pa = s.get("plateAppearances") or 1
                    gp = s.get("gamesPlayed") or 1
                    return {
                        "avg":   float(s["avg"])  if s.get("avg")  else None,
                        "obp":   float(s["obp"])  if s.get("obp")  else None,
                        "slg":   float(s["slg"])  if s.get("slg")  else None,
                        "rpg":   round(s.get("runs", 0) / gp, 2),
                        "hpg":   round(s.get("hits", 0) / gp, 1),
                        "k_pct": round(s.get("strikeOuts", 0) / pa, 3),
                        "bb_pct":round(s.get("baseOnBalls", 0) / pa, 3),
                        "games": gp,
                    }
        except Exception:
            pass
        return {}

    l5_start  = game_dates[-5]  if len(game_dates) >= 5  else None
    l10_start = game_dates[-10] if len(game_dates) >= 10 else None

    return {
        "season": fetch(f"{season}-03-01"),
        "l5":     fetch(l5_start)  if l5_start  else {},
        "l10":    fetch(l10_start) if l10_start else {},
    }
