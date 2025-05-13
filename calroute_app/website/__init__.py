import os
from flask import Flask
from .optimize_routes import optimize_bp

from .extensions import db, migrate
from flask_cors import CORS

def create_app():
    # Initialize the Flask app
    app = Flask(__name__)
    # app.config['SECRET_KEY'] = 'your_secret_key_here'  # needed for sessions, etc.

    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI', 'sqlite:///calroute.db')
    app.config['GOOGLE_MAPS_API_KEY'] = os.getenv('GOOGLE_MAPS_API_KEY')
    app.config['GOOGLE_GENAI_API_KEY'] = os.getenv('GOOGLE_GENAI_API_KEY')

    # Register Blueprints
    from .routes import main
    app.register_blueprint(main)
    app.register_blueprint(optimize_bp)

    db.init_app(app)
    migrate.init_app(app, db)

    from . import models

    # (Optional) If you have an auth blueprint, it might look like this:
    # from .auth import auth
    # app.register_blueprint(auth, url_prefix='/auth')

    CORS(app,
         supports_credentials=True,
         resources={r"/*": {"origins": "http://localhost:8080"}})

    return app
