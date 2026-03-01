from __future__ import annotations

import json
import sqlite3
from typing import Any

from libs.common.models import SearchFilters

ALL_MODELS_KEY = "__all__"
INVOICE_ONLY_FILTER_KEYS = ("start_date", "end_date", "buyer", "seller", "invoice_no", "min_total", "max_total")


def _invoice_where_clauses(filters: SearchFilters) -> tuple[list[str], list[Any]]:
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
    if "model_key" in compact and compact["model_key"] != ALL_MODELS_KEY:
        clauses.append("i.model_key = ?")
        params.append(compact["model_key"])
    return clauses, params


def _failed_where_clauses(filters: SearchFilters) -> tuple[list[str], list[Any]]:
    clauses = ["mr.status != 'done'"]
    params: list[Any] = []
    compact = filters.compact()

    if any(compact.get(key) for key in INVOICE_ONLY_FILTER_KEYS):
        clauses.append("1 = 0")
        return clauses, params

    if "batch_id" in compact:
        clauses.append("mr.batch_id = ?")
        params.append(compact["batch_id"])
    if "model_key" in compact and compact["model_key"] != ALL_MODELS_KEY:
        clauses.append("mr.model_key = ?")
        params.append(compact["model_key"])
    return clauses, params


def _success_records(connection: sqlite3.Connection, filters: SearchFilters) -> list[dict[str, Any]]:
    clauses, params = _invoice_where_clauses(filters)
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
            i.service_summary AS service_summary,
            i.service_summary AS service_name,
            i.prompt_version,
            i.normalized_payload,
            i.amt AS amount,
            i.tax,
            i.total,
            i.created_at,
            'done' AS result_status,
            NULL AS error_reason,
            NULL AS error_message,
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
                service_summary,
                service_name,
                prompt_version,
                normalized_payload,
                amount,
                tax,
                total,
                created_at,
                result_status,
                error_reason,
                error_message
            FROM ranked
            WHERE rn = 1
            ORDER BY COALESCE(invoice_date, '') DESC, page_no ASC, record_index ASC, model_key ASC
        """

    rows = connection.execute(query, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("model_rank", None)
        results.append(item)
    return results


def _failed_records(connection: sqlite3.Connection, filters: SearchFilters) -> list[dict[str, Any]]:
    clauses, params = _failed_where_clauses(filters)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            mr.batch_id,
            mr.page_id,
            mr.file_id,
            mr.doc_id,
            p.page_no,
            p.image_relpath,
            p.thumb_relpath,
            f.original_relpath,
            mr.model_key,
            mr.run_id,
            NULL AS record_index,
            0 AS is_invoice,
            NULL AS invoice_no,
            NULL AS invoice_date,
            NULL AS buyer_name,
            NULL AS seller_name,
            NULL AS service_summary,
            NULL AS service_name,
            mr.prompt_version,
            NULL AS normalized_payload,
            NULL AS amount,
            NULL AS tax,
            NULL AS total,
            mr.created_at,
            mr.status AS result_status,
            mr.error_reason,
            mr.error_message
        FROM model_runs mr
        JOIN pages p ON p.page_id = mr.page_id
        JOIN files f ON f.file_id = mr.file_id
        WHERE {where_sql}
        ORDER BY mr.created_at DESC, p.page_no ASC, mr.model_key ASC
    """
    rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def search_invoices(connection: sqlite3.Connection, filters: SearchFilters) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    results = _success_records(connection, filters)
    if filters.include_failed:
        results.extend(_failed_records(connection, filters))

    for item in results:
        payload = item.get("normalized_payload")
        if payload:
            try:
                item["normalized_payload"] = json.loads(payload)
            except Exception:
                item["normalized_payload"] = None
        item["thumb_url"] = f"/api/assets/thumb/{item['batch_id']}/{item['page_id']}"
        item["image_url"] = f"/api/assets/image/{item['batch_id']}/{item['page_id']}"
    return results
