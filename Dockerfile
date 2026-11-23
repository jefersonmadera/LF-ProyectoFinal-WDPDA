# Imagen de la aplicación web WDPDA (Fase 7).
# - gcc: compila en el servidor el validador C generado (sandbox).
# - Usuario sin privilegios; el contenedor se ejecuta con el sistema de
#   archivos de solo lectura y /tmp en memoria (ver docker-compose.yml).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.32.1" \
        pymongo==4.10.1 jsonschema==4.23.0 lxml==5.3.0

COPY compiler/ compiler/
COPY runtime/ runtime/
COPY web/ web/
COPY tests/schemas/ tests/schemas/
COPY tests/schemas_xsd/ tests/schemas_xsd/
COPY bench/ bench/
COPY avance_mvp/example_schema.json avance_mvp/example_schema.json

RUN useradd --create-home --uid 10001 wdpda \
 && mkdir -p /app/web/data && chown wdpda /app/web/data
USER wdpda

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/health'); sys.exit(0)"
CMD ["sh", "-c", "python -m uvicorn web.backend.app:app --host 0.0.0.0 --port ${PORT}"]
