FROM node:24-slim AS frontend
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY . ./
COPY --from=frontend /build/web/dist /app/web/dist
RUN useradd --uid 10001 --create-home appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
ENV APP_DATA=/app/data PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8765"]
