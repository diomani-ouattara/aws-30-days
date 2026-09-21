-- Day 9: CTAS - materialize a cleaned table as Parquet in processed/.
-- This is how you turn "a query" into "a table other people can use".
-- Filters: Jan-Mar 2024 only (source has strays), positive distance and fare, dropoff after pickup.

CREATE TABLE ds_lab.yellow_clean
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  external_location = 's3://dave-ds-lab-ca/processed/yellow_clean/'
) AS
SELECT
  vendorid,
  tpep_pickup_datetime,
  tpep_dropoff_datetime,
  CAST(passenger_count AS integer)           AS passenger_count,
  trip_distance,
  CAST(ratecodeid AS integer)                AS ratecodeid,
  pulocationid,
  dolocationid,
  CAST(payment_type AS integer)              AS payment_type,
  fare_amount, extra, mta_tax, tip_amount, tolls_amount,
  improvement_surcharge, total_amount, congestion_surcharge, airport_fee,
  date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) / 60.0 AS trip_minutes
FROM ds_lab.yellow_raw
WHERE tpep_pickup_datetime >= TIMESTAMP '2024-01-01 00:00:00'
  AND tpep_pickup_datetime <  TIMESTAMP '2024-04-01 00:00:00'
  AND trip_distance > 0
  AND fare_amount > 0
  AND tpep_dropoff_datetime > tpep_pickup_datetime;

-- Then:  SELECT COUNT(*) FROM ds_lab.yellow_clean;   and look at s3://dave-ds-lab-ca/processed/yellow_clean/
-- CTAS writes the files AND registers the table. DROP TABLE removes only the catalog entry; the files stay.
