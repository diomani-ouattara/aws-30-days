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
