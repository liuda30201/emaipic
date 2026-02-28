from __future__ import annotations

import sqlite3
from typing import Any

from libs.common.models import SearchFilters


def search_invoices(connection: sqlite3.Connection, filters: SearchFilters) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    query = """
        SELECT
            batch_id,
            page_id,
            file_id,
            doc_id,
            page_no,
            image_relpath,
            thumb_relpath,
            original_relpath,
            invoice_no,
            invoice_date,
            buyer_name,
            seller_name,
            service_name,
            amount,
            tax,
            total,
            extraction_id,
            indexed_at
        FROM invoices
    """
    clauses: list[str] = []
    params: list[Any] = []
    compact = filters.compact()

    if "batch_id" in compact:
        clauses.append("batch_id = ?")
        params.append(compact["batch_id"])
    if "start_date" in compact:
        clauses.append("invoice_date >= ?")
        params.append(compact["start_date"])
    if "end_date" in compact:
        clauses.append("invoice_date <= ?")
        params.append(compact["end_date"])
    if "buyer" in compact:
        clauses.append("buyer_name LIKE ?")
        params.append(f"%{compact['buyer']}%")
    if "seller" in compact:
        clauses.append("seller_name LIKE ?")
        params.append(f"%{compact['seller']}%")
    if "invoice_no" in compact:
        clauses.append("invoice_no LIKE ?")
        params.append(f"%{compact['invoice_no']}%")
    if "min_total" in compact:
        clauses.append("CAST(total AS REAL) >= CAST(? AS REAL)")
        params.append(compact["min_total"])
    if "max_total" in compact:
        clauses.append("CAST(total AS REAL) <= CAST(? AS REAL)")
        params.append(compact["max_total"])

    if clauses:
        query = f"{query} WHERE {' AND '.join(clauses)}"
    query = f"{query} ORDER BY COALESCE(invoice_date, '') DESC, page_id ASC"

    rows = connection.execute(query, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["thumb_url"] = f"/api/assets/thumb/{item['batch_id']}/{item['page_id']}"
        item["image_url"] = f"/api/assets/image/{item['batch_id']}/{item['page_id']}"
        results.append(item)
    return results
