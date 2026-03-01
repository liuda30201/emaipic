from __future__ import annotations

import sqlite3
from typing import Any

from libs.common.models import SearchFilters


def _where_clauses(filters: SearchFilters) -> tuple[list[str], list[Any]]:
    clauses = ["i.is_invoice = 1"]
    params: list[Any] = []
    compact = filters.compact()

    if "batch_id" in compact:
        clauses.append("i.batch_id = ?")
        params.append(compact["batch_id"])
    if "start_date" in compact:
        clauses.append("i.date >= ?")
        params.append(compact["start_date"])
    if "end_date" in compact:
        clauses.append("i.date <= ?")
        params.append(compact["end_date"])
    if "buyer" in compact:
        clauses.append("i.buyer LIKE ?")
        params.append(f"%{compact['buyer']}%")
    if "seller" in compact:
        clauses.append("i.seller LIKE ?")
        params.append(f"%{compact['seller']}%")
    if "invoice_no" in compact:
        clauses.append("i.num LIKE ?")
        params.append(f"%{compact['invoice_no']}%")
    if "min_total" in compact:
        clauses.append("CAST(i.total AS REAL) >= CAST(? AS REAL)")
        params.append(compact["min_total"])
    if "max_total" in compact:
        clauses.append("CAST(i.total AS REAL) <= CAST(? AS REAL)")
        params.append(compact["max_total"])
    if "model_key" in compact:
        clauses.append("i.model_key = ?")
        params.append(compact["model_key"])
    return clauses, params


def search_invoices(connection: sqlite3.Connection, filters: SearchFilters) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    clauses, params = _where_clauses(filters)
    where_sql = " AND ".join(clauses)

    base_query = f"""
        SELECT
            i.batch_id,
            i.page_id,
            i.file_id,
            i.doc_id,
            p.page_no,
            p.image_relpath,
            p.thumb_relpath,
            f.original_relpath,
            i.model_key,
            i.run_id,
            i.record_index,
            i.is_invoice,
            i.num AS invoice_no,
            i.date AS invoice_date,
            i.buyer AS buyer_name,
            i.seller AS seller_name,
            i.item AS service_name,
            i.amt AS amount,
            i.tax,
            i.total,
            i.created_at,
            CASE WHEN i.model_key = 'mock' THEN 0 ELSE 1 END AS model_rank
        FROM invoices i
        JOIN pages p ON p.page_id = i.page_id
        JOIN files f ON f.file_id = i.file_id
        WHERE {where_sql}
    """

    if filters.model_key:
        query = f"""
            {base_query}
            ORDER BY COALESCE(i.date, '') DESC, p.page_no ASC, i.record_index ASC, i.model_key ASC
        """
    else:
        query = f"""
            WITH filtered AS (
                {base_query}
            ),
            ranked AS (
                SELECT
                    filtered.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY page_id, record_index
                        ORDER BY model_rank ASC, created_at DESC, model_key ASC
                    ) AS rn
                FROM filtered
            )
            SELECT
                batch_id,
                page_id,
                file_id,
                doc_id,
                page_no,
                image_relpath,
                thumb_relpath,
                original_relpath,
                model_key,
                run_id,
                record_index,
                is_invoice,
                invoice_no,
                invoice_date,
                buyer_name,
                seller_name,
                service_name,
                amount,
                tax,
                total,
                created_at
            FROM ranked
            WHERE rn = 1
            ORDER BY COALESCE(invoice_date, '') DESC, page_no ASC, record_index ASC, model_key ASC
        """

    rows = connection.execute(query, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("model_rank", None)
        item["thumb_url"] = f"/api/assets/thumb/{item['batch_id']}/{item['page_id']}"
        item["image_url"] = f"/api/assets/image/{item['batch_id']}/{item['page_id']}"
        results.append(item)
    return results
