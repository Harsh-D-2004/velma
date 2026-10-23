FROM python:3.12-slim

# System tools: Graphviz for diagrams, bubblewrap + prlimit for the bash sandbox
RUN apt-get update && apt-get install -y --no-install-recommends \
        graphviz bubblewrap util-linux ca-certificates fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mpl \
    VELMA_SCRATCH_DIR=/scratch \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app
# Slow layers first so code edits don't redo them.
# NLI model for check (~250 MB, Apache-2.0); stdlib-only script, cached until it changes
COPY scripts/fetch_nli.py ./scripts/
RUN python scripts/fetch_nli.py /app/models/nli

# Dependencies only, cached until pyproject.toml changes
COPY pyproject.toml ./
# pip cache mount (outside the image) keeps finished downloads when a slow/flaky build fails, so a rerun resumes
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
    && pip install -r /tmp/req.txt
# textstat's syllable dict; else the first rewrite downloads it (~3 min stall)
RUN python -m nltk.downloader -d /usr/local/share/nltk_data cmudict

COPY app ./app
COPY schema.sql ./
RUN pip install --no-cache-dir --no-deps .

RUN useradd --create-home --uid 1000 velma && mkdir /scratch && chown velma /scratch
USER velma

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
