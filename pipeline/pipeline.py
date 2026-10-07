"""Day 25: the whole month's ML path as one SageMaker Pipeline (a DAG AWS runs for you).

    Prepare (processing)  ->  Train (training job)  ->  Evaluate (processing)  ->  MAE <= threshold?
                                                                                     yes: Promote -> models/approved/
                                                                                     no:  Fail step, nothing promoted

Every step runs in the Day 23 image (ds-lab-train) - one image, one set of library versions, start to end.

Uses SageMaker Python SDK v2 in its own venv (AWS now calls v2 "on the path to deprecation"; v3 exists,
but v2 is what most production code and tutorials still use):
    py -m venv .venv
    .venv\\Scripts\\python -m pip install "sagemaker>=2.250,<3"

Run with the venv's Python:
    .venv\\Scripts\\python pipeline\\pipeline.py definition        # write pipeline/definition.json, create nothing
    .venv\\Scripts\\python pipeline\\pipeline.py upsert            # create / update the pipeline in SageMaker
    .venv\\Scripts\\python pipeline\\pipeline.py start             # run it, print each step as it finishes
    .venv\\Scripts\\python pipeline\\pipeline.py start --max-mae 1.20   # a bar it can't clear -> Fail branch
"""
import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("SAGEMAKER_SUPPRESS_V2_WARNING", "1")

import boto3  # noqa: E402
from sagemaker.estimator import Estimator  # noqa: E402
from sagemaker.inputs import TrainingInput  # noqa: E402
from sagemaker.processing import ProcessingInput, ProcessingOutput, ScriptProcessor  # noqa: E402
from sagemaker.workflow.condition_step import ConditionStep  # noqa: E402
from sagemaker.workflow.conditions import ConditionLessThanOrEqualTo  # noqa: E402
from sagemaker.workflow.execution_variables import ExecutionVariables  # noqa: E402
from sagemaker.workflow.fail_step import FailStep  # noqa: E402
from sagemaker.workflow.functions import Join, JsonGet  # noqa: E402
from sagemaker.workflow.parameters import ParameterFloat, ParameterString  # noqa: E402
from sagemaker.workflow.pipeline import Pipeline  # noqa: E402
from sagemaker.workflow.pipeline_context import PipelineSession  # noqa: E402
from sagemaker.workflow.properties import PropertyFile  # noqa: E402
from sagemaker.workflow.steps import ProcessingStep, TrainingStep  # noqa: E402

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
NAME = "ds-lab-tips"
HERE = Path(__file__).resolve().parent
RUN = f"s3://{BUCKET}/outputs/pipeline/"          # per-execution scratch; outputs/ expires after 30 days


def code(name):
    """SDK v2 reads 'C:\...' as a URL with scheme 'c' - hand it a relative path instead."""
    return os.path.relpath(HERE / name).replace("\\", "/")


def execution_role(boto_session):
    for page in boto_session.client("iam").get_paginator("list_roles").paginate(PathPrefix="/service-role/"):
        for r in page["Roles"]:
            if r["RoleName"].startswith("AmazonSageMaker-ExecutionRole-"):
                return r["Arn"]
    raise SystemExit("no AmazonSageMaker-ExecutionRole-* found")


def build():
    boto_session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    account = boto_session.client("sts").get_caller_identity()["Account"]
    image = f"{account}.dkr.ecr.{REGION}.amazonaws.com/ds-lab-train:latest"
    role = execution_role(boto_session)
    # Step code is uploaded under models/ - the execution role can read there. No sagemaker-* default bucket.
    session = PipelineSession(boto_session=boto_session, default_bucket=BUCKET,
                              default_bucket_prefix="models/pipeline-code")

    # ---- parameters: knobs you can change per run without editing the pipeline
    max_mae = ParameterFloat(name="MaxTestMAE", default_value=1.30)
    processing_type = ParameterString(name="ProcessingInstanceType", default_value="ml.t3.large")
    training_type = ParameterString(name="TrainingInstanceType", default_value="ml.m5.large")

    def processor(base, instance_type=processing_type):
        return ScriptProcessor(image_uri=image, command=["python3"], role=role, instance_count=1,
                               instance_type=instance_type, base_job_name=base, sagemaker_session=session,
                               max_runtime_in_seconds=1800)

    exec_path = Join(on="/", values=[RUN.rstrip("/"), ExecutionVariables.PIPELINE_EXECUTION_ID])

    # ---- 1. Prepare
    prep = ProcessingStep(
        name="Prepare",
        step_args=processor("tips-prep").run(
            code=code("prep.py"),
            inputs=[ProcessingInput(source=f"s3://{BUCKET}/features/v1/",
                                    destination="/opt/ml/processing/input/features")],
            outputs=[ProcessingOutput(output_name=ch, source=f"/opt/ml/processing/output/{ch}",
                                      destination=Join(on="/", values=[exec_path, ch]))
                     for ch in ["train", "validation", "test"]],
        ),
    )

    def channel(ch):
        return prep.properties.ProcessingOutputConfig.Outputs[ch].S3Output.S3Uri

    # ---- 2. Train - the Day 19 winner's settings, in the Day 23 image
    estimator = Estimator(
        image_uri=image, role=role, instance_count=1, instance_type=training_type,
        output_path=f"s3://{BUCKET}/models/pipeline/", base_job_name="tips-pipe",
        sagemaker_session=session, max_run=1800,
        hyperparameters={"max-leaf-nodes": 255, "min-samples-leaf": 50, "learning-rate": 0.1, "max-iter": 300},
        metric_definitions=[{"Name": "validation:mae", "Regex": r"validation:mae=([0-9\.]+);"}],
    )
    train = TrainingStep(
        name="Train",
        step_args=estimator.fit(inputs={
            "train": TrainingInput(s3_data=channel("train"), content_type="text/csv"),
            "validation": TrainingInput(s3_data=channel("validation"), content_type="text/csv"),
        }),
    )
    model_uri = train.properties.ModelArtifacts.S3ModelArtifacts

    # ---- 3. Evaluate on the test slice; evaluation.json is what the Condition reads
    report = PropertyFile(name="EvaluationReport", output_name="evaluation", path="evaluation.json")
    evaluate = ProcessingStep(
        name="Evaluate",
        step_args=processor("tips-eval").run(
            code=code("evaluate.py"),
            inputs=[ProcessingInput(source=model_uri, destination="/opt/ml/processing/model"),
                    ProcessingInput(source=channel("test"), destination="/opt/ml/processing/test")],
            outputs=[ProcessingOutput(output_name="evaluation", source="/opt/ml/processing/evaluation",
                                      destination=Join(on="/", values=[exec_path, "evaluation"]))],
        ),
        property_files=[report],
    )
    test_mae = JsonGet(step_name=evaluate.name, property_file=report, json_path="regression.mae")

    # ---- 4a. Promote (if branch)
    promote = ProcessingStep(
        name="Promote",
        step_args=processor("tips-promote", "ml.t3.medium").run(
            code=code("promote.py"),
            arguments=["--execution", ExecutionVariables.PIPELINE_EXECUTION_ID],
            inputs=[ProcessingInput(source=model_uri, destination="/opt/ml/processing/model"),
                    ProcessingInput(source=evaluate.properties.ProcessingOutputConfig.Outputs["evaluation"]
                                    .S3Output.S3Uri, destination="/opt/ml/processing/evaluation")],
            outputs=[ProcessingOutput(output_name="approved", source="/opt/ml/processing/output",
                                      destination=f"s3://{BUCKET}/models/approved/")],
        ),
    )
    # ---- 4b. Fail (else branch) - stops the run, marked Failed, with a readable reason
    fail = FailStep(name="MAETooHigh",
                    error_message=Join(on=" ", values=["Test MAE", test_mae, "is above the bar of", max_mae]))

    decide = ConditionStep(
        name="MAEBelowBar",
        conditions=[ConditionLessThanOrEqualTo(left=test_mae, right=max_mae)],
        if_steps=[promote],
        else_steps=[fail],
    )

    return Pipeline(name=NAME, parameters=[max_mae, processing_type, training_type],
                    steps=[prep, train, evaluate, decide], sagemaker_session=session), role


def watch(sm, arn):
    seen, t0 = {}, time.time()
    while True:
        steps = sm.list_pipeline_execution_steps(PipelineExecutionArn=arn, SortOrder="Ascending")["PipelineExecutionSteps"]
        for s in steps:
            if seen.get(s["StepName"]) != s["StepStatus"]:
                seen[s["StepName"]] = s["StepStatus"]
                extra = f"  {s.get('FailureReason', '')}" if s["StepStatus"] == "Failed" else ""
                print(f"{time.time() - t0:6.0f}s  {s['StepName']:12s} {s['StepStatus']}{extra}")
        status = sm.describe_pipeline_execution(PipelineExecutionArn=arn)["PipelineExecutionStatus"]
        if status in ("Succeeded", "Failed", "Stopped"):
            print(f"\npipeline {status} after {(time.time() - t0) / 60:.1f} min")
            return status
        time.sleep(30)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["definition", "upsert", "start"])
    p.add_argument("--max-mae", type=float)
    args = p.parse_args()

    pipeline, role = build()
    if args.action == "definition":
        out = HERE / "definition.json"
        out.write_text(json.dumps(json.loads(pipeline.definition()), indent=2), encoding="utf-8")
        print("wrote", out)
        return
    if args.action == "upsert":
        arn = pipeline.upsert(role_arn=role, tags=[{"Key": "project", "Value": "ds-lab"}])["PipelineArn"]
        print("pipeline:", arn)
        print(f"console : https://{REGION}.console.aws.amazon.com/sagemaker/home?region={REGION}#/pipelines")
        return

    params = {"MaxTestMAE": args.max_mae} if args.max_mae is not None else {}
    execution = pipeline.start(parameters=params)
    print("execution:", execution.arn)
    print("watch the DAG: SageMaker Studio -> Pipelines -> ds-lab-tips, or wait here\n")
    watch(boto3.Session(profile_name=PROFILE, region_name=REGION).client("sagemaker"), execution.arn)


if __name__ == "__main__":
    main()
