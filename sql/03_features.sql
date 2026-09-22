-- Day 11: build the model-ready feature table with SQL, then CTAS it to features/v1/.
-- Target: tip_amount. Split by DATE, not at random - a random split lets March trips train a model
-- that is then "validated" on January trips it has already seen the neighbours of. That is leakage.
--
-- Train:      Jan + Feb 2024
-- Validation: Mar 2024
--
-- Run the SELECT on its own first (with a LIMIT) to eyeball it, then run the whole CREATE TABLE.

CREATE TABLE ds_lab.trip_features_v1
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  external_location = 's3://dave-ds-lab-ca/features/v1/',
  partitioned_by = ARRAY['split']          -- partition column must be last in the SELECT
) AS
WITH base AS (
  -- Credit-card trips only: cash tips are not recorded, so their tip_amount is a fake 0
  -- and would teach the model that cash = no tip.
  SELECT
    tpep_pickup_datetime,
    pulocationid,
    dolocationid,
    passenger_count,
    trip_distance,
    trip_minutes,
    fare_amount,
    tolls_amount,
    total_amount,
    tip_amount,
    year,
    month
  FROM ds_lab.yellow_part
  WHERE payment_type = 1
    AND trip_minutes BETWEEN 1 AND 180       -- drop meter glitches
    AND trip_distance < 100
    AND fare_amount BETWEEN 2.5 AND 500
    AND tip_amount >= 0 AND tip_amount < 200
),
calendar AS (
  -- Time-of-day / day-of-week features. Models can't read a timestamp; give them the parts.
  SELECT
    *,
    hour(tpep_pickup_datetime)                  AS pickup_hour,
    day_of_week(tpep_pickup_datetime)           AS pickup_dow,        -- 1 = Monday
    CASE WHEN day_of_week(tpep_pickup_datetime) >= 6 THEN 1 ELSE 0 END AS is_weekend,
    CASE WHEN hour(tpep_pickup_datetime) BETWEEN 7 AND 9
           OR hour(tpep_pickup_datetime) BETWEEN 16 AND 18 THEN 1 ELSE 0 END AS is_rush_hour,
    fare_amount / NULLIF(trip_minutes, 0)       AS fare_per_minute,
    trip_distance / NULLIF(trip_minutes, 0) * 60 AS avg_mph
  FROM base
),
zone_stats AS (
  -- TARGET ENCODING, done safely: the average tip rate per pickup zone, computed on TRAINING MONTHS ONLY.
  -- Computing it over all data would leak March information into a feature used to predict March.
  SELECT
    pulocationid,
    AVG(tip_amount / NULLIF(fare_amount, 0)) AS zone_tip_rate,
    COUNT(*)                                 AS zone_trips
  FROM calendar
  WHERE month IN (1, 2)
  GROUP BY pulocationid
),
windowed AS (
  -- Window functions: what was happening around this trip. PARTITION BY = "restart the window per zone".
  SELECT
    c.*,
    z.zone_tip_rate,
    z.zone_trips,
    -- rolling average fare over the previous 20 trips from the same pickup zone
    AVG(c.fare_amount) OVER (
      PARTITION BY c.pulocationid ORDER BY c.tpep_pickup_datetime
      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
    ) AS zone_rolling_fare_20,
    -- lag feature: the previous trip's tip in this zone
    LAG(c.tip_amount) OVER (
      PARTITION BY c.pulocationid ORDER BY c.tpep_pickup_datetime
    ) AS zone_prev_tip,
    -- seconds since the previous pickup in this zone: a crude demand signal
    date_diff('second',
      LAG(c.tpep_pickup_datetime) OVER (PARTITION BY c.pulocationid ORDER BY c.tpep_pickup_datetime),
      c.tpep_pickup_datetime
    ) AS zone_seconds_since_prev
  FROM calendar c
  LEFT JOIN zone_stats z ON z.pulocationid = c.pulocationid
)
SELECT
  -- features
  pulocationid, dolocationid, passenger_count, trip_distance, trip_minutes,
  fare_amount, tolls_amount, avg_mph, fare_per_minute,
  pickup_hour, pickup_dow, is_weekend, is_rush_hour,
  zone_tip_rate, zone_trips, zone_rolling_fare_20, zone_prev_tip, zone_seconds_since_prev,
  -- target
  tip_amount,
  -- keep for auditing; never feed total_amount to the model - it CONTAINS the tip (leakage)
  tpep_pickup_datetime,
  CASE WHEN month IN (1, 2) THEN 'train' ELSE 'valid' END AS split
FROM windowed;

-- Checks to run afterwards:
-- SELECT split, COUNT(*) FROM ds_lab.trip_features_v1 GROUP BY 1;
-- SELECT split, ROUND(AVG(tip_amount),3) avg_tip, ROUND(AVG(zone_tip_rate),4) avg_zone_rate,
--        SUM(CASE WHEN zone_tip_rate IS NULL THEN 1 END) AS null_zone_rate
-- FROM ds_lab.trip_features_v1 GROUP BY 1;
