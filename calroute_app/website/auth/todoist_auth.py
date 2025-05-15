import os
import requests
from flask import Blueprint, redirect, request, session
from website.extensions import db
from website.models import User, UserPreference

todoist_auth = Blueprint('todoist_auth', __name__)

@todoist_auth.route("/login/todoist")
def login_todoist():
    return redirect(
        f"https://todoist.com/oauth/authorize"
        f"?client_id={os.getenv('TODOIST_CLIENT_ID')}"
        f"&scope=data:read_write&state=xyz"
        f"&redirect_uri=http://localhost:8888/login/todoist/callback"
    )

@todoist_auth.route("/login/todoist/callback")
def callback_todoist():
    code = request.args.get("code")
    token = requests.post(
        "https://todoist.com/oauth/access_token",
        data={
            "client_id": os.getenv("TODOIST_CLIENT_ID"),
            "client_secret": os.getenv("TODOIST_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": "http://localhost:8888/login/todoist/callback"
        }
    ).json().get("access_token")

    user = User.query.get(session["user_id"])
    user.todoist_token = token
    db.session.commit()

    frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
    pref = UserPreference.query.filter_by(user_id=user.user_id).first()
    if pref:
        return redirect(f"{frontend}/homepage")
    else:
        return redirect(f"{frontend}/preferences")
