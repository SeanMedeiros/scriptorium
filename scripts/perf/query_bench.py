#!/usr/bin/env python3
"""Median latency (5 runs) and plan root of hot queries on a database (performance evidence).
Usage: query_bench.py <libpq-uri>   — DELETE statements run inside a rolled-back transaction."""
import json
import statistics
import sys
import time

import psycopg

QUERIES = {
    "retention_unreferenced_assets": """
        SELECT a.id FROM image_assets a WHERE a.created_at < now() + interval '1 day'
          AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.source_asset_id = a.id)
          AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.image_asset_id = a.id)
          AND NOT EXISTS (SELECT 1 FROM segmentations s WHERE s.input_asset_id = a.id)
          AND NOT EXISTS (SELECT 1 FROM recognitions r WHERE r.input_asset_id = a.id)
          AND NOT EXISTS (SELECT 1 FROM image_assets c WHERE c.parent_asset_id = a.id)""",
    "documents_of_collection_page1": """
        SELECT d.id, d.title, (SELECT count(*) FROM pages p WHERE p.document_id = d.id) FROM documents d
        WHERE d.collection_id = (SELECT id FROM collections ORDER BY created_at LIMIT 1)
        ORDER BY d.created_at DESC LIMIT 100""",
    "document_title_search": """
        SELECT id FROM documents WHERE title ILIKE '%PERF-0999%' LIMIT 100""",
    "dashboard_counts": """
        SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM pages),
               (SELECT count(*) FROM recognitions), (SELECT count(*) FROM line_recognitions)""",
    "audit_recent_page": "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 50",
    "asset_by_page_lookup": """
        SELECT p.id FROM pages p WHERE p.image_asset_id = (SELECT image_asset_id FROM pages ORDER BY created_at DESC LIMIT 1)""",
    "delete_document_cascade": "DELETE FROM documents WHERE id = (SELECT id FROM documents WHERE title = 'PERF-05000')",
}


def main(uri: str) -> None:
    out = {}
    with psycopg.connect(uri) as c:
        for name, q in QUERIES.items():
            times = []
            for _ in range(5):
                t = time.perf_counter()
                c.execute(q).fetchall() if not q.lstrip().startswith("DELETE") else c.execute(q)
                times.append((time.perf_counter() - t) * 1000)
                c.rollback()
            plan = c.execute("EXPLAIN (FORMAT JSON) " + q).fetchone()[0][0]["Plan"]
            c.rollback()
            out[name] = {"median_ms": round(statistics.median(times), 2), "plan_root": plan["Node Type"],
                         "total_cost": plan["Total Cost"]}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
