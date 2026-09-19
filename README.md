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
