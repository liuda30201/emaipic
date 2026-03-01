import sqlite3

from libs.common.models import SearchFilters
from services.index_export.app.search import search_invoices


def setup_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE files (
            file_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_relpath TEXT NOT NULL,
            staging_relpath TEXT NOT NULL,
            content_type TEXT,
            file_type TEXT,
            sha256 TEXT,
            size INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            page_no INTEGER NOT NULL,
            profile TEXT NOT NULL,
            image_relpath TEXT,
            thumb_relpath TEXT,
            original_relpath TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE invoices (
            page_id TEXT NOT NULL,
            model_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            record_index INTEGER NOT NULL,
            is_invoice INTEGER NOT NULL,
            num TEXT,
            date TEXT,
            buyer TEXT,
            seller TEXT,
            item TEXT,
            amt TEXT,
            tax TEXT,
            total TEXT,
            created_at TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            PRIMARY KEY (page_id, model_key, run_id, record_index)
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "file_1",
                "batch_1",
                "doc_1",
                "a.jpg",
                "raw/batch_1/attachments/a.jpg",
                "staging/batch_1/files/doc_1/file_1.jpg",
                "image/jpeg",
                "jpg",
                "hash_1",
                12,
                "2026-03-01T00:00:00",
            ),
            (
                "file_2",
                "batch_2",
                "doc_2",
                "b.jpg",
                "raw/batch_2/attachments/b.jpg",
                "staging/batch_2/files/doc_2/file_2.jpg",
                "image/jpeg",
                "jpg",
                "hash_2",
                34,
                "2026-03-01T00:01:00",
            ),
        ],
    )
    connection.executemany(
        """
        INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "page_1",
                "batch_1",
                "file_1",
                "doc_1",
                1,
                "prod_default",
                "processed/batch_1/pages/page_1.jpg",
                "processed/batch_1/thumbs/page_1.jpg",
                "raw/batch_1/attachments/file_1.jpg",
                "done",
                "2026-03-01T00:00:00",
            ),
            (
                "page_2",
                "batch_2",
                "file_2",
                "doc_2",
                1,
                "prod_default",
                "processed/batch_2/pages/page_2.jpg",
                "processed/batch_2/thumbs/page_2.jpg",
                "raw/batch_2/attachments/file_2.jpg",
                "done",
                "2026-03-01T00:01:00",
            ),
        ],
    )
    connection.executemany(
        """
        INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "page_1",
                "mock",
                "run_1",
                0,
                1,
                "INV-1001",
                "2026-02-10",
                "采购甲",
                "销方甲",
                "服务A",
                "100.00",
                "6.00",
                "106.00",
                "2026-03-01T00:00:00",
                "batch_1",
                "file_1",
                "doc_1",
            ),
            (
                "page_1",
                "glm-ocr",
                "run_2",
                0,
                1,
                "INV-9001",
                "2026-02-10",
                "采购甲",
                "销方甲",
                "服务A",
                "100.00",
                "6.00",
                "106.00",
                "2026-03-01T00:00:01",
                "batch_1",
                "file_1",
                "doc_1",
            ),
            (
                "page_2",
                "mock",
                "run_3",
                0,
                1,
                "INV-1002",
                "2026-02-20",
                "采购乙",
                "销方乙",
                "服务B",
                "300.00",
                "18.00",
                "318.00",
                "2026-03-01T00:01:00",
                "batch_2",
                "file_2",
                "doc_2",
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


def test_search_invoices_filters_by_model_key() -> None:
    connection = sqlite3.connect(":memory:")
    setup_table(connection)
    results = search_invoices(connection, SearchFilters(model_key="glm-ocr"))
    assert len(results) == 1
    assert results[0]["model_key"] == "glm-ocr"
