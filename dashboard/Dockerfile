FROM python:3.10-slim

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install dependencies with specific secure versions
RUN pip install --no-cache-dir -r requirements.txt

# Copy config first (changes more often than code)
COPY config.yaml .

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 8080

# Run gunicorn with 1 worker and gthread worker class.
# - Single worker: collection status and OAuth token store are in-memory.
# - gthread with 4 threads: Re-analyze sends GLM requests synchronously;
#   threaded workers prevent blocking all other requests while waiting.
CMD ["python3", "-m", "gunicorn", "-w", "1", "--threads", "4", "-k", "gthread", "-b", "0.0.0.0:8080", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
