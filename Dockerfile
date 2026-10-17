# ChainWatch serving image (serve env only: no numpy, no LightGBM). Spec 16.1.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0 PIP_NO_CACHE_DIR=1
WORKDIR /app
ARG REQS=requirements-serve.txt
COPY ${REQS} ./requirements.txt
RUN pip install --only-binary=:all: -r requirements.txt
COPY pyproject.toml ./
COPY chainwatch ./chainwatch
COPY configs ./configs
COPY models/default ./models/default
COPY data/replay ./data/replay
RUN useradd --uid 10001 --no-create-home chainwatch
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["python", "-m", "chainwatch", "serve", "--host", "0.0.0.0", "--port", "8000"]
