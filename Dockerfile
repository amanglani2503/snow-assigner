# ---- Stage 1: build the React app ----
FROM node:20-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npx vite build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    SNOW_BASE_URL=http://127.0.0.1:8080/mock-snow
WORKDIR /app
COPY backend/requirements-deploy.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /web/dist ./static
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}