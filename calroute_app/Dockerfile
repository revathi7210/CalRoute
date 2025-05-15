FROM python:3.11-slim

WORKDIR /app

# Install MySQL client
RUN apt-get update && \
    apt-get install -y default-mysql-client && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt /app/

# ✅ SAFEST install order for Python slim images
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Copy rest of the code
COPY . /app/

# ✅ Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]

# ✅ Set default command
 CMD ["python", "app.py"]
