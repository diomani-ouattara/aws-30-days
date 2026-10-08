"""Day 26: secrets, config, logs, alarms - the plumbing nobody demos but every team relies on.

    py scripts/ops_demo.py secrets                    # Secrets Manager + Parameter Store: create, read back
    py scripts/ops_demo.py logs                       # 30-day retention on every log group; where logs live
    py scripts/ops_demo.py alarm --email you@x.com    # SNS topic + metric filter + alarm on the row-counter Lambda
    py scripts/ops_demo.py break                      # upload a corrupt .parquet -> Lambda errors -> alarm -> email
    py scripts/ops_demo.py audit                      # CloudTrail: who deleted what, and when
    py scripts/ops_demo.py teardown                   # schedule the secret for deletion (alarm stays for Day 28)
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone

import boto3

PROFILE, REGION = "ds", "ca-central-1"
SECRET = "ds-lab/fake-db"
PARAMS = {"/ds-lab/bucket": "dave-ds-lab-ca", "/ds-lab/max-test-mae": "1.30"}
TOPIC = "ds-lab-alerts"
FUNCTION = "raw-row-counter"
LOG_GROUP = f"/aws/lambda/{FUNCTION}"
METRIC_NS, METRIC = "DsLab", "RowCounterErrors"
ALARM = "ds-lab-row-counter-errors"
RETENTION_DAYS = 30


def s():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


# ------------------------------------------------------------------ secrets + config
def secrets():
    sess = s()
    sm, ssm = sess.client("secretsmanager"), sess.client("ssm")

    # A secret: credentials. Encrypted with KMS, access-controlled, rotatable, audited, $0.40/month.
    try:
        password = sm.get_random_password(PasswordLength=24, ExcludePunctuation=True)["RandomPassword"]
        sm.create_secret(Name=SECRET, Description="Fake database login for the Day 26 exercise",
                         SecretString=json.dumps({"username": "analyst", "password": password,
                                                  "host": "db.example.internal", "port": 5432}),
                         Tags=[{"Key": "project", "Value": "ds-lab"}])
        print("created secret", SECRET)
    except sm.exceptions.ResourceExistsException:
        print("secret already exists:", SECRET)

    # Parameters: plain config. Standard tier is free. Not for passwords (use SecureString or a secret).
    for name, value in PARAMS.items():
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
        print("put parameter", name, "=", value)

    print("\n--- what application code does ---")
    print(read_config())


def read_config():
    """The snippet worth keeping: no credentials in code, no credentials in git, nothing in env files."""
    sess = s()
    secret = json.loads(sess.client("secretsmanager").get_secret_value(SecretId=SECRET)["SecretString"])
    ssm = sess.client("ssm")
    bucket = ssm.get_parameter(Name="/ds-lab/bucket")["Parameter"]["Value"]
    max_mae = float(ssm.get_parameter(Name="/ds-lab/max-test-mae")["Parameter"]["Value"])
    masked = secret["password"][:2] + "*" * (len(secret["password"]) - 2)
    return (f"db user={secret['username']} password={masked} host={secret['host']}:{secret['port']}\n"
            f"bucket={bucket}  max_test_mae={max_mae}")


# ------------------------------------------------------------------ logs
def logs():
    cw = s().client("logs")
    print(f"{'log group':62s} {'stored':>9s}  retention")
    for page in cw.get_paginator("describe_log_groups").paginate():
        for g in page["logGroups"]:
            before = g.get("retentionInDays")
            if before != RETENTION_DAYS:
                cw.put_retention_policy(logGroupName=g["logGroupName"], retentionInDays=RETENTION_DAYS)
            print(f"{g['logGroupName']:62s} {g.get('storedBytes', 0) / 1e3:7.0f} KB  "
                  f"{before or 'never expire'} -> {RETENTION_DAYS} days")
    print("\nNew log groups default to 'never expire'. Logs are cheap per GB but they never stop growing.")


# ------------------------------------------------------------------ alarm
def alarm(email):
    sess = s()
    sns, cw_logs, cw = sess.client("sns"), sess.client("logs"), sess.client("cloudwatch")

    topic = sns.create_topic(Name=TOPIC, Tags=[{"Key": "project", "Value": "ds-lab"}])["TopicArn"]
    subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
    if not any(x["Endpoint"] == email for x in subs):
        sns.subscribe(TopicArn=topic, Protocol="email", Endpoint=email)
        print(f"subscribed {email} - CHECK YOUR INBOX and click 'Confirm subscription' (AWS Notifications)")
    else:
        print(f"{email} already subscribed")

    # Metric filter: turn matching LOG LINES into a NUMBER CloudWatch can graph and alarm on.
    cw_logs.put_metric_filter(
        logGroupName=LOG_GROUP, filterName="errors",
        filterPattern='"[ERROR]"',
        metricTransformations=[{"metricName": METRIC, "metricNamespace": METRIC_NS,
                                "metricValue": "1", "defaultValue": 0}],
    )
    print(f"metric filter: lines containing [ERROR] in {LOG_GROUP} -> {METRIC_NS}/{METRIC}")

    cw.put_metric_alarm(
        AlarmName=ALARM,
        AlarmDescription="raw-row-counter logged an error - a bad file probably landed in raw/",
        Namespace=METRIC_NS, MetricName=METRIC, Statistic="Sum",
        Period=60, EvaluationPeriods=1, Threshold=1, ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",         # no log lines = nothing wrong
        AlarmActions=[topic], OKActions=[topic],
        Tags=[{"Key": "project", "Value": "ds-lab"}],
    )
    arn = cw.describe_alarms(AlarmNames=[ALARM])["MetricAlarms"][0]["AlarmArn"]
    print("alarm ARN:", arn)


# ------------------------------------------------------------------ break it on purpose
def break_it():
    sess = s()
    s3, cw = sess.client("s3"), sess.client("cloudwatch")
    bucket = sess.client("ssm").get_parameter(Name="/ds-lab/bucket")["Parameter"]["Value"]
    key = "raw/alarm-test-not-really.parquet"     # raw/ root: outside raw/yellow/, so Athena tables never see it

    s3.put_object(Bucket=bucket, Key=key, Body=b"this is a text file pretending to be parquet\n")
    print(f"uploaded s3://{bucket}/{key} - the Lambda will try to read its footer and fail")
    try:
        t0 = time.time()
        while time.time() - t0 < 600:
            state = cw.describe_alarms(AlarmNames=[ALARM])["MetricAlarms"][0]["StateValue"]
            print(f"{time.time() - t0:5.0f}s  alarm {state}")
            if state == "ALARM":
                print("\nALARM - the email is on its way. S3 also retries the failed Lambda twice, so expect")
                print("the metric to show 3 errors. The alarm returns to OK (and emails again) a few minutes later.")
                break
            time.sleep(30)
        else:
            print("no ALARM within 10 min - check the Lambda's log group for the [ERROR] line")
    finally:
        s3.delete_object(Bucket=bucket, Key=key)
        print("deleted the bad file")


# ------------------------------------------------------------------ audit
def audit():
    ct = s().client("cloudtrail")
    since = datetime.now(timezone.utc) - timedelta(days=30)
    print("CloudTrail: every API call is recorded with who, when, from where (90 days, free)\n")
    for name in ["DeleteNotebookInstance", "DeleteEndpoint", "CreateTrainingJob", "DeleteFunctionUrlConfig"]:
        events = ct.lookup_events(LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": name}],
                                  StartTime=since, MaxResults=5)["Events"]
        for e in events:
            detail = json.loads(e["CloudTrailEvent"])
            who = detail.get("userIdentity", {}).get("arn", "?").split("/")[-1]
            target = json.dumps(detail.get("requestParameters") or {})[:70]
            ip = detail.get("sourceIPAddress", "?")
            if ip.count(".") == 3:                       # mask your home IP - this output may end up in a public repo
                ip = ".".join(ip.split(".")[:2] + ["x", "x"])
            print(f"{e['EventTime']:%Y-%m-%d %H:%M}  {name:24s} by {who:14s} from {ip}  {target}")


# ------------------------------------------------------------------ teardown
def teardown():
    sm = s().client("secretsmanager")
    try:
        r = sm.delete_secret(SecretId=SECRET, RecoveryWindowInDays=7)
        print(f"secret {SECRET} scheduled for deletion on {r['DeletionDate']:%Y-%m-%d} (7-day undo window)")
    except sm.exceptions.ResourceNotFoundException:
        print("no secret to delete")
    print("parameters (free) and the alarm stay; Day 28's teardown removes them")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["secrets", "logs", "alarm", "break", "audit", "teardown"])
    p.add_argument("--email")
    a = p.parse_args()
    if a.action == "alarm":
        if not a.email:
            p.error("alarm needs --email")
        alarm(a.email)
    else:
        {"secrets": secrets, "logs": logs, "break": break_it, "audit": audit, "teardown": teardown}[a.action]()


if __name__ == "__main__":
    main()
