# Athena, Redshift, or just pandas? (Day 12)

## What I'd actually pick

**pandas / DuckDB on my laptop** - data under ~2 GB, one analyst, exploration.
Zero setup, zero cost, instant feedback. My Day 5 notebook is this. Stop here unless something forces you off.

**Athena** - files already in S3, queries are ad-hoc or scheduled but not user-facing.
No cluster to run, no data to load, pay per byte scanned. Latency is 1-10 s even for trivial queries,
so it's wrong behind a dashboard that 50 people refresh. This is where most of my month lives.

**Redshift** - a warehouse you load data INTO, running on provisioned or serverless compute.
Worth it when: many concurrent users (BI dashboards), complex joins across many tables,
sub-second latency expected, or the data is modelled (star schema) rather than dumped.
Costs money while it exists, not just while it's used.

## The distinction that matters

Lake = files in object storage + a catalog describing them. Compute is separate and disposable.
Warehouse = storage and compute owned by one system, data loaded in, indexes/statistics/sort keys maintained.

Athena reads the lake. Redshift owns a warehouse. **Redshift Spectrum** is Redshift reaching out into the
lake - same S3 files, queried from Redshift, joinable against warehouse tables. That's the usual real answer
at work: the modelled dimensions live in the warehouse, the raw event firehose stays in S3.

## Lakehouse / Iceberg (the word to know this year)

The gap: plain Hive tables like my `yellow_part` can't do transactions, can't change schema safely,
can't delete one customer's rows for a GDPR request, and - Day 10 - can't prune partitions unless the
WHERE clause names the partition columns.

Apache Iceberg is a table format on top of the same Parquet files in S3. It keeps a manifest of which
files belong to the table at each snapshot, which buys: ACID inserts/updates/deletes, time travel
(query the table as of yesterday), schema evolution, and *hidden partitioning* - the table remembers that
`month` was derived from the timestamp, so filtering on the timestamp prunes correctly. That last one is
exactly the trap I hit on Day 10.

Athena and Redshift both read Iceberg. Glue can write it. When a posting says "lakehouse" this is usually
what they mean; Delta Lake (Databricks) is the same idea, different vendor.

## Costs, ca-central-1, from the pricing pages

| | Pricing model | Idle cost |
|---|---|---|
| Athena | $5 per TB scanned | $0 |
| Redshift Serverless | ~$0.50 per RPU-hour, 8 RPU minimum, 60 s minimum per query | $0 when paused |
| Redshift provisioned (ra3.xlplus) | ~$1.30/hour per node | bills 24/7 until deleted |

My whole month of Athena so far: under $0.05. One ra3 node left running for a month: ~$950.
