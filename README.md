# AWS in 30 Days

A data-science-focused tour of AWS: S3 -> Glue/Athena -> SageMaker -> deployment.
Region: `ca-central-1`. One account, one bucket, one dataset.

## Week 1 - Account, identity, and storage

### Day 1 - Account and guardrails
- Root user locked down with MFA; day-to-day work as an IAM admin user.
- $10/month budget with email alert. Cost Explorer enabled.

### Day 2 - IAM
- Group `data-scientists` with `AmazonS3ReadOnlyAccess` + `AmazonAthenaFullAccess`.
- Hand-written policy [`iam/raw-read-only.json`](iam/raw-read-only.json): list the bucket root,
  read only under `raw/`.
- Tested as `ds-analyst`: `raw/` readable, `models/` denied, upload denied.
- Lesson: to IAM, "not visible" and "denied" are the same thing - the console just
  doesn't show folders it can't list.

### Day 3 - CLI and boto3
- AWS CLI v2, profile `ds` (region `ca-central-1`). `aws sts get-caller-identity` is the first
  thing to run on any Access Denied.
- [`whoami.py`](whoami.py): boto3 session on the same profile, prints account, ARN, buckets.

### Day 4 - S3 layout
Bucket `dave-ds-lab-ca`, versioning on, public access blocked.

| Prefix       | What goes there                                  |
|--------------|--------------------------------------------------|
| `raw/`       | Source files exactly as received. Never edited.  |
| `processed/` | Cleaned, typed Parquet.                          |
| `features/`  | Model-ready tables (versioned: `features/v1/`).  |
| `models/`    | Trained artifacts + `metrics.json`.              |
| `outputs/`   | Predictions, Athena results. Lifecycle: expire after 30 days. |

Dataset: NYC TLC yellow taxi, Jan-Mar 2024, ~50 MB Parquet per month
([`scripts/get_data.py`](scripts/get_data.py)). Uploaded with `aws s3 sync data/ s3://dave-ds-lab-ca/raw/`.

Storage classes, ca-central-1 (per GB-month, approx):
Standard ~$0.025 | Intelligent-Tiering ~$0.025 + monitoring | Glacier Flexible ~$0.004 | Deep Archive ~$0.002.
Deep Archive is ~10x cheaper than Standard but retrieval takes hours and costs extra.

### Day 5 - S3 from pandas
[`notebooks/01_s3_io.ipynb`](notebooks/01_s3_io.ipynb): read Parquet straight from `s3://`,
write a CSV copy, read it back, convert to Snappy Parquet in `processed/`, presigned URL.

| Format  | Size   | Read time |
|---------|--------|-----------|
| CSV     | 314 MB | 348 s     |
| Parquet |  61 MB |  39 s     |

Same 2,964,624 rows x 19 columns. Parquet is 5.2x smaller and 9x faster to read over the network.
CSV also lost the timestamp types (came back as strings) and threw a mixed-dtype warning; Parquet keeps the schema.

### Day 6 - EC2
- Launched a `t3.micro` (Amazon Linux 2023) in `ca-central-1`, SSH open to my IP only.
- No keys copied to the box: an instance role (`ec2-ds-lab-s3-read`, trust policy in
  [`iam/ec2-s3-read-trust.json`](iam/ec2-s3-read-trust.json)) lets the CLI on the instance read the bucket.
- Row count from a Parquet footer on a 1 GB machine without loading the file: 2,964,624 rows (matches Day 5).
- S3 -> EC2 download ran at ~130 MB/s inside the AWS network, vs a few MB/s from home. Data and compute belong in the same region.
- Write test from the instance: `AccessDenied ... assumed-role/ec2-ds-lab-s3-read` - the role, not my user, is the identity.
- Instance table: [`notes/ec2-instances.md`](notes/ec2-instances.md). Terminated the same day.

### Day 7 - Week 1 review
Month-to-date cost: **$0.00** (Cost Explorer, grouped by service - see [`docs/week1-cost-explorer.png`](docs/week1-cost-explorer.png)).
S3 storage and one EC2 hour both fit inside the free allowance.

**What I built this week**

<!-- your words: account -> IAM -> CLI -> S3 -> pandas -> EC2 -->
This week, i learned how  to create 
- an Account and guardrails, IAM: users, roles, policies, Read the IAM policy JSON structure: Effect / Action / Resource / Condition. Understand identity policies vs resource policies vs roles.
- Create a group data-scientists with AmazonS3ReadOnlyAccess + AmazonAthenaFullAccess; add a second user to it and log in as that user.
- CLI and boto3: Install AWS CLI v2. Create an access key for your IAM user (not root), install boto3; open a session with the profile, list buckets, print my account ID
- S3: buckets, prefixes, storage classes: Create one bucket:-ds-lab-ca. Block public access (default). Turn on versioning.
- Lay it out as raw/, processed/, features/, models/, outputs/. This layout is the pattern you'll see at work.
- Upload your dataset to raw/ with aws s3 cp and aws s3 sync
- EC2: what a cloud machine actually is: Launch a t3.micro (free-tier eligible) with Amazon Linux. Create a key pair, open SSH only to your IP in the security group.
- SSH in, install Python, pull your S3 data with the CLI (attach an IAM role to the instance instead of copying keys — this is the right way).
-  Look up on-demand vs spot pricing for m5.xlarge and g4dn.xlarge.
- Terminate the instance before you close the laptop.
- Review and cost check
**One thing that surprised me**

"not visible = denied" in the S3 console

**What I'd tell someone starting Day 1**

consistency is the key. one step at a time

## Week 2 - SQL over the data lake

### Day 8 - Glue Data Catalog
- Reorganized `raw/` so each table has its own prefix and one file format:
  `raw/yellow/` (3 Parquet months) and `raw/yellow_csv/` (January as CSV). One table = one prefix = one format.
- Database `ds_lab`. Crawler over `processed/` produced table `processed`; it inferred `passenger_count` and
  `ratecodeid` as `double` - correct for that file (the CSV round-trip on Day 5 turned nullable ints into floats),
  but not what the source data means. Crawlers describe files; they don't know intent.
- Hand-written [`ddl/trips.sql`](ddl/trips.sql) -> `ds_lab.yellow_raw` over `raw/yellow/`, types taken from
  the original Parquet schema. Row count: 9,554,778 - and `COUNT(*)` scanned **0 bytes**: Parquet keeps row counts in the file footer, so Athena never read the data.

### Day 9 - Athena
- [`ddl/trips_csv.sql`](ddl/trips_csv.sql): CSV table over `raw/yellow_csv/` for the comparison.
- [`sql/01_explore.sql`](sql/01_explore.sql): five queries with bytes scanned in comments.
- [`sql/01_ctas_clean.sql`](sql/01_ctas_clean.sql): CTAS -> `ds_lab.yellow_clean`, Parquet in `processed/yellow_clean/`.

| Query                | Parquet scanned | CSV scanned |
|----------------------|-----------------|-------------|
| COUNT(*) (January)   | 41.3 MB         | 313.6 MB    |
| avg tip by payment   | 46.4 MB         | 313.6 MB    |
| SELECT * / 2 cols, LIMIT 10 | 30.4 / 10.4 MB | -     |

CTAS `yellow_clean`: 9,210,888 rows kept of 9,554,778 (3.6% dropped by the filters), 160 MB scanned, 11 s, one 209 MB Parquet file.

Athena bills $5/TB scanned. Parquet wins twice: compressed (fewer bytes) and columnar (only the columns you name).

### Day 10 - Partitioning
[`sql/02_partition.sql`](sql/02_partition.sql): CTAS `yellow_part`, partitioned by `year`/`month`, 2 buckets per partition.

| Query: avg tip by payment type, January | Scanned | Time |
|------------------------------------------|---------|------|
| `yellow_clean`, timestamp filter          | 62.4 MB | 1.3 s |
| `yellow_part`, timestamp filter           | 70.1 MB | 1.6 s |
| `yellow_part`, `year = 2024 AND month = 1` | **4.7 MB** | 0.9 s |

Partition pruning only kicks in when the WHERE clause uses the partition columns. Same table, same question,
filtered the "wrong" way, costs the same as no partitioning at all.
Small-files rule: aim for 128 MB - 1 GB per Parquet file. Thousands of 1 MB files make every query slow
regardless of partitioning (each file is an S3 request).

### Day 11 - Feature engineering in SQL
[`sql/03_features.sql`](sql/03_features.sql) -> `ds_lab.trip_features_v1`, Parquet in `features/v1/`,
partitioned by `split`. [`notebooks/02_check_features.py`](notebooks/02_check_features.py) reads it back and checks it.

Three leakage traps this query avoids:
1. **Date-based split, not random.** Train = Jan + Feb, validate = Mar. A random split would let the model
   see trips from the same hour and zone on both sides.
2. **Zone tip-rate computed on training months only.** Target encoding over all data would carry March's
   answers into a March feature.
3. **`total_amount` excluded.** It is fare + tip, so it contains the target. Left in, the model scores
   ~1.0 and is worthless.

Also: credit-card trips only (`payment_type = 1`) - cash tips are never recorded, so their `tip_amount = 0`
is missing data disguised as a real value.

| Split | Rows | Mean tip |
|-------|------|----------|
| train (Jan-Feb) | 4,610,759 | $4.14 |
| valid (Mar)     | 2,570,032 | $4.28 |

7.18M of 9.21M clean trips survive the credit-card + sanity filters. The two means are 3% apart - close enough that the split isn't hiding a regime change.

### Day 12 - Warehouse vs lake (concept day)
[`notes/warehouse-vs-lake.md`](notes/warehouse-vs-lake.md): when I'd pick pandas, Athena, or Redshift;
what Spectrum is for; and what Iceberg fixes about the Hive-style tables I built on Day 10.
No resources created - read, wrote, moved on.

### Day 13 - Lambda
[`lambda/row_counter.py`](lambda/row_counter.py): triggered by `s3:ObjectCreated` under `raw/`,
logs each new Parquet file's size, row count, column count and row-group count to CloudWatch.

- Reads only the **file footer** via `pq.read_metadata`, so a 50 MB upload costs two small range
  requests instead of a 50 MB download. Lambda bills GB-seconds; downloading would be ~10x the cost.
- pyarrow comes from the AWS-managed layer `AWSSDKPandas-Python314`. A bare Lambda has boto3 and the
  standard library and nothing else.
- Execution role: `AWSLambdaBasicExecutionRole` (CloudWatch Logs) + [`iam/lambda-raw-read.json`](iam/lambda-raw-read.json)
  (`s3:GetObject` on `raw/*` only).

Constraints that shape how a DS uses Lambda: 15 min max runtime, 10 GB max memory, 250 MB unzipped
package (layers included), no GPU. Fine for glue, triggers and small scoring; wrong for training.

Log line from the test upload (50 MB file, read with a single 64 KB range request):
```
LANDED  raw/yellow/test-trigger.parquet  50.3 MB  3,007,526 rows  19 cols  3 row groups
```

### Day 14 - Week 2 review
Diagram: [`docs/week2-architecture.md`](docs/week2-architecture.md) - S3 prefixes, Glue catalog, Athena CTAS chain, the Lambda trigger.

Month-to-date cost after two weeks: **$0.00**. Glue crawlers were the only line item worth naming
(~$0.07 each, run twice); everything else rounds to zero. No crawler schedule, so nothing runs on its own.

**What I learned in Week 2**
this week i have learned 
- Glue Data Catalog: The catalog is a Hive-style metastore: databases → tables → columns + where the files live.Create a database ds_lab. Run a Glue crawler over processed/; inspect the schema it inferred and fix any types it got wrong. Then create the same table by hand with a CREATE EXTERNAL TABLE DDL
- Athena: querying S3 with SQL:Set a query result location (outputs/athena/). Run SELECT COUNT(*), a GROUP BY, a date truncation. Run the same aggregate on the CSV table and on the Parquet table. Write down the bytes scanned for each. Learn CREATE TABLE AS SELECT (CTAS) with format = 'PARQUET' — this is how you materialize a cleaned table.
- Partitioning and file layout: Rewrite your table partitioned by year/month (partitioned_by = ARRAY['year','month'] in CTAS). Look at the resulting S3 key structure.Query one month with and without the partition filter. Bytes scanned should drop by roughly the partition fraction. Understand the small-files problem: aim for 128 MB–1 GB Parquet files, not thousands of tiny ones.
- Feature engineering in SQL:Build a features table with CTEs: rolling averages with window functions, lag features, time-of-day and day-of-week, target encoding of a category by group. CTAS it to features/v1/ as Parquet. Include a train/validation split column based on date, not random - leakage matters. Read the result back with pandas from S3 and sanity-check nulls and ranges.
- Warehouse vs lake (concept day): Read about Redshift (provisioned vs Serverless), Redshift Spectrum, and when a team picks a warehouse over Athena: concurrency, BI dashboards, joins across many tables, predictable latency. Understand the lakehouse pitch (Iceberg tables in S3, queryable by Athena and Redshift) — you will hear "Iceberg" in interviews this year.
- Lambda: code without a server: Write a Python Lambda that triggers on s3:ObjectCreated under raw/, reads the new file's size and row count, and logs it to CloudWatch.Upload a file, then find the log line in CloudWatch Logs.Learn the constraints that shape how DS uses Lambda: 15-minute limit, memory up to 10 GB, no pandas by default 

<!-- your words: catalog vs table, bytes scanned as the unit of cost, partition pruning, leakage in SQL -->

## Week 3 - SageMaker

### Day 15 - SageMaker setup and the execution role
- Classic notebook instance `ds-lab-notebook`, `ml.t3.medium` (2 vCPU, 4 GB), chosen over Studio because it is
  one resource with one Stop button.
- Execution role created by SageMaker, plus inline policy [`iam/sagemaker-bucket-access.json`](iam/sagemaker-bucket-access.json):
  read `features/` and `processed/`, read/write `models/` and `outputs/`, nothing on `raw/`.
- [`notebooks/03_sagemaker_features.ipynb`](notebooks/03_sagemaker_features.ipynb): counted rows from footers,
  loaded only `split=valid` (partition filter pushed down), proved the role can't write `raw/`.
- Valid split in memory: ___ rows, ___ GB, ___ s to load.
- Stopped the instance at the end of the session.
