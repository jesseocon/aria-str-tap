# CoStar STR connector image. This image is only STR: the Lighthouse tap is a
# different image. Build from this directory:
#
#   mkdir -p vendor && rm -rf vendor/singer-playwright
#   cp -R ../aria-singer-playwright vendor/singer-playwright
#   docker build --build-arg ARIA_RUNNER_IMAGE=aria-runner:0.1.4 -t aria-connector-str .
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
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MELTANO_PROJECT_ROOT=/app/str \
    MELTANO_SEND_ANONYMOUS_USAGE_STATS=false \
    PATH=/app/str/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && pip install uv==0.5.29 \
    && useradd --create-home --uid 10001 runner

COPY --from=aria-runner /app/pyproject.toml /app/README.md /app/aria-runner/
COPY --from=aria-runner /app/src /app/aria-runner/src
COPY vendor/singer-playwright/pyproject.toml vendor/singer-playwright/README.md /app/aria-singer-playwright/
COPY vendor/singer-playwright/singer_playwright /app/aria-singer-playwright/singer_playwright

WORKDIR /app/str
COPY pyproject.toml meltano.yml README.md uv.lock ./
COPY packages ./packages
COPY transform ./transform

RUN uv sync --no-dev \
    && playwright install --with-deps chromium \
    && meltano install \
    && .meltano/extractors/tap-str/venv/bin/playwright install chromium \
    && meltano --environment=development invoke dbt-bigquery:deps \
    && python -m aria_runner connectors export str --out /app/str/connector-manifest.json \
    && chown -R runner:runner /app/str /ms-playwright /app/aria-singer-playwright

COPY docker/entrypoint.sh /usr/local/bin/str-entrypoint
RUN chmod 755 /usr/local/bin/str-entrypoint

USER runner

# The entrypoint starts a virtual display so Chromium stays headed on Cloud Run.
ENTRYPOINT ["/usr/local/bin/str-entrypoint"]
CMD ["execute"]
