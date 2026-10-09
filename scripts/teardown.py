"""Day 28: tag what stays, delete what bills, report what's left.

    py scripts/teardown.py tag              # tag every lab resource project=ds-lab, activate it as a cost tag
    py scripts/teardown.py plan             # DRY RUN: what would be deleted (default level)
    py scripts/teardown.py plan --all       # DRY RUN: the full lab, everything except the bucket's data
    py scripts/teardown.py apply            # delete the default level
    py scripts/teardown.py apply --all      # delete the full lab
    py scripts/teardown.py report           # what's left, and what the month cost

Default level = anything that bills while idle, or is a security leftover:
    SageMaker endpoints / configs / notebooks / running jobs, ECR repositories (image storage),
    the Lambda model API and its role (its image lives in ECR), Function URLs, the Day 6 SSH security group
    and key pair, old S3 object versions, the Day 26 secret.
--all adds the free-but-done pieces: the row-counter Lambda and its S3 trigger, the alarm, SNS topic,
parameters, SageMaker models and pipeline, Glue crawler and catalog, EC2/Glue/Lambda roles, outputs/.

Never touched: the bucket and its current data, IAM users, the budget, the SageMaker execution role, your code.
"""
import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
TAG = {"Key": "project", "Value": "ds-lab"}
ACCOUNT = None


def s():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def attempt(label, fn):
    try:
        fn()
        print(f"  ok    {label}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        quiet = code in ("ResourceNotFoundException", "NoSuchEntity", "NoSuchEntityException", "EntityNotFoundException",
                         "ValidationException", "InvalidGroup.NotFound", "RepositoryNotFoundException")
        print(f"  {'gone ' if quiet else 'FAIL '} {label}  ({code})")


# ------------------------------------------------------------------------------------------- tag
def tag():
    sess = s()
    global ACCOUNT
    ACCOUNT = sess.client("sts").get_caller_identity()["Account"]
    arn = lambda svc, res: f"arn:aws:{svc}:{REGION}:{ACCOUNT}:{res}"  # noqa: E731
    print("tagging lab resources project=ds-lab")

    s3 = sess.client("s3")
    def tag_bucket():
        try:
            tags = s3.get_bucket_tagging(Bucket=BUCKET)["TagSet"]
        except ClientError:
            tags = []
        tags = [t for t in tags if t["Key"] != TAG["Key"]] + [TAG]     # put_bucket_tagging REPLACES the set
        s3.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": tags})
    attempt(f"s3 bucket {BUCKET}", tag_bucket)

    lam = sess.client("lambda")
    for fn in ["raw-row-counter", "ds-lab-tip-api"]:
        attempt(f"lambda {fn}", lambda fn=fn: lam.tag_resource(Resource=arn("lambda", f"function:{fn}"),
                                                                Tags={TAG["Key"]: TAG["Value"]}))
    glue = sess.client("glue")
    attempt("glue crawler ds-lab-processed", lambda: glue.tag_resource(
        ResourceArn=arn("glue", "crawler/ds-lab-processed"), TagsToAdd={TAG["Key"]: TAG["Value"]}))
    attempt("glue database ds_lab", lambda: glue.tag_resource(
        ResourceArn=arn("glue", "database/ds_lab"), TagsToAdd={TAG["Key"]: TAG["Value"]}))

    ec2 = sess.client("ec2")
    sgs = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["ds-lab-ssh"]}])["SecurityGroups"]
    kps = ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": ["ds-lab-key"]}])["KeyPairs"]
    ids = [g["GroupId"] for g in sgs] + [k["KeyPairId"] for k in kps]
    if ids:
        attempt("ec2 security group + key pair", lambda: ec2.create_tags(Resources=ids, Tags=[TAG]))

    ssm = sess.client("ssm")
    for name in ["/ds-lab/bucket", "/ds-lab/max-test-mae"]:
        attempt(f"parameter {name}", lambda name=name: ssm.add_tags_to_resource(
            ResourceType="Parameter", ResourceId=name, Tags=[TAG]))

    logs = sess.client("logs")
    for page in logs.get_paginator("describe_log_groups").paginate():
        for g in page["logGroups"]:
            attempt(f"log group {g['logGroupName']}", lambda g=g: logs.tag_resource(
                resourceArn=g["arn"].rstrip(":*"), tags={TAG["Key"]: TAG["Value"]}))

    iam = sess.client("iam")
    for role in ["ec2-ds-lab-s3-read", "AWSGlueServiceRole-ds-lab", "ds-lab-tip-api-role"] + \
            [r["RoleName"] for r in iam.list_roles()["Roles"]
             if r["RoleName"].startswith(("raw-row-counter-role", "AmazonSageMaker-ExecutionRole"))]:
        attempt(f"iam role {role}", lambda role=role: iam.tag_role(RoleName=role, Tags=[TAG]))

    ce = boto3.Session(profile_name=PROFILE).client("ce", region_name="us-east-1")
    attempt("cost allocation tag 'project' -> Active", lambda: ce.update_cost_allocation_tags_status(
        CostAllocationTagsStatus=[{"TagKey": "project", "Status": "Active"}]))
    print("\nCost Explorer can filter by tag project=ds-lab from the next billing refresh (up to 24 h).")


# ------------------------------------------------------------------------------------------- plan / apply
def collect(full):
    """Return [(description, monthly_cost_hint, callable)] - nothing is called here."""
    sess = s()
    sm, ecr, lam, iam, ec2 = (sess.client(x) for x in ["sagemaker", "ecr", "lambda", "iam", "ec2"])
    s3 = sess.client("s3")
    todo = []

    for e in sm.list_endpoints()["Endpoints"]:
        todo.append((f"SageMaker endpoint {e['EndpointName']}", "BILLS HOURLY",
                     lambda n=e["EndpointName"]: sm.delete_endpoint(EndpointName=n)))
    for c in sm.list_endpoint_configs()["EndpointConfigs"]:
        todo.append((f"endpoint config {c['EndpointConfigName']}", "free",
                     lambda n=c["EndpointConfigName"]: sm.delete_endpoint_config(EndpointConfigName=n)))
    for nb in sm.list_notebook_instances()["NotebookInstances"]:
        todo.append((f"notebook instance {nb['NotebookInstanceName']} ({nb['NotebookInstanceStatus']})",
                     "BILLS HOURLY", lambda n=nb["NotebookInstanceName"]: (
                         sm.stop_notebook_instance(NotebookInstanceName=n)
                         if sm.describe_notebook_instance(NotebookInstanceName=n)["NotebookInstanceStatus"] == "InService"
                         else sm.delete_notebook_instance(NotebookInstanceName=n))))
    for kind, lister, key, stopper in [
        ("training job", sm.list_training_jobs, "TrainingJobSummaries", "stop_training_job"),
        ("processing job", sm.list_processing_jobs, "ProcessingJobSummaries", "stop_processing_job"),
        ("transform job", sm.list_transform_jobs, "TransformJobSummaries", "stop_transform_job"),
    ]:
        for j in lister(StatusEquals="InProgress")[key]:
            name = next(v for k, v in j.items() if k.endswith("JobName"))
            arg = next(k for k in j if k.endswith("JobName"))
            todo.append((f"running {kind} {name}", "BILLS", lambda f=getattr(sm, stopper), a=arg, n=name: f(**{a: n})))

    for r in ecr.describe_repositories()["repositories"]:
        if r["repositoryName"].startswith("ds-lab"):
            size = sum(i.get("imageSizeInBytes", 0) for i in
                       ecr.describe_images(repositoryName=r["repositoryName"])["imageDetails"]) / 1e9
            todo.append((f"ECR repo {r['repositoryName']} ({size:.2f} GB of images)", f"~${size * 0.10:.2f}/mo",
                         lambda n=r["repositoryName"]: ecr.delete_repository(repositoryName=n, force=True)))

    fns = {f["FunctionName"]: f for f in lam.list_functions()["Functions"]}
    for name in fns:
        if lam.list_function_url_configs(FunctionName=name)["FunctionUrlConfigs"]:
            todo.append((f"Function URL on {name}", "PUBLIC", lambda n=name: lam.delete_function_url_config(FunctionName=n)))
    if "ds-lab-tip-api" in fns:
        todo.append(("Lambda ds-lab-tip-api (its image goes with ECR)", "free idle",
                     lambda: lam.delete_function(FunctionName="ds-lab-tip-api")))
        todo.append(("IAM role ds-lab-tip-api-role", "free", lambda: delete_role(iam, "ds-lab-tip-api-role")))

    for g in ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["ds-lab-ssh"]}])["SecurityGroups"]:
        rule = ", ".join(r["CidrIp"] for p in g["IpPermissions"] for r in p.get("IpRanges", []))
        todo.append((f"security group ds-lab-ssh (port 22 from {rule})", "security leftover",
                     lambda gid=g["GroupId"]: ec2.delete_security_group(GroupId=gid)))
    for k in ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": ["ds-lab-key"]}])["KeyPairs"]:
        todo.append(("EC2 key pair ds-lab-key", "security leftover", lambda: ec2.delete_key_pair(KeyName="ds-lab-key")))

    old = [(v["Key"], v["VersionId"], v.get("Size", 0)) for v in versions(s3, noncurrent=True)]
    if old:
        gb = sum(x[2] for x in old) / 1e9
        todo.append((f"{len(old)} old S3 object versions + delete markers ({gb:.2f} GB, current data untouched)",
                     f"~${gb * 0.025:.3f}/mo", lambda: purge_versions(s3, old)))

    sec = sess.client("secretsmanager")
    try:
        d = sec.describe_secret(SecretId="ds-lab/fake-db")
        if "DeletedDate" not in d:
            todo.append(("secret ds-lab/fake-db", "$0.40/mo",
                         lambda: sec.delete_secret(SecretId="ds-lab/fake-db", RecoveryWindowInDays=7)))
    except ClientError:
        pass

    if not full:
        return todo

    # ---------------- --all: free pieces that are simply finished
    if "raw-row-counter" in fns:
        todo.append(("S3 -> raw-row-counter trigger", "free",
                     lambda: s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={})))
        todo.append(("Lambda raw-row-counter", "free idle", lambda: lam.delete_function(FunctionName="raw-row-counter")))
        role = fns["raw-row-counter"]["Role"].split("/")[-1]
        todo.append((f"IAM role {role}", "free", lambda r=role: delete_role(iam, r)))
    cw, sns, logs, ssm, glue = (sess.client(x) for x in ["cloudwatch", "sns", "logs", "ssm", "glue"])
    todo.append(("alarm ds-lab-row-counter-errors + metric filter", "free tier",
                 lambda: (cw.delete_alarms(AlarmNames=["ds-lab-row-counter-errors"]),
                          safe(lambda: logs.delete_metric_filter(logGroupName="/aws/lambda/raw-row-counter",
                                                                 filterName="errors")))))
    for t in sns.list_topics()["Topics"]:
        if t["TopicArn"].endswith(":ds-lab-alerts"):
            todo.append(("SNS topic ds-lab-alerts", "free", lambda a=t["TopicArn"]: sns.delete_topic(TopicArn=a)))
    todo.append(("parameters /ds-lab/*", "free",
                 lambda: ssm.delete_parameters(Names=["/ds-lab/bucket", "/ds-lab/max-test-mae"])))
    for m in sm.list_models(MaxResults=100)["Models"]:
        todo.append((f"SageMaker model {m['ModelName']}", "free",
                     lambda n=m["ModelName"]: sm.delete_model(ModelName=n)))
    for p in sm.list_pipelines()["PipelineSummaries"]:
        todo.append((f"SageMaker pipeline {p['PipelineName']}", "free",
                     lambda n=p["PipelineName"]: sm.delete_pipeline(PipelineName=n)))
    todo.append(("Glue crawler ds-lab-processed", "free idle", lambda: glue.delete_crawler(Name="ds-lab-processed")))
    todo.append(("Glue database ds_lab (Athena tables; S3 files stay)", "free",
                 lambda: glue.delete_database(Name="ds_lab")))
    for role in ["AWSGlueServiceRole-ds-lab", "ec2-ds-lab-s3-read"]:
        todo.append((f"IAM role {role}", "free", lambda r=role: delete_role(iam, r)))
    todo.append(("s3://dave-ds-lab-ca/outputs/ (query results, predictions, pipeline scratch)", "pennies",
                 lambda: empty_prefix(s3, "outputs/")))
    return todo


def safe(fn):
    try:
        fn()
    except ClientError:
        pass


def versions(s3, noncurrent):
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET):
        for v in page.get("Versions", []):
            if not v["IsLatest"] or not noncurrent:
                yield v
        for m in page.get("DeleteMarkers", []):
            yield {**m, "Size": 0}


def purge_versions(s3, items):
    for i in range(0, len(items), 1000):
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k, "VersionId": v} for k, v, _ in items[i:i + 1000]]})


def empty_prefix(s3, prefix):
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
            for o in page.get("Contents", [])]
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})


def delete_role(iam, name):
    for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
        iam.detach_role_policy(RoleName=name, PolicyArn=p["PolicyArn"])
    for p in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=p)
    for ip in iam.list_instance_profiles_for_role(RoleName=name)["InstanceProfiles"]:
        iam.remove_role_from_instance_profile(InstanceProfileName=ip["InstanceProfileName"], RoleName=name)
        iam.delete_instance_profile(InstanceProfileName=ip["InstanceProfileName"])
    iam.delete_role(RoleName=name)


def plan_or_apply(apply, full):
    todo = collect(full)
    level = "--all" if full else "default"
    print(f"{'APPLYING' if apply else 'DRY RUN'} - {level} level, {len(todo)} item(s)\n")
    if not todo:
        print("nothing to do")
        return
    for desc, cost, fn in todo:
        if apply:
            attempt(desc, fn)
        else:
            print(f"  would delete  {desc:78s} {cost}")
    if not apply:
        print(f"\nRun again with 'apply{' --all' if full else ''}' to do it.")


# ------------------------------------------------------------------------------------------- report
def report():
    sess = s()
    tagged = sess.client("resourcegroupstaggingapi").get_resources(
        TagFilters=[{"Key": "project", "Values": ["ds-lab"]}])["ResourceTagMappingList"]
    kinds = {}
    for r in tagged:
        kind = ":".join(r["ResourceARN"].split(":")[2:3]) + " " + r["ResourceARN"].split(":")[5].split("/")[0]
        kinds[kind] = kinds.get(kind, 0) + 1
    print("still tagged project=ds-lab (SageMaker job records are permanent history, not running things):")
    for k, n in sorted(kinds.items()):
        print(f"  {n:4d}  {k}")

    ce = boto3.Session(profile_name=PROFILE).client("ce", region_name="us-east-1")
    from datetime import date
    start, end = date.today().replace(day=1).isoformat(), date.today().isoformat()
    if start == end:
        return
    rows = ce.get_cost_and_usage(TimePeriod={"Start": "2026-09-01", "End": end}, Granularity="MONTHLY",
                                 Metrics=["UnblendedCost"],
                                 GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])["ResultsByTime"]
    print("\nCost Explorer, unblended, by month:")
    for month in rows:
        total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in month["Groups"])
        print(f"  {month['TimePeriod']['Start'][:7]}  ${total:.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["tag", "plan", "apply", "report"])
    p.add_argument("--all", action="store_true")
    a = p.parse_args()
    if a.action == "tag":
        tag()
    elif a.action == "report":
        report()
    else:
        plan_or_apply(apply=a.action == "apply", full=a.all)


if __name__ == "__main__":
    main()
