import os
import requests
from flask import Blueprint, redirect, request, session
from website.extensions import db
from website.models import User

google_auth = Blueprint('google_auth', __name__)

@google_auth.route("/login/google")
def login_google():
    return redirect(
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={os.getenv('GOOGLE_CLIENT_ID')}"
        f"&redirect_uri=http://localhost:8888/login/google/callback"
        f"&response_type=code"
        f"&scope=openid%20profile%20email%20https://www.googleapis.com/auth/calendar.readonly"
    )

@google_auth.route("/login/google/callback")
def callback_google():
    session.clear()
    code = request.args.get('code')
    if not code:
        return "Authorization failed", 400

    token_response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            'code': code,
            'client_id': os.getenv('GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
            'redirect_uri': 'http://localhost:8888/login/google/callback',
            'grant_type': 'authorization_code'
        }
    )
    token = token_response.json().get("access_token")

    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"access_token": token}
    ).json()

    user = User.query.filter_by(email=user_info.get("email")).first()
    if user:
        user.name = user_info.get("name")
        user.google_access_token = token
    else:
        user = User(
            email=user_info.get("email"),
            name=user_info.get("name"),
            google_access_token=token
        )
        db.session.add(user)

    db.session.commit()
    session["user_id"] = user.user_id
    return redirect("/login/todoist")
