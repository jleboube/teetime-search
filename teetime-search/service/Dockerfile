FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build the ZIP centroid database at image build time so the running container
# has no network dependency for geo resolution.
COPY scripts/build_zip_db.py /srv/scripts/build_zip_db.py
RUN python /srv/scripts/build_zip_db.py --out /srv/app/data/zips.sqlite

COPY app /srv/app

# Non-root: this process handles user credentials in memory and has no reason
# to run privileged.
RUN useradd --create-home --uid 10001 teetime && chown -R teetime /srv
USER teetime

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
