"""DuckDB connection and schema for the MLB pitcher/batter stats pipeline."""
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mlb_pipeline.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pitcher_season_stats (
    season INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    player_name VARCHAR,
    team_id INTEGER,
    innings_pitched DOUBLE,
    batters_faced INTEGER,
    strikeouts INTEGER,
    walks INTEGER,
    hbp INTEGER,
    home_runs INTEGER,
    flyballs_allowed INTEGER,
    k_pct DOUBLE,
    bb_pct DOUBLE,
    whiff_pct DOUBLE,
    xfip DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (season, player_id)
);

CREATE TABLE IF NOT EXISTS batter_season_stats (
    season INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    player_name VARCHAR,
    team_id INTEGER,
    plate_appearances INTEGER,
    strikeouts INTEGER,
    walks INTEGER,
    k_pct DOUBLE,
    bb_pct DOUBLE,
    whiff_pct DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (season, player_id)
);

CREATE TABLE IF NOT EXISTS league_constants (
    season INTEGER PRIMARY KEY,
    lg_era DOUBLE,
    lg_hr_fb_pct DOUBLE,
    fip_constant DOUBLE,
    flyball_sample_start DATE,
    flyball_sample_end DATE,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fielding_stats (
    player_id INTEGER NOT NULL,
    stat_date DATE NOT NULL,
    season INTEGER NOT NULL,
    player_name VARCHAR,
    team_id INTEGER,
    position VARCHAR,
    innings_played DOUBLE,
    putouts INTEGER,
    assists INTEGER,
    errors INTEGER,
    fielding_pct DOUBLE,
    range_factor DOUBLE,
    oaa INTEGER,
    updated_at TIMESTAMP,
    PRIMARY KEY (player_id, stat_date, position)
);

CREATE TABLE IF NOT EXISTS pitcher_stats (
    player_id INTEGER NOT NULL,
    stat_date DATE NOT NULL,
    season INTEGER NOT NULL,
    player_name VARCHAR,
    team_id INTEGER,
    innings_pitched DOUBLE,
    batters_faced INTEGER,
    earned_runs INTEGER,
    strikeouts INTEGER,
    walks INTEGER,
    hbp INTEGER,
    home_runs INTEGER,
    hits_allowed INTEGER,
    flyballs_allowed INTEGER,
    era DOUBLE,
    whip DOUBLE,
    xfip DOUBLE,
    k_pct DOUBLE,
    bb_pct DOUBLE,
    whiff_pct DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (player_id, stat_date)
);

CREATE TABLE IF NOT EXISTS batter_stats (
    player_id INTEGER NOT NULL,
    stat_date DATE NOT NULL,
    season INTEGER NOT NULL,
    player_name VARCHAR,
    team_id INTEGER,
    plate_appearances INTEGER,
    at_bats INTEGER,
    hits INTEGER,
    doubles INTEGER,
    triples INTEGER,
    home_runs INTEGER,
    walks INTEGER,
    strikeouts INTEGER,
    avg DOUBLE,
    obp DOUBLE,
    slg DOUBLE,
    woba DOUBLE,
    k_pct DOUBLE,
    bb_pct DOUBLE,
    whiff_pct DOUBLE,
    pa_vs_l INTEGER,
    avg_vs_l DOUBLE,
    obp_vs_l DOUBLE,
    slg_vs_l DOUBLE,
    woba_vs_l DOUBLE,
    pa_vs_r INTEGER,
    avg_vs_r DOUBLE,
    obp_vs_r DOUBLE,
    slg_vs_r DOUBLE,
    woba_vs_r DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (player_id, stat_date)
);

CREATE TABLE IF NOT EXISTS todays_starters (
    game_date DATE NOT NULL,
    game_pk INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    is_home BOOLEAN,
    pitcher_id INTEGER,
    pitcher_name VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (game_date, game_pk, team_id)
);
"""


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con
