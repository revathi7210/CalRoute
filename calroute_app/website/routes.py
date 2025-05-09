import os
import requests
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from flask import Blueprint, render_template, redirect, request, session, url_for, jsonify

from .models import User, db, RawTask, Location

from todoist_api_python.api import TodoistAPI
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

main = Blueprint('main', __name__)

TODOIST_API_KEY = os.environ.get("TODOIST_CLIENT_SECRET")
api = TodoistAPI(TODOIST_API_KEY)

llm_template = (
    "You are tasked with extracting specific information from the following text content: {dom_content}. "
    "Please follow these instructions carefully:\n\n"
    "1. **Extract Information:** Only extract the information that directly matches the provided description: {parse_description}. \n"
    "2. **No Extra Content:** Do not include any additional text, comments, or explanations in your response.\n"
    "3. **Empty Response:** If no information matches the description, return an empty string ('').\n"
    "4. **Direct Data Only:** Your output should contain only the data that is explicitly requested, with no other text.\n"
)
model = OllamaLLM(model="llama3", base_url="http://host.docker.internal:11434")


# Landing page
@main.route("/")
def landing():
    return render_template("landingpage.html")


# Google login
@main.route("/login/google")
def login_google():
    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={os.getenv('GOOGLE_CLIENT_ID')}"
        f"&redirect_uri=http://localhost:8888/login/google/callback"
        f"&response_type=code"
        f"&scope=openid%20profile%20email%20https://www.googleapis.com/auth/calendar.readonly"
    )
    return redirect(google_auth_url)


# Google callback
@main.route("/login/google/callback")
def callback_google():
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
    if token_response.status_code != 200:
        return "Token exchange failed", 400

    token_json = token_response.json()
    google_token = token_json.get('access_token')

    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={'access_token': google_token}
    ).json()

    email = user_info.get('email')
    name = user_info.get('name')
    if not email or not name:
        return "Failed to fetch user info", 400

    user = User.query.filter_by(email=email).first()
    if user:
        user.name = name
        user.google_access_token = google_token
    else:
        user = User(email=email, name=name, google_access_token=google_token)
        db.session.add(user)
    db.session.commit()

    session['user_id'] = user.user_id

    # Decide where to send after Google login:
    frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
    if user.todoist_token:
        # Returning user → straight to React Homepage
        return redirect(f"{frontend}/homepage")
    else:
        # First-timer → kick off Todoist OAuth
        return redirect(url_for("main.login_todoist"))


# Todoist login
@main.route("/login/todoist")
def login_todoist():
    todoist_auth_url = (
        f"https://todoist.com/oauth/authorize?client_id={os.getenv('TODOIST_CLIENT_ID')}"
        f"&scope=data:read_write&state=random_csrf_token"
        f"&redirect_uri=http://localhost:8888/login/todoist/callback"
    )
    return redirect(todoist_auth_url)


# Todoist callback
@main.route("/login/todoist/callback")
def callback_todoist():
    code = request.args.get("code")
    if not code:
        return "Authorization failed", 400

    response = requests.post(
        "https://todoist.com/oauth/access_token",
        data={
            "client_id": os.getenv("TODOIST_CLIENT_ID"),
            "client_secret": os.getenv("TODOIST_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": "http://localhost:8888/login/todoist/callback"
        }
    )
    if response.status_code != 200:
        return "Token exchange failed", 400

    todoist_token = response.json().get("access_token")
    user_id = session.get("user_id")
    if not user_id:
        return "User session not found", 400

    user = User.query.get(user_id)
    if not user:
        return "User not found in database", 400

    user.todoist_token = todoist_token
    db.session.commit()

    # After Todoist OAuth, send everyone to the React Homepage
    frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return redirect(f"{frontend}/homepage")


# Protected schedule route (optional if still used server-side)
@main.route("/schedule")
def schedule():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.landing"))

    user = User.query.get(user_id)
    if not user or not user.google_access_token or not user.todoist_token:
        return redirect(url_for("main.landing"))

    fetch_google_calendar_events(user)
    parse_and_store_tasks(user)
    return render_template("schedule.html", user=user)


# “Who am I?” endpoint for React
@main.route("/me")
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({}), 401

    u = User.query.get(user_id)
    if not u:
        return jsonify({}), 404

    return jsonify({
        "id":            u.user_id,
        "name":          u.name,
        "email":         u.email,
        "todoist_token": u.todoist_token or ""
    })