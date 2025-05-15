#!/bin/bash

set -e

cd /app
echo "⏳ Waiting for MySQL to be ready..."

# Wait for MySQL to accept connections
until mysqladmin ping -h"$DB_HOST" -P"$DB_PORT" --silent; do
  sleep 2
done

echo "✅ MySQL is up!"

echo "📥 Running flask db upgrade..."
export FLASK_APP=app:create_app     # ✅ points to factory function
export FLASK_ENV=development
python -m flask db upgrade

echo "🚀 Starting Flask app..."
exec flask run --host=0.0.0.0 --port=5000
