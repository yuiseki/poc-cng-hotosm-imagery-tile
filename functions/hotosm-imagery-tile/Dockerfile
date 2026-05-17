FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV STAC_API_URL=https://api.imagery.hotosm.org/stac
ENV TILE_FORMAT=PNG
ENV TILE_SIZE=512
ENV STAC_ASSET_KEY=visual
ENV STAC_SEARCH_LIMIT=50

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
