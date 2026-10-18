FROM python:3.12-slim

# System tools: Graphviz for diagrams, bubblewrap + prlimit for the bash sandbox
RUN apt-get update && apt-get install -y --no-install-recommends \
        graphviz bubblewrap util-linux ca-certificates fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mpl \
    VELMA_SCRATCH_DIR=/scratch

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY schema.sql ./
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 velma && mkdir /scratch && chown velma /scratch
USER velma

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
