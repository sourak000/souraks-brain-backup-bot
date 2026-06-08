FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg for voice message processing
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Run with unbuffered output for Railway logs
CMD ["python", "-u", "bot.py"]
