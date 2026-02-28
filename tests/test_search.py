import sqlite3

from libs.common.models import SearchFilters
from services.index_export.app.search import search_invoices


def setup_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE invoices (
            page_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            page_no INTEGER NOT NULL,
            image_relpath TEXT,
            thumb_relpath TEXT,
            original_relpath TEXT,
            invoice_no TEXT,
            invoice_date TEXT,
            buyer_name TEXT,
            seller_name TEXT,
            service_name TEXT,
            amount TEXT,
            tax TEXT,
            total TEXT,
            extraction_id TEXT,
            indexed_at TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "page_1",
                "batch_1",
                "file_1",
                "doc_1",
                1,
                "processed/batch_1/pages/page_1.jpg",
                "processed/batch_1/thumbs/page_1.jpg",
                "raw/batch_1/attachments/file_1.jpg",
                "INV-1001",
                "2026-02-10",
                "采购甲",
                "销方甲",
                "服务A",
                "100.00",
                "6.00",
                "106.00",
                "ext_1",
                "2026-02-28T00:00:00",
            ),
            (
                "page_2",
                "batch_2",
                "file_2",
                "doc_2",
                1,
                "processed/batch_2/pages/page_2.jpg",
                "processed/batch_2/thumbs/page_2.jpg",
                "raw/batch_2/attachments/file_2.jpg",
                "INV-1002",
                "2026-02-20",
                "采购乙",
                "销方乙",
                "服务B",
                "300.00",
                "18.00",
                "318.00",
                "ext_2",
                "2026-02-28T00:01:00",
            ),
        ],
    )
    connection.commit()


def test_search_invoices_filters_by_invoice_no_and_amount() -> None:
    connection = sqlite3.connect(":memory:")
    setup_table(connection)
    results = search_invoices(connection, SearchFilters(invoice_no="1002", min_total="200"))
    assert len(results) == 1
    assert results[0]["page_id"] == "page_2"


def test_search_invoices_filters_by_date_range() -> None:
    connection = sqlite3.connect(":memory:")
    setup_table(connection)
    results = search_invoices(
        connection,
        SearchFilters(start_date="2026-02-01", end_date="2026-02-15"),
    )
    assert len(results) == 1
    assert results[0]["invoice_no"] == "INV-1001"
