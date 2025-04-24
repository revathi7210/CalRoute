import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate


db = SQLAlchemy()
migrate = Migrate()

def create_app():
    # Initialize the Flask app
    app = Flask(__name__)
    # app.config['SECRET_KEY'] = 'your_secret_key_here'  # needed for sessions, etc.

    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SQLALCHEMY_DATABASE_URI']
    # Register Blueprints
    from .routes import main
    app.register_blueprint(main)

    

    db.init_app(app)
    migrate.init_app(app, db)

    from . import models 

    # (Optional) If you have an auth blueprint, it might look like this:
    # from .auth import auth
    # app.register_blueprint(auth, url_prefix='/auth')

    return app
