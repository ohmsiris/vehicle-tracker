# IceBox on Railway (11 Nov 2026): the same Python app, plus LibreOffice Writer so the server can turn the drivers' Word sheet
# (ใบงานคนขับ, app/route_docx.py) into PDF, and Laksaman -- the free font made to match TH Sarabun New -- so it lays out the same.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-tlwg-laksaman fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
