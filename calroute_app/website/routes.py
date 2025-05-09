import os
import requests
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from flask import Blueprint, render_template, redirect, request, session, url_for, jsonify

from .models import User, db, RawTask, Location, UserPreference

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

def fetch_google_calendar_events(user):
    headers = {
        "Authorization": f"Bearer {user.google_access_token}"
    }

    now = datetime.utcnow().isoformat() + "Z"
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events"
    params = {
        'timeMin': now,
        'singleEvents': True,
        'orderBy': 'startTime',
        'maxResults': 50
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print("Failed to fetch events:", response.text)
        return

    events = response.json().get("items", [])

    for event in events:
        event_id = event.get("id")
        summary = event.get("summary", "No Title")
        description = event.get("description", "")
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        location_text = event.get("location")

        if not start or not end:
            continue

        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
        except Exception as e:
            print(f"Skipping event due to date parsing error: {e}")
            continue

        existing = RawTask.query.filter_by(external_id=event_id, source='google_calendar').first()
        if existing:
            continue

        # Handle location creation
        location_obj = None
        if location_text:
            location_obj = Location.query.filter_by(user_id=user.user_id, name=location_text).first()
            if not location_obj:
                location_obj = Location(
                    user_id=user.user_id,
                    name=location_text
                )
                db.session.add(location_obj)
                try:
                    db.session.flush()  # Ensure location_obj.id is available
                except IntegrityError:
                    db.session.rollback()
                    print("Duplicate location or DB error occurred.")
                    continue

        task = RawTask(
            user_id=user.user_id,
            source='google_calendar',
            external_id=event_id,
            title=summary,
            description=description,
            start_time=start_dt,
            end_time=end_dt,
            raw_data=event,
            location_id=location_obj.location_id if location_obj else None
        )

        db.session.add(task)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        print("Duplicate event or DB error occurred.")


def split_content(content, chunk_size=500):
    return [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]

def parse_ollama(dom_chunks, parse_description):
    prompt = ChatPromptTemplate.from_template(llm_template)
    chain = prompt | model
    parsed_results = []

    for i, chunk in enumerate(dom_chunks, 1):
        response = chain.invoke({
            "dom_content": chunk,
            "parse_description": parse_description
        })
        parsed_results.append(response)

    return "\n".join(parsed_results)


def parse_and_store_tasks(user):
    api = TodoistAPI(user.todoist_token)
    try:
        paginator = api.get_tasks()
        tasks = list(paginator)  # This should return a flat list of Task objects
    except Exception as e:
        print("Error fetching Todoist tasks:", e)
        return

    if not tasks:
        print("No tasks found.")
        return

    content_block = ""

    for task_list in tasks:
        for task in task_list:
            # print(task)
            # print("\n")
            # if "Quick Add" in task.content or "Assign a task" in task.content:
            #     continue
            name = task.content
            due = task.due.string if task.due else "None"
            content_block += f"{name} at {due}\n"
    parse_description = (
        """For each line of the content, extract the following:\n
        • task: what needs to be done\n
        • location: place involved (if mentioned)\n
        • date: when it happens (use format 'DD Mon', e.g., '14 Apr') or 'none'\n
        • time: time of day (e.g., '16:00') or 'none'\n\n
        Return one line per task in this exact format:\n
        "task=..., location=..., date=..., time=...\n\n"
        If a value is not mentioned, use 'none'. Only return this structured output — no explanation or extra text.
        Example:
        Input: go to Starbucks at 8 Apr 15:00
        Output: task=go to Starbucks, location=Starbucks, date=8 Apr, time=15:00

        Input: Learn DSA
        Output: task=Learn DSA, location=none, date=none, time=none"""
    )

    chunks = split_content(content_block)
    parsed = parse_ollama(chunks, parse_description)

    print(parsed)
    print("\n")
    for line in parsed.splitlines():
        match = re.match(r'task=(.*?),\s*location=(.*?),\s*date=(.*?),\s*time=(.*)', line.strip().strip('"'))
        if not match:
            continue
        print(match.groups())

        task, location, date, time = match.groups()
        location_id = None

        if location.lower() != "none":
            loc = Location.query.filter_by(name=location).first()
            if not loc:
                loc = Location(name=location)
                db.session.add(loc)
                db.session.commit()
            location_id = loc.location_id

        start_time = None
        if date.lower() != "none" or time.lower() != "none":
            try:
                dt_str = f"{date} {time}" if date != "none" else time
                start_time = datetime.strptime(dt_str.strip(), "%d %b %H:%M")
                start_time = start_time.replace(year=datetime.now().year)
            except:
                pass

        raw_task = RawTask(
            user_id=user.user_id,
            source="todoist",
            external_id=f"{task}-{date}-{time}",  # Not ideal — better to use actual ID if available
            title=task,
            description=None,
            location_id=location_id,
            start_time=start_time,
            end_time=None,
            due_date=start_time,
            priority=3,
            raw_data={},
        )
        try:
            db.session.add(raw_task)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("Could not insert:", task, e)

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

# helper to parse “HH:MM” or “HH:MM:SS”
def parse_time_str(ts: str):
    if not ts:
        return None
    try:
        # first try full isoformat
        return time.fromisoformat(ts)
    except ValueError:
        try:
            # fallback to H:M
            return datetime.strptime(ts, "%H:%M").time()
        except ValueError:
            return None

@main.route("/preferences", methods=["POST"])
def save_preferences():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json() or {}

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        pref = UserPreference(user_id=user_id)

    pref.max_daily_hours        = data.get("max_daily_hours", pref.max_daily_hours)
    pref.travel_mode            = data.get("travel_mode", pref.travel_mode)
    pref.prioritization_style   = data.get("prioritization_style", pref.prioritization_style)

    pref.work_start_time        = parse_time_str(data.get("work_start_time"))
    pref.work_end_time          = parse_time_str(data.get("work_end_time"))

    # home address + coords
    pref.home_address           = data.get("home_address", pref.home_address)
    pref.home_lat               = data.get("home_lat", pref.home_lat)
    pref.home_lng               = data.get("home_lng", pref.home_lng)

    # inline favourite-store address + coords
    pref.favorite_store_address = data.get("favorite_store_address", pref.favorite_store_address)
    pref.favorite_store_lat     = data.get("favorite_store_lat", pref.favorite_store_lat)
    pref.favorite_store_lng     = data.get("favorite_store_lng", pref.favorite_store_lng)

    db.session.add(pref)
    db.session.commit()

    return jsonify({"message": "Preferences saved"}), 200