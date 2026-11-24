FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' entrypoint.sh \
 && DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput \
 && useradd --system --uid 1000 --no-create-home --home-dir /app emr \
 && mkdir -p /app/media \
 && chown emr /app/media

USER emr
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["sh", "/app/entrypoint.sh"]
CMD ["gunicorn", "emr.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--timeout", "120", "--no-control-socket", "--access-logfile", "-"]
