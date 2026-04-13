FROM node:20-slim AS builder

WORKDIR /app
COPY dashboard/frontend/package*.json ./
RUN npm ci
COPY dashboard/frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

# Install dashboard API deps
COPY pyproject.toml README.md ./
COPY voicegateway/ ./voicegateway/
COPY dashboard/api/ ./dashboard/api/
COPY dashboard/__init__.py ./dashboard/
RUN pip install --no-cache-dir -e ".[dashboard]"

# Copy built frontend
COPY --from=builder /app/dist ./dashboard/frontend/dist

EXPOSE 9090

CMD ["python", "-m", "dashboard.api.main", "--host", "0.0.0.0", "--port", "9090"]
