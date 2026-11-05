"""Google Sheets as a lightweight CRM.

Expected header row (first worksheet): id | name | company | email | notes | deal_value | status
"""
import asyncio
import json
import os

from ..config import settings

COLUMNS = ["id", "name", "company", "email", "notes", "deal_value", "status"]

# Fictional sample leads for demo mode.
_MOCK_LEADS = [
    {"id": "L-101", "name": "Dana Rivera", "company": "Northwind Robotics (demo)", "email": "dana@example.com",
     "notes": "Inbound: wants to automate warehouse ops reporting, 40 staff, budget approved",
     "deal_value": "4800", "status": "New"},
    {"id": "L-102", "name": "Sam Okafor", "company": "Bluebird Clinics (demo)", "email": "sam@example.com",
     "notes": "Webinar attendee, exploring options for next year", "deal_value": "2400", "status": "New"},
    {"id": "L-103", "name": "Priya Nair", "company": "Kite Logistics (demo)", "email": "priya@example.com",
     "notes": "Referral from partner, urgent: replacing legacy tool this quarter",
     "deal_value": "9600", "status": "New"},
]
_mock_state = [dict(x) for x in _MOCK_LEADS]


def mode() -> str:
    return settings.modes()["sheets"]


def _client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = settings.google_sa_json
    info = json.load(open(raw)) if os.path.exists(raw) else json.loads(raw)
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds).open_by_key(settings.sheet_id).sheet1


def _real_list() -> list[dict]:
    ws = _client()
    return [{k: str(v) for k, v in r.items()} for r in ws.get_all_records()]


def _real_update(row_id: str, column: str, value: str) -> None:
    ws = _client()
    header = [h.strip().lower() for h in ws.row_values(1)]
    if column.lower() not in header:
        raise KeyError(f"column '{column}' not in sheet header {header}")
    id_col, col = header.index("id") + 1, header.index(column.lower()) + 1
    cell = ws.find(row_id, in_column=id_col)
    if cell is None:
        raise KeyError(f"row {row_id} not found in sheet")
    ws.update_cell(cell.row, col, value)


def _real_header() -> list[str]:
    return [h.strip() for h in _client().row_values(1)]


async def list_leads() -> list[dict]:
    if mode() == "mock":
        return [dict(x) for x in _mock_state]
    return await asyncio.to_thread(_real_list)


async def next_new_lead() -> dict | None:
    for lead in await list_leads():
        if str(lead.get("status", "")).strip().lower() == "new":
            return lead
    return None


async def update_cell(row_id: str, column: str, value: str) -> dict:
    if mode() == "mock":
        for lead in _mock_state:
            if lead["id"] == row_id:
                lead[column.lower()] = value
        return {"mode": "mock", "row_id": row_id, "column": column, "value": value}
    await asyncio.to_thread(_real_update, row_id, column, value)
    return {"mode": "google", "row_id": row_id, "column": column, "value": value}


async def update_status(lead_id: str, status: str) -> dict:
    out = await update_cell(lead_id, "status", status)
    return {**out, "lead_id": lead_id, "status": status}


async def columns() -> list[str]:
    if mode() == "mock":
        return list(COLUMNS)
    return await asyncio.to_thread(_real_header)


def matches(row: dict, where: dict) -> bool:
    """Case-insensitive equality on every column in `where` (missing columns never match)."""
    for k, v in (where or {}).items():
        actual = next((val for col, val in row.items() if col.lower() == str(k).lower()), None)
        if actual is None or str(actual).strip().lower() != str(v).strip().lower():
            return False
    return True


def reset_mock() -> None:
    _mock_state[:] = [dict(x) for x in _MOCK_LEADS]
