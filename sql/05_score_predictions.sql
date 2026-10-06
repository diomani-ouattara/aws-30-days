-- Day 20: score the Batch Transform output in SQL.
-- Each output line is "actual,predicted" (DataProcessing kept column 0 and the model's output).
-- Replace JOB below with your transform job's name, e.g. tips-batch-20261006-101500.

CREATE EXTERNAL TABLE IF NOT EXISTS ds_lab.predictions_v1 (
  actual     double,
  predicted  double
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://dave-ds-lab-ca/outputs/predictions/JOB/';

-- 1. The headline metrics, computed in SQL. Compare with the training job's validation:mae.
WITH p AS (SELECT actual, predicted, AVG(actual) OVER () AS mean_actual FROM ds_lab.predictions_v1)
SELECT
  COUNT(*)                                                                  AS rows_scored,
  ROUND(AVG(ABS(actual - predicted)), 4)                                    AS mae,
  ROUND(SQRT(AVG(POWER(actual - predicted, 2))), 4)                         AS rmse,
  ROUND(1 - SUM(POWER(actual - predicted, 2)) / SUM(POWER(actual - mean_actual, 2)), 4) AS r2,
  ROUND(AVG(predicted), 3)                                                  AS mean_predicted,
  ROUND(AVG(actual), 3)                                                     AS mean_actual
FROM p;

-- 2. Where is the model wrong? Error by size of the real tip.
SELECT
  CASE
    WHEN actual = 0   THEN 'a. $0 (no tip)'
    WHEN actual < 2   THEN 'b. under $2'
    WHEN actual < 5   THEN 'c. $2-5'
    WHEN actual < 10  THEN 'd. $5-10'
    ELSE                   'e. $10+'
  END                                         AS tip_band,
  COUNT(*)                                    AS trips,
  ROUND(AVG(actual), 2)                       AS avg_actual,
  ROUND(AVG(predicted), 2)                    AS avg_predicted,
  ROUND(AVG(ABS(actual - predicted)), 2)      AS mae
FROM ds_lab.predictions_v1
GROUP BY 1
ORDER BY 1;
