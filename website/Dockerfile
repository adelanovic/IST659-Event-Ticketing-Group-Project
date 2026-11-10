FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# 1) wait for SQL Server, create the database/tables and load data if empty, 2) start the web server
CMD ["sh", "-c", "python load_ticketmaster_data.py && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --access-logfile - app:app"]
