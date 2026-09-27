# Week 2 data flow

```mermaid
flowchart LR
  subgraph S3["S3 · dave-ds-lab-ca"]
    RAW["raw/yellow/<br/>3 Parquet months · 160 MB"]
    CSV["raw/yellow_csv/<br/>January as CSV · 299 MB"]
    PROC["processed/yellow_clean/<br/>9.2M rows · 209 MB"]
    PART["processed/yellow_part/<br/>year=2024/month=1,2,3"]
    FEAT["features/v1/<br/>split=train · split=valid"]
    OUT["outputs/athena/<br/>query results · expire 30d"]
  end

  GLUE["Glue Data Catalog<br/>database ds_lab"]
  ATHENA["Athena<br/>$5 per TB scanned"]
  LAMBDA["Lambda raw-row-counter<br/>logs rows on upload"]
  CW["CloudWatch Logs"]

  RAW -- "CREATE EXTERNAL TABLE" --> GLUE
  CSV -- "CREATE EXTERNAL TABLE" --> GLUE
  GLUE <--> ATHENA
  ATHENA -- "CTAS clean" --> PROC
  PROC -- "CTAS partitioned" --> PART
  PART -- "CTAS features + split" --> FEAT
  ATHENA --> OUT
  RAW -- "s3:ObjectCreated" --> LAMBDA --> CW
```

## What each hop costs

| Step | Scanned / billed | Note |
|------|------------------|------|
| `COUNT(*)` on Parquet | 0 bytes | row count lives in the footer |
| `COUNT(*)` on CSV | 313.6 MB | every byte read |
| avg tip by payment, unpartitioned | 62.4 MB | baseline |
| same, partitioned but filtered on the timestamp | 70.1 MB | **no pruning** - the trap |
| same, filtered on `year`/`month` | 4.7 MB | 13x cheaper |
| Lambda per upload | 64 KB fetched, ~700 ms | footer only, free tier |

Whole week: under $0.20, most of it the two Glue crawler runs.
