FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY src/dinary/__about__.py src/dinary/__about__.py
COPY src/dinary/__init__.py src/dinary/__init__.py
RUN uv pip install --system .

COPY src/ src/
COPY static/ static/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "dinary.main:app", "--host", "0.0.0.0", "--port", "8000"]
