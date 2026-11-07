FROM python:3.11-slim

# Install system dependencies for OCR, PDF processing, and image handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create default local paths. For deployment, mount persistent storage at /app/data.
RUN mkdir -p /app/data/uploads /app/data/seed

# Expose Streamlit default port; the runtime command respects PORT.
EXPOSE 8501

# Render sets PORT; database and uploads paths can be overridden independently.
CMD ["sh", "-c", "python -c \"from db.seed import seed; seed(force=False)\" && streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8501') + '/_stcore/health', timeout=3).read()"
