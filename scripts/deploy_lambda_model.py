"""Day 24: ship the tip model as a Lambda container image behind a Function URL.

    py scripts/deploy_lambda_model.py build      # fetch model from S3, docker build
    py scripts/deploy_lambda_model.py local      # run the image locally (Lambda emulator) and call it
    py scripts/deploy_lambda_model.py deploy     # push to ECR, create/update the function, open a Function URL
    py scripts/deploy_lambda_model.py test       # call the URL: cold start, warm latency, a bad request
    py scripts/deploy_lambda_model.py url-off    # close the URL (function stays, costs $0 idle)
    py scripts/deploy_lambda_model.py delete     # remove function, role and ECR repo entirely

Docker Desktop must be running for build / local / deploy.
The URL is public (no auth) for the length of the exercise, so the function's concurrency is capped at 2
and `url-off` closes it. Anyone with the URL could otherwise make you pay for their requests.
"""
import base64
import io
import json
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
NAME = "ds-lab-tip-api"              # function, ECR repo and image name
ROLE = NAME + "-role"
ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "lambda_model"
SAMPLE = APP / "sample_request.json"


def sh(*cmd, stdin=None, check=True):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=check, input=stdin, text=stdin is not None)


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


# ---------------------------------------------------------------- build
def latest_byo_artifact(sm):
    """Day 23's own-image job: trained with scikit-learn 1.9.1, the same version this image installs."""
    jobs = sm.list_training_jobs(NameContains="sk-tips-byo", StatusEquals="Completed",
                                 SortBy="CreationTime", SortOrder="Descending")["TrainingJobSummaries"]
    if not jobs:
        raise SystemExit("no completed sk-tips-byo-* job - finish Day 23 first")
    d = sm.describe_training_job(TrainingJobName=jobs[0]["TrainingJobName"])
    return d["TrainingJobName"], d["ModelArtifacts"]["S3ModelArtifacts"]


def build():
    s = session()
    job, uri = latest_byo_artifact(s.client("sagemaker"))
    key = uri.replace(f"s3://{BUCKET}/", "")
    raw = s.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for name in ["model.joblib", "metrics.json"]:
            (APP / name).write_bytes(tar.extractfile(name).read())
    print(f"model from {job} -> lambda_model/model.joblib")
    sh("docker", "build", "--platform", "linux/amd64", "--provenance=false", "-t", f"{NAME}:latest", str(APP))
    sh("docker", "image", "ls", NAME)


# ---------------------------------------------------------------- local
def local():
    """AWS's Lambda base image ships a runtime emulator: same handler, same event shape, on localhost."""
    sh("docker", "rm", "-f", NAME + "-local", check=False)
    sh("docker", "run", "-d", "--name", NAME + "-local", "-p", "9000:8080", f"{NAME}:latest")
    try:
        time.sleep(3)
        event = {"body": SAMPLE.read_text(), "isBase64Encoded": False}
        req = urllib.request.Request("http://localhost:9000/2015-03-31/functions/function/invocations",
                                     data=json.dumps(event).encode(), method="POST")
        out = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print("statusCode", out["statusCode"])
        print(json.dumps(json.loads(out["body"]), indent=2))
    finally:
        sh("docker", "rm", "-f", NAME + "-local", check=False)


# ---------------------------------------------------------------- deploy
def ensure_repo(ecr):
    try:
        ecr.create_repository(repositoryName=NAME, tags=[{"Key": "project", "Value": "ds-lab"}])
        ecr.put_lifecycle_policy(repositoryName=NAME, lifecyclePolicyText=(
            '{"rules":[{"rulePriority":1,"description":"keep last 3","selection":'
            '{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}'))
        print("created ECR repository", NAME)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass


def push(s):
    ecr = s.client("ecr")
    ensure_repo(ecr)
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    registry = auth["proxyEndpoint"].replace("https://", "")
    sh("docker", "login", "--username", user, "--password-stdin", registry, stdin=password)
    remote = f"{registry}/{NAME}:latest"
    sh("docker", "tag", f"{NAME}:latest", remote)
    sh("docker", "push", remote)
    digest = ecr.describe_images(repositoryName=NAME, imageIds=[{"imageTag": "latest"}])["imageDetails"][0]["imageDigest"]
    return f"{registry}/{NAME}@{digest}"     # pin the exact image, not the moving 'latest' tag


def ensure_role(iam):
    trust = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                              Tags=[{"Key": "project", "Value": "ds-lab"}])["Role"]["Arn"]
        # Logs only. The model is inside the image, so the function needs no S3 access at all.
        iam.attach_role_policy(RoleName=ROLE,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        print("created role", ROLE, "- waiting 10 s for IAM to propagate")
        time.sleep(10)
        return arn
    except iam.exceptions.EntityAlreadyExistsException:
        return iam.get_role(RoleName=ROLE)["Role"]["Arn"]


def deploy():
    s = session()
    lam = s.client("lambda")
    image = push(s)
    role = ensure_role(s.client("iam"))

    try:
        lam.create_function(FunctionName=NAME, PackageType="Image", Code={"ImageUri": image}, Role=role,
                            MemorySize=1024, Timeout=15, Architectures=["x86_64"],
                            Tags={"project": "ds-lab"})
        print("created function", NAME)
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=NAME, ImageUri=image)
        print("updated function", NAME)
    lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)

    # Cap the blast radius of a public URL: at most 2 copies running at once.
    lam.put_function_concurrency(FunctionName=NAME, ReservedConcurrentExecutions=2)

    try:
        url = lam.create_function_url_config(FunctionName=NAME, AuthType="NONE")["FunctionUrl"]
    except lam.exceptions.ResourceConflictException:
        url = lam.get_function_url_config(FunctionName=NAME)["FunctionUrl"]
    # A public URL needs two resource-policy statements: one for the URL, one for the invoke behind it.
    for sid, kwargs in [
        ("public-url", {"Action": "lambda:InvokeFunctionUrl", "FunctionUrlAuthType": "NONE"}),
        ("public-url-invoke", {"Action": "lambda:InvokeFunction", "InvokedViaFunctionUrl": True}),
    ]:
        try:
            lam.add_permission(FunctionName=NAME, StatementId=sid, Principal="*", **kwargs)
        except lam.exceptions.ResourceConflictException:
            pass
    print("\nimage :", image)
    print("URL   :", url)
    print("\ncurl it from PowerShell:")
    print(f'curl.exe -s -X POST "{url}" -H "Content-Type: application/json" --data-binary "@lambda_model/sample_request.json"')


# ---------------------------------------------------------------- test
def call(url, body):
    req = urllib.request.Request(url, data=body.encode(), method="POST", headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, out = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, out = e.code, e.read().decode()
    return status, out, (time.perf_counter() - t0) * 1000


def test():
    s = session()
    lam = s.client("lambda")
    url = lam.get_function_url_config(FunctionName=NAME)["FunctionUrl"]

    # Force a fresh container: changing configuration retires the warm ones.
    lam.update_function_configuration(FunctionName=NAME, Environment={"Variables": {"BUST": str(time.time())}})
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)

    status, out, ms = call(url, SAMPLE.read_text())
    print(f"cold call : HTTP {status}  {ms:6.0f} ms   {out}\n")
    warm = sorted(call(url, SAMPLE.read_text())[2] for _ in range(10))
    print(f"10 warm calls: median {statistics.median(warm):.0f} ms, max {warm[-1]:.0f} ms  (round trip from here)")
    status, out, _ = call(url, '{"trips": "not a list"}')
    print(f"bad request : HTTP {status}  {out[:110]}...")

    time.sleep(8)   # let the REPORT lines reach CloudWatch
    logs = s.client("logs")
    events = logs.filter_log_events(logGroupName=f"/aws/lambda/{NAME}", filterPattern="REPORT",
                                    startTime=int((time.time() - 300) * 1000))["events"]
    inits = [e["message"] for e in events if "Init Duration" in e["message"]]
    if inits:
        part = inits[-1].split("Init Duration:")[1].split("ms")[0].strip()
        print(f"\nCloudWatch: last cold start spent {part} ms initializing (imports + loading the model)")
    durations = [float(e["message"].split("Duration:")[1].split("ms")[0]) for e in events
                 if "Init Duration" not in e["message"]]
    if durations:
        print(f"CloudWatch: warm handler time median {statistics.median(durations):.1f} ms")


# ---------------------------------------------------------------- teardown
def url_off():
    lam = session().client("lambda")
    for sid in ["public-url", "public-url-invoke"]:
        try:
            lam.remove_permission(FunctionName=NAME, StatementId=sid)
        except lam.exceptions.ResourceNotFoundException:
            pass
    try:
        lam.delete_function_url_config(FunctionName=NAME)
        print("Function URL deleted - the endpoint is gone; the function stays (costs $0 idle)")
    except lam.exceptions.ResourceNotFoundException:
        print("no Function URL to delete")


def delete():
    s = session()
    lam, iam, ecr = s.client("lambda"), s.client("iam"), s.client("ecr")
    url_off()
    try:
        lam.delete_function(FunctionName=NAME)
        print("deleted function", NAME)
    except lam.exceptions.ResourceNotFoundException:
        pass
    try:
        iam.detach_role_policy(RoleName=ROLE,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        iam.delete_role(RoleName=ROLE)
        print("deleted role", ROLE)
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        ecr.delete_repository(repositoryName=NAME, force=True)
        print("deleted ECR repository", NAME)
    except ecr.exceptions.RepositoryNotFoundException:
        pass


def main():
    steps = {"build": build, "local": local, "deploy": deploy, "test": test, "url-off": url_off, "delete": delete}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        print(__doc__)
        sys.exit(1)
    steps[sys.argv[1]]()


if __name__ == "__main__":
    main()
