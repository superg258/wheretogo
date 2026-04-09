FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=7860

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run as uid 1000 by convention.
RUN useradd -m -u 1000 user
WORKDIR /app

# Install Python deps. Playwright is intentionally omitted: the analyzer has
# a graceful fallback to requests+BeautifulSoup so we keep the image small
# and boot fast.
COPY --chown=user requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --chown=user . /app
RUN mkdir -p /app/.cache && chown -R user:user /app

USER user
EXPOSE 7860

ENTRYPOINT ["/usr/bin/tini", "--"]
# 1 worker × 4 threads is plenty — this is IO-bound scraping, not CPU-bound.
# Long timeout because the qingflow + robomaster fetch chain can be slow
# when warming its cache.
CMD ["gunicorn", "--bind", "0.0.0.0:7860", \
     "--workers", "1", "--threads", "4", \
     "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "wsgi:app"]
