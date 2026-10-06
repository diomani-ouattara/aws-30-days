# Day 24: the tip model as a Lambda container image.
#
# AWS's Lambda base image already contains the Lambda runtime client, so the only job here is
# "dependencies + code + model". Lambda runs it as a non-root user automatically.
# Build with --provenance=false: Lambda rejects images pushed with Docker's build attestations.

FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# The model is baked into the image: no S3 call at cold start, and the image IS the version.
COPY app.py model.joblib metrics.json ${LAMBDA_TASK_ROOT}/

CMD ["app.handler"]
