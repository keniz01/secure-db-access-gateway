FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN pip install --no-cache-dir uv && uv pip install --system -e .

# Copy application code
COPY . .

# Expose port
EXPOSE 8001

# Run the application
CMD ["python", "main.py"]