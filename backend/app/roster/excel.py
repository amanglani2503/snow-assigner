import io
from datetime import date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.roster.generator import days_in_month

VALID = ["M", "E", "OFF", "PL", "CH"]
HEADER_ROW, FIRST_ROW, FIRST_DAY_COL = 4, 5, 3     # day columns start at index 3 (column D)


def build_template(month: str, members, roster: dict) -> bytes:
    days = days_in_month(month)
    wb = Workbook()
    ws = wb.active
    ws.title = "Rota"
    ws["A1"], ws["B1"] = "Month", month
    ws["A2"] = "Fill each day with M, E, OFF, PL or CH. Do not edit columns A-C or row 4."
    for i, d in enumerate(days):
        ws.cell(row=3, column=FIRST_DAY_COL + 1 + i, value=d.strftime("%a"))
    ws.append([])  # keep row numbering explicit below
    headers = ["Member ID", "Name", "Group"] + [d.isoformat() for d in days]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=HEADER_ROW, column=c, value=h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDE3F0")

    for r, m in enumerate(members, start=FIRST_ROW):
        ws.cell(row=r, column=1, value=m.id)
        ws.cell(row=r, column=2, value=m.name)
        ws.cell(row=r, column=3, value=m.group)
        for i, d in enumerate(days):
            existing = roster.get(m.id, {}).get(d.isoformat())
            ws.cell(row=r, column=FIRST_DAY_COL + 1 + i,
                    value=existing or ("OFF" if d.weekday() >= 5 else None))

    last_col = get_column_letter(FIRST_DAY_COL + len(days))
    dv = DataValidation(type="list", formula1='"' + ",".join(VALID) + '"', allow_blank=True,
                        showErrorMessage=True, error="Use M, E, OFF, PL or CH")
    ws.add_data_validation(dv)
    dv.add(f"D{FIRST_ROW}:{last_col}{FIRST_ROW + len(members) - 1}")
    ws.freeze_panes = "D5"
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 16
    for i in range(len(days)):
        ws.column_dimensions[get_column_letter(FIRST_DAY_COL + 1 + i)].width = 11

    info = wb.create_sheet("Instructions")
    for row in [
        ["Code", "Meaning"],
        ["M", "Morning shift 09:00-18:30"], ["E", "Evening shift 12:30-22:00"],
        ["OFF", "Weekly off / weekend"], ["PL", "Planned leave"],
        ["CH", "Company holiday. If EVERY member has CH on a day, it is a company-wide holiday"],
        [], ["Blank cells are treated as OFF."],
        ["New joiners: download a fresh template so they appear as rows."],
    ]:
        info.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_template(fileobj, members, month: str):
    """Returns ({member_id: {iso_day: code}}, warnings). Raises ValueError with a readable message."""
    try:
        wb = load_workbook(fileobj, data_only=True)
    except Exception:
        raise ValueError("Not a valid .xlsx file")
    ws = wb["Rota"] if "Rota" in wb.sheetnames else wb.active
    file_month = str(ws["B1"].value or "").strip()
    if file_month != month:
        raise ValueError(f"This file is for month '{file_month}', but you are uploading to {month}")

    day_cols = {}
    for i, h in enumerate([c.value for c in ws[HEADER_ROW]]):
        if i < FIRST_DAY_COL or h is None:
            continue
        s = h.date().isoformat() if hasattr(h, "date") else str(h).strip()
        if not s.startswith(month):
            raise ValueError(f"Column header '{s}' is not a day of {month}")
        day_cols[i] = s
    if not day_cols:
        raise ValueError("No day columns found in row 4")

    ids = {m.id for m in members}
    by_name = {m.name.strip().lower(): m.id for m in members}
    result, errors, warnings = {}, [], []
    for row in ws.iter_rows(min_row=FIRST_ROW, values_only=True):
        if not row or not any(row[:3]):
            continue
        mid = str(row[0] or "").strip()
        if mid not in ids:
            mid = by_name.get(str(row[1] or "").strip().lower())
        if not mid:
            errors.append(f"Unknown member '{row[1]}' ({row[0]})")
            continue
        codes = {}
        for i, day in day_cols.items():
            v = row[i] if i < len(row) else None
            code = "OFF" if v in (None, "") else str(v).strip().upper()
            if code not in VALID:
                errors.append(f"{row[1]} {day}: '{v}' is not one of {', '.join(VALID)}")
                continue
            codes[day] = code
        result[mid] = codes
    if errors:
        raise ValueError("\n".join(errors[:20]) + (f"\n...and {len(errors) - 20} more" if len(errors) > 20 else ""))
    missing = [m.name for m in members if m.id not in result]
    if missing:
        warnings.append("Not in the file (kept as before / OFF): " + ", ".join(missing))
    return result, warnings