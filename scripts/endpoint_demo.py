"""Day 22: serve the candidate model two ways - a real-time endpoint and a serverless endpoint -
call each one, then DELETE everything.

A real-time endpoint is the one SageMaker thing this month that bills every hour it exists,
used or not. So this script deletes in a `finally` block: Enter, Ctrl+C or an error all end
in teardown. If the terminal is closed mid-run, clean up with:
    py scripts/endpoint_demo.py --delete

Three objects make an endpoint (all reused from the Day 20 model record):
    Model            artifact + container + inference code       (Day 20 already created it)
    EndpointConfig   which model, on what hardware (instance type, or serverless memory)
    Endpoint         the live HTTPS service built from a config

Run:
    py scripts/endpoint_demo.py                 # real-time on ml.t2.medium, then delete
    py scripts/endpoint_demo.py --serverless    # serverless, then delete
    py scripts/endpoint_demo.py --delete        # remove any ds-lab endpoints left behind
"""
import argparse
import statistics
import time

import boto3

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
INSTANCE = "ml.t2.medium"
PREFIX = "tips-live-"               # every endpoint + config this script makes starts with this


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def latest_model(sm):
    models = sm.list_models(NameContains="tips-candidate-", SortBy="CreationTime", SortOrder="Descending")["Models"]
    if not models:
        raise SystemExit("no tips-candidate-* model record - run scripts/batch_score.py first (Day 20)")
    return models[0]["ModelName"]


def sample_rows(s3, n=5):
    """A few March trips from the validation channel: (true tip, CSV of the 16 features)."""
    key = s3.list_objects_v2(Bucket=BUCKET, Prefix="features/xgb/validation/")["Contents"][0]["Key"]
    text = s3.get_object(Bucket=BUCKET, Key=key, Range="bytes=0-4000")["Body"].read().decode()
    rows = []
    for line in text.splitlines()[:n]:
        cols = line.split(",")
        rows.append((float(cols[0]), ",".join(cols[1:])))
    return rows


def wait_in_service(sm, name):
    t0, last = time.time(), None
    while True:
        status = sm.describe_endpoint(EndpointName=name)["EndpointStatus"]
        if status != last:
            print(f"{time.time() - t0:6.0f}s  {status}")
            last = status
        if status == "InService":
            return time.time() - t0
        if status == "Failed":
            raise SystemExit("endpoint failed: " + sm.describe_endpoint(EndpointName=name).get("FailureReason", "?"))
        time.sleep(20)


def call(rt, name, body):
    t0 = time.perf_counter()
    r = rt.invoke_endpoint(EndpointName=name, ContentType="text/csv", Accept="text/csv", Body=body)
    return r["Body"].read().decode().split(), (time.perf_counter() - t0) * 1000


def delete_all(sm):
    gone = 0
    for ep in sm.list_endpoints(NameContains=PREFIX)["Endpoints"]:
        sm.delete_endpoint(EndpointName=ep["EndpointName"])
        print("deleted endpoint       ", ep["EndpointName"])
        gone += 1
    for cfg in sm.list_endpoint_configs(NameContains=PREFIX)["EndpointConfigs"]:
        sm.delete_endpoint_config(EndpointConfigName=cfg["EndpointConfigName"])
        print("deleted endpoint config", cfg["EndpointConfigName"])
        gone += 1
    if not gone:
        print("nothing to delete - no", PREFIX + "* endpoints or configs")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--serverless", action="store_true")
    p.add_argument("--delete", action="store_true")
    args = p.parse_args()

    s = session()
    sm, rt, s3 = s.client("sagemaker"), s.client("sagemaker-runtime"), s.client("s3")
    if args.delete:
        delete_all(sm)
        return

    model = latest_model(sm)
    kind = "serverless" if args.serverless else "realtime"
    name = f"{PREFIX}{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    variant = {"VariantName": "AllTraffic", "ModelName": model}
    if args.serverless:
        variant["ServerlessConfig"] = {"MemorySizeInMB": 2048, "MaxConcurrency": 1}
    else:
        variant.update({"InstanceType": INSTANCE, "InitialInstanceCount": 1})

    print("model   :", model)
    print("endpoint:", name, f"({'serverless, 2 GB' if args.serverless else INSTANCE})")
    try:
        sm.create_endpoint_config(EndpointConfigName=name, ProductionVariants=[variant],
                                  Tags=[{"Key": "project", "Value": "ds-lab"}])
        sm.create_endpoint(EndpointName=name, EndpointConfigName=name,
                           Tags=[{"Key": "project", "Value": "ds-lab"}])
        print(f"console : https://{REGION}.console.aws.amazon.com/sagemaker/home?region={REGION}#/endpoints/{name}\n")
        took = wait_in_service(sm, name)
        print(f"InService after {took / 60:.1f} min\n")

        rows = sample_rows(s3)

        # 1. One trip per request - what an app would do.
        print("one trip per request:")
        for actual, features in rows:
            pred, ms = call(rt, name, features)
            print(f"  actual ${actual:6.2f}   predicted ${float(pred[0]):6.2f}   {ms:6.0f} ms")

        # 2. Several trips in one request - same model, one round trip.
        body = "\n".join(f for _, f in rows)
        preds, ms = call(rt, name, body)
        print(f"\n{len(rows)} trips in one request: {', '.join('$' + p for p in preds)}   {ms:.0f} ms")

        # 3. Latency over 20 warm calls.
        lat = sorted(call(rt, name, rows[0][1])[1] for _ in range(20))
        print(f"\n20 warm calls: median {statistics.median(lat):.0f} ms, p95 {lat[18]:.0f} ms, max {lat[-1]:.0f} ms")

        print("\nThe endpoint is live and billing. Look at it in the console now (link above).")
        input("Press Enter to DELETE it ... ")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        print()
        delete_all(sm)


if __name__ == "__main__":
    main()
