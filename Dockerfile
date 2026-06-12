FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SSE_ONLY=1 TURB_MODE=auto LIVE_NEWS_MODE=live PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh","-c","uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
