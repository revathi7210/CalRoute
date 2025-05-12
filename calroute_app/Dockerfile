# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory in the container
WORKDIR /app

# Install mysql client for mysqladmin + general tools
RUN apt-get update && apt-get install -y default-mysql-client && rm -rf /var/lib/apt/lists/*

# Install dependencies:
# - Copy and install requirements (prepare a requirements.txt with Flask, Flask-SQLAlchemy, PyMySQL etc.)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project into working directory
COPY . /app/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]

# # Expose the port Flask runs on
# EXPOSE 5000

# # Command to run the app (adjust if needed)
# CMD ["python", "app.py"]