FROM python:3.11-slim

# Evitar archivos .pyc y activar logs
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# 1. Instalar Poetry en el sistema del contenedor
RUN pip install poetry

# 2. Copiar archivos de dependencias
COPY pyproject.toml poetry.lock* /app/

# 3. Configurar Poetry para que no cree entornos virtuales (Docker ya es uno)
# e instalar las librerías
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# 4. Copiar el resto del código
COPY . /app/

# 5. Comando por defecto (puede ser sobreescrito por docker-compose)
CMD ["poetry", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]