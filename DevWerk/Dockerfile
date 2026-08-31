FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8000
ENV LOG_LEVEL=debug

WORKDIR /opt/devwerk

COPY requirements.txt ./requirements.txt
RUN python -m venv /opt/devwerk/venv \
    && /opt/devwerk/venv/bin/python -m pip install --upgrade pip \
    && /opt/devwerk/venv/bin/python -m pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN chmod +x /opt/devwerk/startup.sh /opt/devwerk/shutdown.sh

EXPOSE 8000
VOLUME ["/opt/devwerk/data", "/opt/devwerk/config"]

ENTRYPOINT ["/opt/devwerk/startup.sh", "production"]
