"""Pitch-level metrics from Baseball Savant (via pybaseball's statcast endpoints).

MLB Stats API gives us clean official counting stats (K, BB, HBP, HR, IP, PA),
but whiff% and flyball counts require raw pitch-level Statcast data, which
FanGraphs-style scraping can't provide since FanGraphs blocks automated
requests (Cloudflare). Baseball Savant has no such block.
"""
from __future__ import annotations

import pandas as pd
from pybaseball import statcast_batter, statcast_pitcher

WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
SWING_DESCRIPTIONS = WHIFF_DESCRIPTIONS | {"foul", "foul_tip", "foul_bunt", "hit_into_play"}


def whiff_pct(pitch_df: pd.DataFrame) -> float | None:
    if pitch_df.empty:
        return None
    swings = pitch_df["description"].isin(SWING_DESCRIPTIONS).sum()
    if swings == 0:
        return None
    whiffs = pitch_df["description"].isin(WHIFF_DESCRIPTIONS).sum()
    return whiffs / swings


def flyballs_allowed(pitch_df: pd.DataFrame) -> int:
    if pitch_df.empty or "bb_type" not in pitch_df:
        return 0
    return int((pitch_df["bb_type"] == "fly_ball").sum())


def woba_from_pitch_log(pitch_df: pd.DataFrame, p_throws_filter: str | None = None) -> float | None:
    """Compute wOBA from Statcast pitch log using per-PA woba_value/woba_denom columns.

    p_throws_filter: 'L' or 'R' to restrict to platoon split; None for overall.
    """
    if pitch_df.empty or "woba_value" not in pitch_df.columns or "woba_denom" not in pitch_df.columns:
        return None
    df = pitch_df if p_throws_filter is None else pitch_df[pitch_df["p_throws"] == p_throws_filter]
    denom = df["woba_denom"].sum()
    return float(df["woba_value"].sum() / denom) if denom else None


def get_pitcher_pitch_log(player_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    df = statcast_pitcher(start_date, end_date, player_id)
    return df if df is not None else pd.DataFrame()


def get_batter_pitch_log(player_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    df = statcast_batter(start_date, end_date, player_id)
    return df if df is not None else pd.DataFrame()
