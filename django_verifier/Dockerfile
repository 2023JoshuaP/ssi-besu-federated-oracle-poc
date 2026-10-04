FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY django_verifier ./django_verifier

EXPOSE 8100
CMD ["sh", "-c", "gunicorn django_verifier.wsgi:application --bind 0.0.0.0:${PORT:-8100} --workers 1 --access-logfile - --error-logfile -"]
