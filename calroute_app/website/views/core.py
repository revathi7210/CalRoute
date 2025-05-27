from flask import Blueprint, render_template, session, jsonify
from website.models import User
import pytz
from datetime import datetime

core = Blueprint('core', __name__)

@core.route("/")
def landing():
    return render_template("landingpage.html")

@core.route("/me")
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({}), 404

    return jsonify({
        "id": user.user_id,
        "name": user.name,
        "email": user.email,
        "todoist_token": user.todoist_token or ""
    })

def get_current_time_pst():
    pacific = pytz.timezone('US/Pacific')
    current_time = datetime.now(pacific)
    print(f"Current Pacific time: {current_time.isoformat()}")
    return current_time