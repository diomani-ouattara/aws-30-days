"""Day 3: prove the CLI profile and boto3 can both see AWS.

Run:  py whoami.py
Uses the 'ds' profile created with `aws configure --profile ds`.
"""
import boto3

PROFILE = "ds"

session = boto3.Session(profile_name=PROFILE)
sts = session.client("sts")
identity = sts.get_caller_identity()

print(f"Profile : {PROFILE}")
print(f"Region  : {session.region_name}")
print(f"Account : {identity['Account']}")
print(f"User ARN: {identity['Arn']}")

print("\nBuckets:")
s3 = session.client("s3")
for b in s3.list_buckets()["Buckets"]:
    print(f"  - {b['Name']}")
