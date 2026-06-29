"""Push today's pitcher_stats and batter_stats to Google Sheets.

Writes two tabs — "Pitcher Stats" and "Batter Stats" — into a single
spreadsheet. The spreadsheet ID is stored in credentials/sheets_id.txt
after first creation so subsequent runs use open_by_key (Sheets API only,
no Drive API search needed).

Credentials: place your service account JSON key at
    credentials/google_service_account.json
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import gspread
from google.oauth2.service_account import Credentials

CREDS_PATH = Path(__file__).resolve().parent.parent / "credentials" / "google_service_account.json"
SHEETS_ID_PATH = Path(__file__).resolve().parent.parent / "credentials" / "sheets_id.txt"
SPREADSHEET_TITLE = "MLB Pipeline Stats"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(str(CREDS_PATH), scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_spreadsheet(gc: gspread.Client, owner_email: str | None = None) -> gspread.Spreadsheet:
    if SHEETS_ID_PATH.exists():
        return gc.open_by_key(SHEETS_ID_PATH.read_text().strip())

    sh = gc.create(SPREADSHEET_TITLE)
    SHEETS_ID_PATH.write_text(sh.id)
    if owner_email:
        sh.share(owner_email, perm_type="user", role="writer")
    print(f"Created spreadsheet: {sh.url}")
    return sh


def _serialize(v):
    import datetime
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _write_tab(sh: gspread.Spreadsheet, tab_title: str, rows: list[list]) -> None:
    try:
        ws = sh.worksheet(tab_title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_title, rows=1, cols=1)

    ws.clear()
    if rows:
        serialized = [[_serialize(v) for v in row] for row in rows]
        ws.update(serialized, value_input_option="USER_ENTERED")


def push_daily_stats(
    con: duckdb.DuckDBPyConnection, stat_date: str, owner_email: str | None = None
) -> str:
    """Write pitcher_stats and batter_stats for stat_date to Google Sheets.

    Returns the spreadsheet URL.
    """
    gc = _get_client()
    sh = _get_or_create_spreadsheet(gc, owner_email)

    # --- pitcher tab ---
    pitcher_cols = [
        "player_name", "team_id", "innings_pitched", "batters_faced",
        "era", "whip", "xfip", "k_pct", "bb_pct", "whiff_pct",
        "earned_runs", "strikeouts", "walks", "hbp", "home_runs",
        "hits_allowed", "flyballs_allowed", "player_id", "season", "stat_date",
    ]
    pitcher_rows = con.execute(
        f"SELECT {', '.join(pitcher_cols)} FROM pitcher_stats "
        f"WHERE stat_date = ? ORDER BY player_name",
        [stat_date],
    ).fetchall()
    _write_tab(sh, f"Pitchers {stat_date}", [pitcher_cols] + [list(r) for r in pitcher_rows])
    print(f"Pitchers {stat_date} tab: {len(pitcher_rows)} rows")

    # --- batter tab ---
    batter_cols = [
        "player_name", "team_id", "plate_appearances", "at_bats", "hits",
        "avg", "obp", "slg", "woba", "k_pct", "bb_pct", "whiff_pct",
        "doubles", "triples", "home_runs", "walks", "strikeouts",
        "pa_vs_l", "avg_vs_l", "obp_vs_l", "slg_vs_l", "woba_vs_l",
        "pa_vs_r", "avg_vs_r", "obp_vs_r", "slg_vs_r", "woba_vs_r",
        "player_id", "season", "stat_date",
    ]
    batter_rows = con.execute(
        f"SELECT {', '.join(batter_cols)} FROM batter_stats "
        f"WHERE stat_date = ? ORDER BY player_name",
        [stat_date],
    ).fetchall()
    _write_tab(sh, f"Batters {stat_date}", [batter_cols] + [list(r) for r in batter_rows])
    print(f"Batters {stat_date} tab:  {len(batter_rows)} rows")

    # remove default blank Sheet1 if it still exists
    try:
        sh.del_worksheet(sh.worksheet("Sheet1"))
    except (gspread.WorksheetNotFound, gspread.exceptions.APIError):
        pass

    return sh.url
