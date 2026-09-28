# One image, many roles:  docker run guruji-backend <role>
# Build context is the repo root (see /.dockerignore).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

WORKDIR /app

# JPL DE440s for the astro engine (32 MB), in its own layer so code changes don't refetch it.
# Keep the URL and checksum in sync with src/guruji/ephemeris.py.
RUN mkdir -p data/ephemeris \
 && python -c "import urllib.request as u; u.urlretrieve('https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp', 'data/ephemeris/de440s.bsp')" \
 && echo "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2  data/ephemeris/de440s.bsp" | sha256sum -c -

# GeoNames places for onboarding (~40 MB unzipped), also in its own layer.
RUN mkdir -p data/geonames && python -c "import io, urllib.request as u, zipfile; b='https://download.geonames.org/export/dump/'; zipfile.ZipFile(io.BytesIO(u.urlopen(b+'cities500.zip').read())).extract('cities500.txt', 'data/geonames'); [u.urlretrieve(b+f, 'data/geonames/'+f) for f in ('admin1CodesASCII.txt', 'countryInfo.txt')]"

# Dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Local embedding model for rule-card search (~0.2 GB), before the code for layer caching.
# Keep the name in sync with guruji.rag.embed.MODEL_NAME.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='data/models')"
# Workers must not reach Hugging Face at runtime: everything is in the image.
ENV HF_HUB_OFFLINE=1

COPY backend/src ./src
RUN uv sync --locked --no-dev

# SQL migrations, applied on deploy with `docker run guruji-backend migrate`.
COPY supabase/migrations ./migrations
ENV MIGRATIONS_DIR=/app/migrations

# Data and model files are fetched as root at build time; the app user only reads them.
RUN chmod -R a+rX /app/data && useradd --create-home --uid 10001 app
USER app

ENTRYPOINT ["python", "-m", "guruji"]
