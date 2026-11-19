-- Foreign keys whose referencing column is not the leading column of any index.
SELECT c.conrelid::regclass AS tbl, a.attname AS col, c.confrelid::regclass AS ref
FROM pg_constraint c
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
WHERE c.contype = 'f' AND array_length(c.conkey,1) = 1
  AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])
ORDER BY 1,2;
