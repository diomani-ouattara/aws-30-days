-- Day 8: hand-written Glue Catalog table over raw/yellow/ (3 monthly Parquet files).
-- Run in Athena query editor, workgroup primary, database ds_lab.
--
-- Types map from the Parquet schema (pyarrow.parquet.read_schema):
--   int32 -> int, int64 -> bigint, double -> double, timestamp[us] -> timestamp, string -> string
-- Athena matches Parquet columns BY NAME, case-insensitively, so VendorID / PULocationID are fine.
-- Types must match the physical file: declaring `int` over a double column fails at read time.

CREATE EXTERNAL TABLE IF NOT EXISTS ds_lab.yellow_raw (
  vendorid               int,
  tpep_pickup_datetime   timestamp,
  tpep_dropoff_datetime  timestamp,
  passenger_count        bigint,
  trip_distance          double,
  ratecodeid             bigint,
  store_and_fwd_flag     string,
  pulocationid           int,
  dolocationid           int,
  payment_type           bigint,
  fare_amount            double,
  extra                  double,
  mta_tax                double,
  tip_amount             double,
  tolls_amount           double,
  improvement_surcharge  double,
  total_amount           double,
  congestion_surcharge   double,
  airport_fee            double
)
STORED AS PARQUET
LOCATION 's3://dave-ds-lab-ca/raw/yellow/'
TBLPROPERTIES ('parquet.compression' = 'SNAPPY');

-- Sanity checks after creating it:
-- SELECT COUNT(*) FROM ds_lab.yellow_raw;                       -- expect ~9.4M (3 months)
-- SELECT date_trunc('month', tpep_pickup_datetime) AS m, COUNT(*) FROM ds_lab.yellow_raw GROUP BY 1 ORDER BY 1;
-- SHOW CREATE TABLE ds_lab.processed;                          -- compare with what the crawler wrote
