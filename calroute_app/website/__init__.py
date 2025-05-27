import os
from flask import Flask
from flask_cors import CORS
from website.extensions import db, migrate
from .optimize_routes import optimize_bp

def create_app():
    app = Flask(__name__)

    # Load critical config from environment variables
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["SQLALCHEMY_DATABASE_URI"]
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = os.environ.get("SECRET_KEY", "dev")

    # ✅ Add all your custom env vars into app.config
    app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID")
    app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET")
    app.config["TODOIST_CLIENT_ID"] = os.environ.get("TODOIST_CLIENT_ID")
    app.config["TODOIST_CLIENT_SECRET"] = os.environ.get("TODOIST_CLIENT_SECRET")
    app.config["FRONTEND_URL"] = os.environ.get("FRONTEND_URL")
    app.config["GOOGLE_MAPS_API_ID"] = os.environ.get("GOOGLE_MAPS_API_ID")
    app.config["GOOGLE_MAPS_API_KEY"] = os.environ.get("GOOGLE_MAPS_API_KEY")
    app.config["GOOGLE_GENAI_API_KEY"] = os.environ.get("GOOGLE_GENAI_API_KEY")

    # ✅ Enable CORS so frontend can access backend
    CORS(app, 
         resources={r"/*": {"origins": ["http://localhost:8080"], 
                           "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                           "allow_headers": ["Content-Type", "Authorization"],
                           "supports_credentials": True}})

    # ✅ Init extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # ✅ Register blueprints
    from website.auth.google_auth import google_auth
    from website.auth.todoist_auth import todoist_auth
    from website.views.core import core
    from website.views.calendar import calendar_bp
    from website.views.tasks import tasks_bp
    from website.views.preferences import preferences_bp

    app.register_blueprint(google_auth)
    app.register_blueprint(todoist_auth)
    app.register_blueprint(core)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(preferences_bp)
    app.register_blueprint(optimize_bp)

    return app