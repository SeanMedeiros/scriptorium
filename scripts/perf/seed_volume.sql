-- SYNTHETIC VOLUME DATA FOR PERFORMANCE TESTING ONLY (never on a research database).
-- Adds 50 000 image-asset rows, 10 000 documents with 40 000 pages and 200 000 audit records to a
-- COPY of an installation, so that query plans and latencies can be measured at archive scale.
-- The rows reference no stored files; titles are prefixed "PERF-".
BEGIN;
CREATE TEMP TABLE perf_assets AS
SELECT gen_random_uuid() AS id, n FROM generate_series(1, 50000) n;
INSERT INTO image_assets (id, kind, storage_key, sha256, size_bytes, mime_type, width, height)
SELECT id, 'page_image', 'perf/' || substr(md5(n::text), 1, 2) || '/' || encode(sha256(n::text::bytea), 'hex'),
       encode(sha256(n::text::bytea), 'hex'), 2500000, 'image/jpeg', 2400, 3200 FROM perf_assets;
CREATE TEMP TABLE perf_docs AS
SELECT gen_random_uuid() AS id, n FROM generate_series(1, 10000) n;
INSERT INTO documents (id, collection_id, title, ingest_status, source_asset_id, languages, created_at, updated_at)
SELECT d.id, (SELECT id FROM collections ORDER BY created_at LIMIT 1), 'PERF-' || lpad(d.n::text, 5, '0'), 'ready',
       a.id, '["por"]', now() - (d.n || ' minutes')::interval, now()
FROM perf_docs d JOIN perf_assets a ON a.n = d.n;
INSERT INTO pages (id, document_id, sequence, image_asset_id)
SELECT gen_random_uuid(), d.id, s, a.id
FROM perf_docs d CROSS JOIN generate_series(1, 4) s
JOIN perf_assets a ON a.n = 10000 + (d.n - 1) * 4 + s;
INSERT INTO audit_logs (action, actor_id, entity_type, details, created_at)
SELECT 'perf.synthetic', NULL, 'document', jsonb_build_object('n', n), now() - (n || ' seconds')::interval
FROM generate_series(1, 200000) n;
COMMIT;
ANALYZE image_assets, documents, pages, audit_logs;
