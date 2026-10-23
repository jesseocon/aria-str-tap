# CoStar STR connector image. This image is only STR: the Lighthouse tap is a
# different image. Build from this directory:
#
#   docker build --build-arg ARIA_RUNNER_IMAGE=aria-runner:0.1.11 -t aria-connector-str .
#
# singer-playwright installs from GitHub at the commit pinned in pyproject.toml.
#
# Cloud Run Job: python -m aria_runner execute --run-id <id>
#
# Username and password arrive at run time from the runner. None are baked in.

ARG ARIA_RUNNER_IMAGE=aria-runner:latest
FROM ${ARIA_RUNNER_IMAGE} AS aria-runner

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_HTTP_RETRIES=8 \
    UV_HTTP_TIMEOUT=120 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MELTANO_PROJECT_ROOT=/app/str \
    MELTANO_SEND_ANONYMOUS_USAGE_STATS=false \
    PATH=/app/str/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && pip install uv==0.13.0 \
    && useradd --create-home --uid 10001 runner \
    && mkdir -p /app/str /ms-playwright \
    && chown runner:runner /app/str /ms-playwright

# Layers run from slowest-changing to fastest so a code change reuses every dependency
# layer (Cloud Build pulls the previous image as cache). Each RUN depends only on the
# files copied above it. Installs run as runner, so no closing chown copies the layers.
WORKDIR /app/str
USER runner

# 1. Third-party Python packages, exactly as locked. Local packages install in step 5.
COPY --chown=runner:runner pyproject.toml uv.lock ./
COPY --chown=runner:runner packages/tap-str/pyproject.toml packages/tap-str/README.md packages/tap-str/
COPY --chown=runner:runner packages/str-connector/pyproject.toml packages/str-connector/
RUN uv sync --frozen --no-dev --no-install-local

# 2. Chromium and its system libraries.
USER root
RUN playwright install-deps chromium && rm -rf /var/lib/apt/lists/*
USER runner
RUN playwright install chromium

# 3. Meltano plugin venvs. tap-str is an editable install, so a placeholder package is
#    enough here; the real source lands in step 5.
COPY --chown=runner:runner meltano.yml ./
RUN mkdir -p packages/tap-str/tap_str && touch packages/tap-str/tap_str/__init__.py \
    && meltano install \
    && .meltano/extractors/tap-str/venv/bin/playwright install chromium

# 4. dbt packages.
COPY --chown=runner:runner transform/dbt_project.yml transform/packages.yml transform/package-lock.yml transform/
COPY --chown=runner:runner transform/profiles transform/profiles
RUN meltano --environment=development invoke dbt-bigquery:deps

# 5. aria-runner and this project's code.
COPY --from=aria-runner /app/pyproject.toml /app/README.md /app/aria-runner/
COPY --from=aria-runner /app/src /app/aria-runner/src
COPY --chown=runner:runner README.md ./
COPY --chown=runner:runner packages ./packages
COPY --chown=runner:runner transform ./transform
RUN uv sync --frozen --no-dev \
    && python -m aria_runner connectors export str --out /app/str/connector-manifest.json

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/str-entrypoint

# The entrypoint starts a virtual display so Chromium stays headed on Cloud Run.
ENTRYPOINT ["/usr/local/bin/str-entrypoint"]
CMD ["execute"]
