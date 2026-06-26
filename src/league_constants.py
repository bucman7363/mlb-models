"""League-wide constants needed for xFIP: the HR/FB rate and the FIP
constant (the offset that scales FIP onto the same scale as league ERA).

cFIP and HR/FB% are official FanGraphs concepts but FanGraphs itself blocks
scraping, so we compute equivalents ourselves:
  - lgERA, HR, BB, HBP, K, IP come from summing official MLB Stats API
    team-season pitching totals (exact, no sampling).
  - lg HR/FB% comes from a rolling recent-window sample of league-wide
    Statcast pitches (Baseball Savant), since flyball classification isn't
    in the MLB Stats API and pulling a full season of league-wide pitch
    data is too large to be practical here.
Results are cached per season in duckdb.
"""
from __future__ import annotations

import datetime as dt

import duckdb
from pybaseball import statcast

from . import mlb_api

FALLBACK_LG_HR_FB_PCT = 0.115  # used only if the Statcast sample comes back empty


def _compute_lg_era_and_fip_constant(season: int) -> tuple[float, float, float]:
    teams = mlb_api.get_teams(season)
    total_er = total_outs = total_hr = total_bb = total_hbp = total_k = 0
    for team in teams:
        stat = mlb_api.get_team_pitching_totals(team["id"], season)
        if not stat:
            continue
        total_er += stat["earnedRuns"]
        total_outs += mlb_api.innings_pitched_to_outs(stat["inningsPitched"])
        total_hr += stat["homeRuns"]
        total_bb += stat["baseOnBalls"]
        total_hbp += stat["hitByPitch"]
        total_k += stat["strikeOuts"]

    innings = total_outs / 3
    lg_era = 9 * total_er / innings
    fip_no_constant = (13 * total_hr + 3 * (total_bb + total_hbp) - 2 * total_k) / innings
    fip_constant = lg_era - fip_no_constant
    return lg_era, fip_constant, innings


def _compute_lg_hr_fb_pct(sample_days: int) -> tuple[float, dt.date, dt.date]:
    end_date = dt.date.today() - dt.timedelta(days=1)
    start_date = end_date - dt.timedelta(days=sample_days)
    df = statcast(start_date.isoformat(), end_date.isoformat())
    if df is None or df.empty or "bb_type" not in df:
        return FALLBACK_LG_HR_FB_PCT, start_date, end_date

    is_flyball = df["bb_type"] == "fly_ball"
    flyballs = int(is_flyball.sum())
    home_runs_on_flyballs = int((is_flyball & (df["events"] == "home_run")).sum())
    if flyballs == 0:
        return FALLBACK_LG_HR_FB_PCT, start_date, end_date
    return home_runs_on_flyballs / flyballs, start_date, end_date


def get_league_constants(
    con: duckdb.DuckDBPyConnection, season: int, sample_days: int = 21, force_refresh: bool = False
) -> dict:
    if not force_refresh:
        row = con.execute(
            "SELECT lg_era, lg_hr_fb_pct, fip_constant FROM league_constants WHERE season = ?",
            [season],
        ).fetchone()
        if row:
            return {"lg_era": row[0], "lg_hr_fb_pct": row[1], "fip_constant": row[2]}

    lg_era, fip_constant, _ = _compute_lg_era_and_fip_constant(season)
    lg_hr_fb_pct, sample_start, sample_end = _compute_lg_hr_fb_pct(sample_days)

    con.execute(
        """
        INSERT INTO league_constants
            (season, lg_era, lg_hr_fb_pct, fip_constant, flyball_sample_start, flyball_sample_end, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (season) DO UPDATE SET
            lg_era = excluded.lg_era,
            lg_hr_fb_pct = excluded.lg_hr_fb_pct,
            fip_constant = excluded.fip_constant,
            flyball_sample_start = excluded.flyball_sample_start,
            flyball_sample_end = excluded.flyball_sample_end,
            updated_at = excluded.updated_at
        """,
        [season, lg_era, lg_hr_fb_pct, fip_constant, sample_start, sample_end],
    )
    return {"lg_era": lg_era, "lg_hr_fb_pct": lg_hr_fb_pct, "fip_constant": fip_constant}
