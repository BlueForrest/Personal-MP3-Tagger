# jstal-tagger V2.1
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py tags.py lookups.py ./
COPY templates templates
COPY static static
ENV MUSIC_ROOT=/music BACKUP_ROOT=/backups PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 5080
# one worker: the prepared plans and the running save job are kept in memory
CMD ["gunicorn", "--bind", "0.0.0.0:5080", "--workers", "1", "--threads", "4", "--timeout", "600", "app:app"]
