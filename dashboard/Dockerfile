FROM python:3.10-slim

WORKDIR /app

# Copy application code
COPY . .

# Install dependencies
RUN pip install --no-cache-dir flask pyyaml requests rich click gunicorn

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 8080

# Run gunicorn
CMD ["python3", "-m", "gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
