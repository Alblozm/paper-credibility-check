FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[full]" || pip install --no-cache-dir \
    fastapi "uvicorn[standard]" pydantic pydantic-settings sqlalchemy httpx python-multipart

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
