# Airflow image for the pipeline: Airflow, the ingestion CLI and dbt.
# Build from the repo root:
#   docker build -f infra/airflow/Dockerfile -t jobtracker-airflow .
#
# The ingestion package and dbt each get their own virtualenv. Their pinned
# dependencies differ from the ones Airflow ships with, and keeping them apart
# means neither can break Airflow. Tasks call them as CLI commands, so they
# only need to be on the PATH.

FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM apache/airflow:3.3.2-python3.12

# The uv bundled with Airflow is older than the one that wrote uv.lock
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON=/usr/python/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never

USER root

# dbt venv, installed from the locked requirements
COPY infra/airflow/requirements-dbt.txt /tmp/requirements-dbt.txt
RUN uv venv /opt/venvs/dbt \
    && uv pip install --python /opt/venvs/dbt/bin/python --no-deps \
        -r /tmp/requirements-dbt.txt \
    && rm /tmp/requirements-dbt.txt

# Ingestion venv, installed from uv.lock. Dependencies go first so a code
# change does not reinstall them.
ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/ingestion
WORKDIR /tmp/ingestion
COPY ingestion/pyproject.toml ingestion/uv.lock ingestion/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY ingestion/README.md ./
COPY ingestion/src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && rm -rf /tmp/ingestion

# Put only the two entry points on the PATH, not the whole venvs, so
# `python` still means Airflow's interpreter
RUN ln -s /opt/venvs/dbt/bin/dbt /usr/local/bin/dbt \
    && ln -s /opt/venvs/ingestion/bin/jobtracker /usr/local/bin/jobtracker

# The source registry. The CLI reads it from JOBTRACKER_SOURCES.
COPY config/sources.yml /opt/jobtracker/config/sources.yml
ENV JOBTRACKER_SOURCES=/opt/jobtracker/config/sources.yml \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

WORKDIR /opt/airflow
USER airflow
