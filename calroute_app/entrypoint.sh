#!/bin/bash
set -e

echo "⏳ Waiting for MySQL to be ready..."

# Wait for MySQL to accept connections
until mysqladmin ping -h"$DB_HOST" -P"$DB_PORT" --silent; do
  sleep 2
done

echo "✅ MySQL is up!"

echo "📥 Running flask db upgrade..."
export FLASK_APP=app.py
python -m flask db upgrade

echo "🚀 Starting Flask app..."
exec python app.py
