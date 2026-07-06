"""Fielding stats: basic (MLB Stats API) + OAA (Baseball Savant bulk pull).

Basic fielding (E, FPCT, PO, A, innings) comes from the MLB Stats API per player.
OAA (Outs Above Average) comes from a single bulk Statcast pull per position group,
which is far more efficient than one call per player.
"""
from __future__ import annotations

import pandas as pd
from pybaseball.statcast_fielding import statcast_outs_above_average

from . import mlb_api

# Position codes Baseball Savant accepts for OAA
_OAA_POSITIONS = [3, 4, 5, 6, 7, 8, 9]  # 1B–RF


def get_player_basic_fielding(player_id: int, season: int) -> list[dict]:
    """Returns one dict per position for a player's basic fielding stats."""
    try:
        r = __import__("requests").get(
            f"{mlb_api.BASE_URL}/people/{player_id}/stats",
            params={"stats": "season", "group": "fielding", "season": season},
            timeout=15,
        )
        r.raise_for_status()
    except Exception:
        return []

    results = []
    for stat_group in r.json().get("stats", []):
        for split in stat_group.get("splits", []):
            s = split.get("stat", {})
            pos = split.get("position", {}).get("abbreviation")
            if not pos:
                continue
            ip_str = str(s.get("innings", "0"))
            whole, _, frac = ip_str.partition(".")
            ip = int(whole or 0) + int(frac or 0) / 3
            results.append({
                "position": pos,
                "innings_played": ip,
                "putouts": s.get("putOuts", 0),
                "assists": s.get("assists", 0),
                "errors": s.get("errors", 0),
                "fielding_pct": float(s["fielding"]) if s.get("fielding") else None,
                "range_factor": float(s["rangeFactorPerGame"]) if s.get("rangeFactorPerGame") else None,
            })
    return results


def build_oaa_lookup(year: int) -> dict[int, int]:
    """Returns {player_id: oaa} for all available infield/outfield positions."""
    lookup: dict[int, int] = {}
    for pos in _OAA_POSITIONS:
        try:
            df = statcast_outs_above_average(year, pos, min_att=1)
            if df is None or df.empty:
                continue
            id_col = next((c for c in df.columns if "id" in c.lower() and "player" in c.lower()), None)
            oaa_col = next((c for c in df.columns if "oaa" in c.lower() or "outs_above_average" in c.lower()), None)
            if not id_col or not oaa_col:
                continue
            for _, row in df.iterrows():
                pid = int(row[id_col])
                oaa = int(row[oaa_col]) if pd.notna(row[oaa_col]) else 0
                lookup[pid] = lookup.get(pid, 0) + oaa
        except Exception:
            continue
    return lookup
