# ----------------------
# BUILD stage
# ----------------------
FROM python:3.13-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# gunicorn workers share metrics through this directory (emptied at each start)
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

COPY shared-lib/ /build/shared-lib/
COPY order-service/requirements.txt /build/
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install /build/shared-lib/

# ----------------------
# DEV stage
# ----------------------
FROM build AS dev

USER root
WORKDIR /app

EXPOSE 8002

CMD ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]

# ----------------------
# PROD stage
# ----------------------
FROM python:3.13-slim AS prod
# gunicorn reads its worker count from here; compose can override it per service
ENV WEB_CONCURRENCY=2

ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin -c "App user" app

WORKDIR /app

COPY --from=build /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=build /usr/local/bin/ /usr/local/bin/

COPY --chown=app:app order-service/ /app/
RUN install -d -o app -g app /app/staticfiles

USER app
EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/api/orders/health/')" || exit 1

CMD ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && python manage.py migrate && python manage.py collectstatic --noinput && exec gunicorn order_service.wsgi:application --bind 0.0.0.0:8002 --log-level info --access-logfile - --error-logfile -"]
