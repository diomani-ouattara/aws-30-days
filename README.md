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

**One thing that surprised me**

<!-- your words -->

**What I'd tell someone starting Day 1**

<!-- your words -->

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
| `yellow_clean`, timestamp filter          | ___     | ___  |
| `yellow_part`, timestamp filter           | ___     | ___  |
| `yellow_part`, `year = 2024 AND month = 1` | ___    | ___  |

Partition pruning only kicks in when the WHERE clause uses the partition columns. Same table, same question,
filtered the "wrong" way, costs the same as no partitioning at all.
Small-files rule: aim for 128 MB - 1 GB per Parquet file. Thousands of 1 MB files make every query slow
regardless of partitioning (each file is an S3 request).
