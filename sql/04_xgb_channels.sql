-- Day 17: write the training and validation channels for SageMaker's built-in XGBoost.
--
-- The built-in algorithm's CSV contract:
--   * target in the FIRST column
--   * no header row, no index column
--   * every value numeric
-- UNLOAD writes exactly that. Nulls become -1: a tree splits on "-1 vs real values" just fine,
-- and it removes any doubt about how an empty CSV field gets parsed inside the container.
--
-- UNLOAD refuses to write into a prefix that already has files. To rerun, empty it first:
--   aws s3 rm s3://dave-ds-lab-ca/features/xgb/ --recursive --profile ds
-- Run each statement separately (highlight, Run).

UNLOAD (
  SELECT
    tip_amount,                                   -- target, must be first
    COALESCE(passenger_count, -1) AS passenger_count,
    trip_distance,
    trip_minutes,
    fare_amount,
    tolls_amount,
    COALESCE(avg_mph, -1) AS avg_mph,
    COALESCE(fare_per_minute, -1) AS fare_per_minute,
    pickup_hour,
    pickup_dow,
    is_weekend,
    is_rush_hour,
    COALESCE(zone_tip_rate, -1) AS zone_tip_rate,
    COALESCE(zone_trips, -1) AS zone_trips,
    COALESCE(zone_rolling_fare_20, -1) AS zone_rolling_fare_20,
    COALESCE(zone_prev_tip, -1) AS zone_prev_tip,
    COALESCE(zone_seconds_since_prev, -1) AS zone_seconds_since_prev
  FROM ds_lab.trip_features_v1
  WHERE split = 'train'
)
TO 's3://dave-ds-lab-ca/features/xgb/train/'
WITH (format = 'TEXTFILE', field_delimiter = ',', compression = 'NONE');

UNLOAD (
  SELECT
    tip_amount,
    COALESCE(passenger_count, -1) AS passenger_count,
    trip_distance,
    trip_minutes,
    fare_amount,
    tolls_amount,
    COALESCE(avg_mph, -1) AS avg_mph,
    COALESCE(fare_per_minute, -1) AS fare_per_minute,
    pickup_hour,
    pickup_dow,
    is_weekend,
    is_rush_hour,
    COALESCE(zone_tip_rate, -1) AS zone_tip_rate,
    COALESCE(zone_trips, -1) AS zone_trips,
    COALESCE(zone_rolling_fare_20, -1) AS zone_rolling_fare_20,
    COALESCE(zone_prev_tip, -1) AS zone_prev_tip,
    COALESCE(zone_seconds_since_prev, -1) AS zone_seconds_since_prev
  FROM ds_lab.trip_features_v1
  WHERE split = 'valid'
)
TO 's3://dave-ds-lab-ca/features/xgb/validation/'
WITH (format = 'TEXTFILE', field_delimiter = ',', compression = 'NONE');

-- Check:  aws s3 ls s3://dave-ds-lab-ca/features/xgb/ --recursive --human-readable --profile ds
