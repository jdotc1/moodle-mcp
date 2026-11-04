FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv build --wheel -o /dist

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 mamcp
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[postgres,pdf]" && rm /tmp/*.whl
USER mamcp
WORKDIR /home/mamcp
ENV MAMCP_ARTIFACT_DIR=/home/mamcp/artifacts MAMCP_LOG_LEVEL=INFO
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=2)"
ENTRYPOINT ["moodle-academic-mcp"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
