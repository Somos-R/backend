"""CSV files for the "download" buttons of the list screens."""
import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any

from fastapi import Response

# A cell that starts with one of these is read as a formula by spreadsheets (CSV injection): text a seller
# typed could run in the operator's Excel. A leading quote keeps it text.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return "" if value is None else value


def csv_response(filename: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> Response:
    """A download. UTF-8 with a BOM so Excel shows accents correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
