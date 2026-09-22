-- Day 10: partition yellow_clean by year/month so a one-month query reads one month's files.
-- Partition columns must be the LAST columns in the SELECT, in the order listed in partitioned_by.
-- bucket_count controls how many files each partition gets (avoids one giant file, or thousands of tiny ones).

CREATE TABLE ds_lab.yellow_part
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  external_location = 's3://dave-ds-lab-ca/processed/yellow_part/',
  partitioned_by = ARRAY['year', 'month'],
  bucketed_by = ARRAY['pulocationid'],
  bucket_count = 2
) AS
SELECT
  *,
  year(tpep_pickup_datetime)  AS year,
  month(tpep_pickup_datetime) AS month
FROM ds_lab.yellow_clean;
-- scanned: 219.4 MB   time: 5.2 s  (reads all of yellow_clean once, writes 6 files)

-- Look at the S3 layout it produced:  processed/yellow_part/year=2024/month=1/...  (Hive-style keys)
-- Then the same question three ways. Note bytes scanned for each.

-- A. Unpartitioned table, January by timestamp filter (yesterday's pattern)
SELECT payment_type, COUNT(*) AS trips, ROUND(AVG(tip_amount), 2) AS avg_tip
FROM ds_lab.yellow_clean
WHERE tpep_pickup_datetime >= TIMESTAMP '2024-01-01' AND tpep_pickup_datetime < TIMESTAMP '2024-02-01'
GROUP BY 1 ORDER BY 2 DESC;
-- scanned: 62.4 MB   time: 1.3 s   <- baseline

-- B. Partitioned table, SAME timestamp filter - Athena can't map this to partitions, so no gain.
SELECT payment_type, COUNT(*) AS trips, ROUND(AVG(tip_amount), 2) AS avg_tip
FROM ds_lab.yellow_part
WHERE tpep_pickup_datetime >= TIMESTAMP '2024-01-01' AND tpep_pickup_datetime < TIMESTAMP '2024-02-01'
GROUP BY 1 ORDER BY 2 DESC;
-- scanned: 70.1 MB   time: 1.6 s   <- WORSE than A: all 3 months read, plus the timestamp column

-- C. Partitioned table, filter on the PARTITION columns - reads only year=2024/month=1/.
SELECT payment_type, COUNT(*) AS trips, ROUND(AVG(tip_amount), 2) AS avg_tip
FROM ds_lab.yellow_part
WHERE year = 2024 AND month = 1
GROUP BY 1 ORDER BY 2 DESC;
-- scanned: 4.7 MB   time: 0.9 s   <- 13x less than A

-- D. Partition metadata - no data read at all.
SELECT year, month, COUNT(*) AS trips FROM ds_lab.yellow_part GROUP BY 1, 2 ORDER BY 1, 2;
-- scanned: 0 bytes - partition counts come from the catalog

-- The lesson: partitioning only helps when the WHERE clause names the partition columns.
-- Analysts who filter on the timestamp instead of year/month pay full price. Document the partition keys.
