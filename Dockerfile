# Day 23: training image for src/train.py - "bring your own container" for SageMaker.
#
# SageMaker starts a training container with:   docker run <image> train
# so the image needs an executable called `train` on PATH. Everything else is the /opt/ml contract
# that train.py already understands (channels in /opt/ml/input/data, model to /opt/ml/model).

FROM python:3.12-slim

# No .pyc files, unbuffered logs (so CloudWatch sees lines as they're printed).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependencies first, code second: editing train.py doesn't re-download scikit-learn on every build.
COPY requirements-train.txt /tmp/requirements-train.txt
RUN pip install --no-cache-dir -r /tmp/requirements-train.txt && rm /tmp/requirements-train.txt

COPY src/train.py /opt/program/train.py
RUN printf '#!/bin/sh\nexec python /opt/program/train.py "$@"\n' > /usr/local/bin/train \
 && chmod +x /usr/local/bin/train

# Runs as root - on purpose. SageMaker mounts its own root-owned /opt/ml/model over whatever the image
# created, so a non-root user can train but can't save the model (the first SageMaker run failed exactly
# that way: PermissionError on /opt/ml/model/model.joblib). AWS's own training images run as root too.
# Non-root matters for containers that serve network traffic (Day 24); a training job runs once,
# isolated, and exits.
WORKDIR /opt/program

CMD ["train"]
