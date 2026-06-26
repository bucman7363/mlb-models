"""Pull and store season-to-date stats for a single pitcher or batter.

Counting stats (K, BB, HBP, HR, IP, PA) come from the official MLB Stats API.
Whiff% and flyball counts come from Baseball Savant pitch-level data
(pybaseball), since FanGraphs blocks scraping. See league_constants.py for
why xFIP is self-computed rather than pulled from FanGraphs directly.
"""
from __future__ import annotations

import datetime as dt

import duckdb

from . import league_constants, mlb_api, statcast_metrics

SEASON_START = "{season}-03-01"


def _stat_float(value) -> float | None:
    """MLB Stats API returns rate stats as strings like '.280' or '3.45'."""
    try:
        return float(value) if value not in (None, "", "-.---", "-.--") else None
    except (ValueError, TypeError):
        return None


def pull_pitcher_season_stats(
    con: duckdb.DuckDBPyConnection, player_id: int, player_name: str, team_id: int, season: int
) -> dict | None:
    stat = mlb_api.get_player_season_stat(player_id, season, "pitching")
    if not stat or not stat.get("battersFaced"):
        return None

    bf = stat["battersFaced"]
    k = stat["strikeOuts"]
    bb = stat["baseOnBalls"]
    hbp = stat["hitByPitch"]
    hr = stat["homeRuns"]
    ip = mlb_api.innings_pitched_to_outs(stat["inningsPitched"]) / 3

    start_date = SEASON_START.format(season=season)
    end_date = dt.date.today().isoformat()
    pitch_log = statcast_metrics.get_pitcher_pitch_log(player_id, start_date, end_date)
    whiff = statcast_metrics.whiff_pct(pitch_log)
    fb = statcast_metrics.flyballs_allowed(pitch_log)

    consts = league_constants.get_league_constants(con, season)
    xfip = (
        (13 * fb * consts["lg_hr_fb_pct"] + 3 * (bb + hbp) - 2 * k) / ip + consts["fip_constant"]
        if ip
        else None
    )

    row = {
        "season": season,
        "player_id": player_id,
        "player_name": player_name,
        "team_id": team_id,
        "innings_pitched": ip,
        "batters_faced": bf,
        "strikeouts": k,
        "walks": bb,
        "hbp": hbp,
        "home_runs": hr,
        "flyballs_allowed": fb,
        "k_pct": k / bf,
        "bb_pct": bb / bf,
        "whiff_pct": whiff,
        "xfip": xfip,
    }
    con.execute(
        """
        INSERT INTO pitcher_season_stats
            (season, player_id, player_name, team_id, innings_pitched, batters_faced,
             strikeouts, walks, hbp, home_runs, flyballs_allowed, k_pct, bb_pct, whiff_pct, xfip, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (season, player_id) DO UPDATE SET
            player_name = excluded.player_name, team_id = excluded.team_id,
            innings_pitched = excluded.innings_pitched, batters_faced = excluded.batters_faced,
            strikeouts = excluded.strikeouts, walks = excluded.walks, hbp = excluded.hbp,
            home_runs = excluded.home_runs, flyballs_allowed = excluded.flyballs_allowed,
            k_pct = excluded.k_pct, bb_pct = excluded.bb_pct, whiff_pct = excluded.whiff_pct,
            xfip = excluded.xfip, updated_at = excluded.updated_at
        """,
        list(row.values()),
    )
    return row


def pull_batter_season_stats(
    con: duckdb.DuckDBPyConnection, player_id: int, player_name: str, team_id: int, season: int
) -> dict | None:
    stat = mlb_api.get_player_season_stat(player_id, season, "hitting")
    if not stat or not stat.get("plateAppearances"):
        return None

    pa = stat["plateAppearances"]
    k = stat["strikeOuts"]
    bb = stat["baseOnBalls"]

    start_date = SEASON_START.format(season=season)
    end_date = dt.date.today().isoformat()
    pitch_log = statcast_metrics.get_batter_pitch_log(player_id, start_date, end_date)
    whiff = statcast_metrics.whiff_pct(pitch_log)

    row = {
        "season": season,
        "player_id": player_id,
        "player_name": player_name,
        "team_id": team_id,
        "plate_appearances": pa,
        "strikeouts": k,
        "walks": bb,
        "k_pct": k / pa,
        "bb_pct": bb / pa,
        "whiff_pct": whiff,
    }
    con.execute(
        """
        INSERT INTO batter_season_stats
            (season, player_id, player_name, team_id, plate_appearances,
             strikeouts, walks, k_pct, bb_pct, whiff_pct, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (season, player_id) DO UPDATE SET
            player_name = excluded.player_name, team_id = excluded.team_id,
            plate_appearances = excluded.plate_appearances, strikeouts = excluded.strikeouts,
            walks = excluded.walks, k_pct = excluded.k_pct, bb_pct = excluded.bb_pct,
            whiff_pct = excluded.whiff_pct, updated_at = excluded.updated_at
        """,
        list(row.values()),
    )
    return row


def pull_pitcher_daily_stats(
    con: duckdb.DuckDBPyConnection,
    player_id: int,
    player_name: str,
    team_id: int,
    season: int,
    stat_date: dt.date,
) -> dict | None:
    stat = mlb_api.get_player_season_stat(player_id, season, "pitching")
    if not stat or not stat.get("battersFaced"):
        return None

    bf = stat["battersFaced"]
    k = stat["strikeOuts"]
    bb = stat["baseOnBalls"]
    hbp = stat["hitByPitch"]
    hr = stat["homeRuns"]
    earned_runs = stat.get("earnedRuns", 0)
    hits = stat.get("hits", 0)
    ip = mlb_api.innings_pitched_to_outs(stat["inningsPitched"]) / 3

    era = _stat_float(stat.get("era")) or (9 * earned_runs / ip if ip else None)
    whip = _stat_float(stat.get("whip")) or ((bb + hits) / ip if ip else None)

    start_date = SEASON_START.format(season=season)
    pitch_log = statcast_metrics.get_pitcher_pitch_log(player_id, start_date, stat_date.isoformat())
    whiff = statcast_metrics.whiff_pct(pitch_log)
    fb = statcast_metrics.flyballs_allowed(pitch_log)

    consts = league_constants.get_league_constants(con, season)
    xfip = (
        (13 * fb * consts["lg_hr_fb_pct"] + 3 * (bb + hbp) - 2 * k) / ip + consts["fip_constant"]
        if ip
        else None
    )

    row = {
        "player_id": player_id,
        "stat_date": stat_date,
        "season": season,
        "player_name": player_name,
        "team_id": team_id,
        "innings_pitched": ip,
        "batters_faced": bf,
        "earned_runs": earned_runs,
        "strikeouts": k,
        "walks": bb,
        "hbp": hbp,
        "home_runs": hr,
        "hits_allowed": hits,
        "flyballs_allowed": fb,
        "era": era,
        "whip": whip,
        "xfip": xfip,
        "k_pct": k / bf if bf else None,
        "bb_pct": bb / bf if bf else None,
        "whiff_pct": whiff,
    }
    con.execute(
        """
        INSERT INTO pitcher_stats
            (player_id, stat_date, season, player_name, team_id, innings_pitched,
             batters_faced, earned_runs, strikeouts, walks, hbp, home_runs,
             hits_allowed, flyballs_allowed, era, whip, xfip, k_pct, bb_pct, whiff_pct, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (player_id, stat_date) DO UPDATE SET
            season = excluded.season, player_name = excluded.player_name,
            team_id = excluded.team_id, innings_pitched = excluded.innings_pitched,
            batters_faced = excluded.batters_faced, earned_runs = excluded.earned_runs,
            strikeouts = excluded.strikeouts, walks = excluded.walks, hbp = excluded.hbp,
            home_runs = excluded.home_runs, hits_allowed = excluded.hits_allowed,
            flyballs_allowed = excluded.flyballs_allowed, era = excluded.era,
            whip = excluded.whip, xfip = excluded.xfip, k_pct = excluded.k_pct,
            bb_pct = excluded.bb_pct, whiff_pct = excluded.whiff_pct,
            updated_at = excluded.updated_at
        """,
        list(row.values()),
    )
    return row


def pull_batter_daily_stats(
    con: duckdb.DuckDBPyConnection,
    player_id: int,
    player_name: str,
    team_id: int,
    season: int,
    stat_date: dt.date,
) -> dict | None:
    stat = mlb_api.get_player_season_stat(player_id, season, "hitting")
    if not stat or not stat.get("plateAppearances"):
        return None

    pa = stat["plateAppearances"]
    bb = stat["baseOnBalls"]
    k = stat["strikeOuts"]

    start_date = SEASON_START.format(season=season)
    pitch_log = statcast_metrics.get_batter_pitch_log(player_id, start_date, stat_date.isoformat())
    whiff = statcast_metrics.whiff_pct(pitch_log)
    woba = statcast_metrics.woba_from_pitch_log(pitch_log)
    woba_vs_l = statcast_metrics.woba_from_pitch_log(pitch_log, p_throws_filter="L")
    woba_vs_r = statcast_metrics.woba_from_pitch_log(pitch_log, p_throws_filter="R")

    split_l = mlb_api.get_player_split_stats(player_id, season, "vl")
    split_r = mlb_api.get_player_split_stats(player_id, season, "vr")

    def _split_int(d, key):
        return int(d[key]) if d and d.get(key) is not None else None

    def _split_float(d, key):
        return _stat_float(d.get(key)) if d else None

    row = {
        "player_id": player_id,
        "stat_date": stat_date,
        "season": season,
        "player_name": player_name,
        "team_id": team_id,
        "plate_appearances": pa,
        "at_bats": stat.get("atBats"),
        "hits": stat.get("hits"),
        "doubles": stat.get("doubles"),
        "triples": stat.get("triples"),
        "home_runs": stat.get("homeRuns"),
        "walks": bb,
        "strikeouts": k,
        "avg": _stat_float(stat.get("avg")),
        "obp": _stat_float(stat.get("obp")),
        "slg": _stat_float(stat.get("slg")),
        "woba": woba,
        "k_pct": k / pa if pa else None,
        "bb_pct": bb / pa if pa else None,
        "whiff_pct": whiff,
        "pa_vs_l": _split_int(split_l, "plateAppearances"),
        "avg_vs_l": _split_float(split_l, "avg"),
        "obp_vs_l": _split_float(split_l, "obp"),
        "slg_vs_l": _split_float(split_l, "slg"),
        "woba_vs_l": woba_vs_l,
        "pa_vs_r": _split_int(split_r, "plateAppearances"),
        "avg_vs_r": _split_float(split_r, "avg"),
        "obp_vs_r": _split_float(split_r, "obp"),
        "slg_vs_r": _split_float(split_r, "slg"),
        "woba_vs_r": woba_vs_r,
    }
    con.execute(
        """
        INSERT INTO batter_stats
            (player_id, stat_date, season, player_name, team_id, plate_appearances,
             at_bats, hits, doubles, triples, home_runs, walks, strikeouts,
             avg, obp, slg, woba, k_pct, bb_pct, whiff_pct,
             pa_vs_l, avg_vs_l, obp_vs_l, slg_vs_l, woba_vs_l,
             pa_vs_r, avg_vs_r, obp_vs_r, slg_vs_r, woba_vs_r, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (player_id, stat_date) DO UPDATE SET
            season = excluded.season, player_name = excluded.player_name,
            team_id = excluded.team_id, plate_appearances = excluded.plate_appearances,
            at_bats = excluded.at_bats, hits = excluded.hits, doubles = excluded.doubles,
            triples = excluded.triples, home_runs = excluded.home_runs,
            walks = excluded.walks, strikeouts = excluded.strikeouts,
            avg = excluded.avg, obp = excluded.obp, slg = excluded.slg,
            woba = excluded.woba, k_pct = excluded.k_pct, bb_pct = excluded.bb_pct,
            whiff_pct = excluded.whiff_pct, pa_vs_l = excluded.pa_vs_l,
            avg_vs_l = excluded.avg_vs_l, obp_vs_l = excluded.obp_vs_l,
            slg_vs_l = excluded.slg_vs_l, woba_vs_l = excluded.woba_vs_l,
            pa_vs_r = excluded.pa_vs_r, avg_vs_r = excluded.avg_vs_r,
            obp_vs_r = excluded.obp_vs_r, slg_vs_r = excluded.slg_vs_r,
            woba_vs_r = excluded.woba_vs_r, updated_at = excluded.updated_at
        """,
        list(row.values()),
    )
    return row
