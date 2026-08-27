# Dockerfile condiviso per tutte e 4 le fasi della pipeline.
# Un'unica immagine (stesse dipendenze per tutte le fasi, che
# condividono già gli stessi file Python), con comando diverso per
# fase specificato in docker-compose.yml — non 4 immagini separate,
# per non duplicare inutilmente le dipendenze pesanti (torch, easyocr)
# su ciascuna.

FROM python:3.11-slim

# Ora italiana nei log Python (asctime usa TZ del container).
ENV TZ=Europe/Rome

# Dipendenze di sistema:
# - tzdata: fuso Europe/Rome (slim è UTC senza questo pacchetto)
# - tesseract-ocr + pacchetto lingua italiana: usato da front_detector.py
# - libzbar0: richiesto da pyzbar (lettura barcode)
# - libgl1 + libglib2.0-0: richiesti da opencv-python-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    tesseract-ocr \
    tesseract-ocr-ita \
    libzbar0 \
    libgl1 \
    libglib2.0-0 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutti i file della pipeline già pronti e testati (invariati)
COPY ocr_cannabis.py date_corrector.py merge_regione.py \
     phase5_difformita.py phase4_excel.py phase1_preprocess.py \
     front_detector.py etichetta_presence.py pipeline.py \
     farmacie_dizionario.py \
     crop_vuoto_v2.png OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx \
     ./

# Script dispatcher: riceve il nome della fase come primo argomento
# da riga di comando, richiama la funzione giusta di pipeline.py
COPY docker/run_fase.py .

ENTRYPOINT ["python", "run_fase.py"]
