FROM python:3.11-slim
WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "600", "app:app"]

# Default upstream — override in k8s env to point at the host's ml-runner.
ENV ML_RUNNER_URL="http://localhost:7070"
ENV WEATHER_LAT="39.7392"
ENV WEATHER_LON="-104.9903"
