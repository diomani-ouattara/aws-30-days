-- Day 9: Athena exploration. Athena bills $5 per TB scanned ($0.005 per GB).
-- After each query, read "Data scanned" at the bottom of the editor and write it in the comment.
-- Both tables hold January 2024 (yellow_raw also has Feb + Mar; the WHERE keeps it to January).

-- Q1. Row count. Parquet answers from the footer; CSV has to read every byte.
SELECT COUNT(*) FROM ds_lab.yellow_raw WHERE tpep_pickup_datetime >= DATE '2024-01-01' AND tpep_pickup_datetime < DATE '2024-02-01';
-- scanned: 41.3 MB (had to read the timestamp column for the WHERE)   time: 0.8 s
SELECT COUNT(*) FROM ds_lab.yellow_csv;
-- scanned: 313.6 MB (every byte of the file)   time: 1.0 s

-- Q2. Same aggregate on both. Parquet reads only the 2 columns it needs (columnar); CSV reads all 19.
SELECT payment_type, COUNT(*) AS trips, ROUND(AVG(tip_amount), 2) AS avg_tip
FROM ds_lab.yellow_raw
WHERE tpep_pickup_datetime >= DATE '2024-01-01' AND tpep_pickup_datetime < DATE '2024-02-01'
GROUP BY payment_type ORDER BY trips DESC;
-- scanned: 46.4 MB (2 columns)   time: 1.2 s
SELECT payment_type, COUNT(*) AS trips, ROUND(AVG(tip_amount), 2) AS avg_tip
FROM ds_lab.yellow_csv
GROUP BY payment_type ORDER BY trips DESC;
-- scanned: 313.6 MB (all 19 columns, no choice)   time: 1.0 s

-- Q3. Date truncation: trips per day in January (find the quiet days).
SELECT date_trunc('day', tpep_pickup_datetime) AS d, COUNT(*) AS trips, ROUND(SUM(total_amount)) AS revenue
FROM ds_lab.yellow_raw
WHERE tpep_pickup_datetime >= DATE '2024-01-01' AND tpep_pickup_datetime < DATE '2024-02-01'
GROUP BY 1 ORDER BY 1;
-- scanned: 47.9 MB   time: 8.3 s

-- Q4. Column pruning. Same LIMIT, different column lists - watch scanned bytes move.
SELECT * FROM ds_lab.yellow_raw LIMIT 10;
-- scanned: 30.4 MB (all 19 columns of one row group)
SELECT tpep_pickup_datetime, tip_amount FROM ds_lab.yellow_raw LIMIT 10;
-- scanned: 10.4 MB (2 columns)

-- Q5. Data quality look before we build anything on it (Day 11).
SELECT
  COUNT(*)                                             AS rows_total,
  SUM(CASE WHEN passenger_count IS NULL THEN 1 END)    AS null_passengers,
  SUM(CASE WHEN trip_distance <= 0 THEN 1 END)         AS zero_distance,
  SUM(CASE WHEN total_amount <= 0 THEN 1 END)          AS nonpositive_total,
  SUM(CASE WHEN tpep_dropoff_datetime < tpep_pickup_datetime THEN 1 END) AS dropoff_before_pickup,
  MIN(tpep_pickup_datetime) AS first_pickup, MAX(tpep_pickup_datetime) AS last_pickup
FROM ds_lab.yellow_raw;
-- scanned: 113.9 MB (6 columns)   time: 1.3 s
