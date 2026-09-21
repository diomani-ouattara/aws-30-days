-- Day 9: the same January data as CSV, for the scan-size comparison.
-- CSV has no schema: types here are what the *file* contains. pandas wrote nullable ints as floats
-- ("1.0"), so passenger_count / ratecodeid must be double or they come back NULL.

CREATE EXTERNAL TABLE IF NOT EXISTS ds_lab.yellow_csv (
  vendorid               bigint,
  tpep_pickup_datetime   timestamp,
  tpep_dropoff_datetime  timestamp,
  passenger_count        double,
  trip_distance          double,
  ratecodeid             double,
  store_and_fwd_flag     string,
  pulocationid           bigint,
  dolocationid           bigint,
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
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://dave-ds-lab-ca/raw/yellow_csv/'
TBLPROPERTIES ('skip.header.line.count' = '1');
