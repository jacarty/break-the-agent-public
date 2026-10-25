# The arena image — FR-1.
#
# Digest-pinned base, no build step for the front end (ADR 0007), and the
# application runs as a non-root user. `infra/hosting/variables.tf` refuses an
# image reference that is not itself digest-pinned, so a tag cannot creep in
# between two applies of one commit.

FROM python:3.13-slim AS base

# uv, for the same lockfile the tests resolve against. Copied from its own
# published image rather than curl-piped into a shell.
COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so a source change does not re-resolve them. `--frozen`
# fails rather than updating: the lockfile is what makes a deployer's
# resolution identical to the reference run's (SC-6), and an image that quietly
# resolved something newer would break that without failing anything.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY verifier/ ./verifier/
# LICENSE, because `pyproject.toml` declares `license = { file = "LICENSE" }`
# and the second sync installs the project itself, so hatchling reads that
# metadata and raises `OSError: License file does not exist` without it. The
# first sync passes `--no-install-project` and never reads it, which is why the
# layer above is fine and this one was not.
COPY LICENSE ./
RUN uv sync --frozen --no-dev

# Nothing in the image holds a deployment value, a secret or a flag value.
# Every one of those arrives from the platform at runtime (POR-6, SEC-9,
# TD-13), which is what lets the same digest run in anyone's project.
RUN useradd --create-home --uid 10001 arena
USER arena

EXPOSE 8080
CMD ["python", "-m", "bta.site"]
