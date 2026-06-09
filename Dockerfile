# syntax=docker/dockerfile:1.6

# Stage 1: build the Vue 3 PWA into _static/
FROM node:22-slim AS webapp-build
WORKDIR /webapp
COPY webapp/package.json webapp/package-lock.json ./
# npm ci has a long-standing bug with optional dependencies that makes it
# skip the platform-specific Rollup native binary (npm/cli#4828); npm
# install resolves and installs it correctly for the build platform.
RUN npm install --no-audit --no-fund
COPY webapp/ ./
RUN npm run build && test -f /_static/index.html

# Stage 2: Python runtime
FROM python:3.13-slim
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
# hatchling's metadata 2.4 builder reads ``[project.license].file`` and
# ``readme`` at wheel-prep time and aborts if those files are missing
# from the build context — so they must be COPY'd before ``uv pip
# install``, even though the resulting image never references them at
# runtime.
COPY LICENSE README.md ./
COPY src/dinary/__about__.py src/dinary/__about__.py
COPY src/dinary/__init__.py src/dinary/__init__.py
RUN uv pip install --system .

COPY src/ src/
# Vue PWA build output served by FastAPI at /.
COPY --from=webapp-build /_static/ _static/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "dinary.main:app", "--host", "0.0.0.0", "--port", "8000"]
