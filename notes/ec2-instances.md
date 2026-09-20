# EC2 instance cheat sheet (Day 6)

Family letters: `t` burstable (cheap, idle-friendly) · `m` general purpose · `c` compute-heavy ·
`r` memory-heavy · `g`/`p` GPU (`g` = inference/small training, `p` = big training).
Sizes double each step: large = 2 vCPU, xlarge = 4, 2xlarge = 8 ...

Prices are ca-central-1, on-demand, Linux, USD/hour. Verified 2026-09-19
against https://aws.amazon.com/ec2/pricing/on-demand/ and EC2 console → Spot Requests → Pricing history.

| Type          | vCPU | RAM (GiB) | On-demand $/h | Spot $/h (typ.) | When a data scientist picks it |
|---------------|-----:|----------:|--------------:|----------------:|--------------------------------|
| t3.micro      |    2 |        1  |        ~0.012 |          ~0.004 | Free-tier lab box, tiny scripts, cron jobs. Never for pandas on real data. |
| m5.large      |    2 |        8  |        ~0.107 |          ~0.035 | Default "just give me a machine" for notebooks on < 2 GB data. |
| m5.xlarge     |    4 |       16  |        ~0.214 |          ~0.070 | Same, but data up to ~5 GB in memory or parallel sklearn. |
| c5.xlarge     |    4 |        8  |        ~0.186 |          ~0.065 | CPU-bound work: XGBoost/LightGBM on tabular data, feature pipelines. |
| r5.xlarge     |    4 |       32  |        ~0.276 |          ~0.090 | Memory-bound: big joins, wide DataFrames, in-memory graph work. |
| g4dn.xlarge   |    4 |       16  |        ~0.584 |          ~0.200 | One T4 GPU. Fine-tuning small models, GPU inference, learning PyTorch. |

Rules of thumb
- Spot is 60–70% cheaper and can be reclaimed with 2 min notice. Fine for training that checkpoints; never for a notebook you're typing in.
- Stopped instance = no compute charge, but the EBS volume still bills (~$0.11/GB-month in ca-central-1). Terminated = nothing.
- The SageMaker versions of these (`ml.m5.xlarge`) cost ~20–40% more than the raw EC2 price. That premium buys you the managed lifecycle.
