# syntax=docker/dockerfile:1
# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

# ---- build the wheel
FROM python:3.15-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir build==1.2.2 && python -m build --wheel --outdir /dist

# ---- runtime image
FROM python:3.15-slim
LABEL org.opencontainers.image.title="Budget Cockpit" \
      org.opencontainers.image.description="Self-hosted month-by-month budget forecast" \
      org.opencontainers.image.source="https://github.com/vinzgithub/budget-cockpit" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUDGET_COCKPIT_DATA_DIR=/data
RUN groupadd --system --gid 10001 cockpit \
 && useradd --system --uid 10001 --gid cockpit --home-dir /data --shell /usr/sbin/nologin cockpit \
 && mkdir -p /data && chown cockpit:cockpit /data
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl
USER cockpit
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "budget_cockpit.app:create_app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
