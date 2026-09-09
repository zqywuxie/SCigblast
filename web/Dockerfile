FROM ghcr.io/mamba-org/micromamba:2.9.0

USER root
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SCIGBLAST_PIPELINE_ROOT=/opt/scigblast \
    SCIGBLAST_RUNTIME_BIN_DIR=/opt/conda/bin \
    PATH=/opt/conda/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates coreutils findutils tini procps util-linux time pigz gawk sed grep gzip \
    && rm -rf /var/lib/apt/lists/*

COPY web/environment.yml /app/environment.yml
RUN micromamba install --yes --name base --strict-channel-priority --file /app/environment.yml \
    && micromamba list --name base --explicit > /app/conda-explicit.txt \
    && micromamba clean --all --yes
ARG MAMBA_DOCKERFILE_ACTIVATE=1
COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# PANDAseq requires the libltdl.so.7 runtime library.
# Keep this after environment installation to reuse the expensive Conda layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libltdl7 \
    && rm -rf /var/lib/apt/lists/*
COPY web/check_runtime.py /app/check_runtime.py
RUN python /app/check_runtime.py && pip freeze > /app/python-packages.txt

COPY web/app.py web/submission.py web/pipeline_registry.json /app/
COPY web/templates /app/templates
COPY web/static /app/static

# The build context is the repository root, restricted by Dockerfile.dockerignore.
COPY IR_split/pipeline/ /opt/scigblast/IR_split/pipeline/
COPY 10X_split/pipeline/ /opt/scigblast/10X_split/pipeline/
COPY Igblast_base/pipeline/ /opt/scigblast/Igblast_base/pipeline/
COPY PigIgblast/pipeline/ /opt/scigblast/PigIgblast/pipeline/
COPY reference/8bp_barcodes.csv /opt/scigblast/reference/8bp_barcodes.csv
COPY web/check_bundle.py /app/check_bundle.py
RUN python /app/check_bundle.py

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/_entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
