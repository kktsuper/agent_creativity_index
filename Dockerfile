FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV PORT=8080
# web:    uvicorn acr.main:app          (default)
# worker: python -m acr.cli worker
CMD ["sh", "-c", "uvicorn acr.main:app --host 0.0.0.0 --port ${PORT} --workers 2"]
