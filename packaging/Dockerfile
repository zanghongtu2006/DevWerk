FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVWERK_HOST=0.0.0.0
ENV DEVWERK_PORT=8000
ENV LOG_LEVEL=debug

WORKDIR /opt/devwerk

RUN python -m pip install --upgrade pip

COPY DevWerk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY DevWerk/ ./
RUN chmod +x /opt/devwerk/start.sh 2>/dev/null || true

EXPOSE 8000
VOLUME ["/opt/devwerk/data", "/opt/devwerk/config"]

ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
