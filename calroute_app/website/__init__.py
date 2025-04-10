from flask import Flask

def create_app():
    # Initialize the Flask app
    app = Flask(__name__)
    # app.config['SECRET_KEY'] = 'your_secret_key_here'  # needed for sessions, etc.

    # Register Blueprints
    from .routes import main
    app.register_blueprint(main)

    # (Optional) If you have an auth blueprint, it might look like this:
    # from .auth import auth
    # app.register_blueprint(auth, url_prefix='/auth')

    return app
