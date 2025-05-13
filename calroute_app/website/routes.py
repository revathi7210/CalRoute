import os
import requests
import re
import requests
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from flask import Blueprint, render_template, redirect, request, session, url_for,current_app

from sqlalchemy.exc import IntegrityError
from flask import Blueprint, render_template, redirect, request, session, url_for, jsonify

from .models import User, db, RawTask, Location, UserPreference
from flask import Blueprint, render_template, redirect, request, session, url_for, jsonify,  current_app
from .models import User, db ,RawTask,Location, ScheduledTask

from sqlalchemy.orm import joinedload

from .models import User, db, RawTask, Location, UserPreference
from todoist_api_python.api import TodoistAPI
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

from .optimize_routes import run_optimization

main = Blueprint('main', __name__)
from website.google_maps_helper import find_nearest_location
from .location_resolver import resolve_location_for_task
from .llm_utils import call_gemini_for_location
from website.google_maps_helper import geocode_address

GOOGLE_MAPS_API = os.environ.get("GOOGLE_MAPS_API_ID")
TODOIST_API_KEY = os.environ.get("TODOIST_CLIENT_SECRET")  


main = Blueprint("main", __name__)

model = OllamaLLM(model="llama3", base_url="http://host.docker.internal:11434")

llm_template = (
    "You are tasked with extracting specific information from the following text content: {dom_content}. "
    "Please follow these instructions carefully:\n\n"
    "1. **Extract Information:** Only extract the information that directly matches the provided description: {parse_description}. \n"
    "2. **No Extra Content:** Do not include any additional text, comments, or explanations in your response.\n"
    "3. **Empty Response:** If no information matches the description, return an empty string ('').\n"
    "4. **Direct Data Only:** Your output should contain only the data that is explicitly requested, with no other text.\n"
)
@main.route("/")
def landing():
    return render_template("landingpage.html")

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
    if token_response.status_code != 200:
        return "Token exchange failed", 400

    token_json = token_response.json()
    google_token = token_json.get("access_token")

    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"access_token": google_token},
    ).json()

    user = User.query.filter_by(email=user_info.get("email")).first()
    if user:
        user.name = user_info.get("name")
        user.google_access_token = google_token
    else:
        user = User(
            email=user_info.get("email"),
            name=user_info.get("name"),
            google_access_token=google_token,
        )
        db.session.add(user)
    db.session.commit()

    session["user_id"] = user.user_id
    return redirect("/login/todoist")
    # Decide where to send after Google login:
    frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
    if user.todoist_token:
        # Returning user → straight to React Homepage
        return redirect(f"{frontend}/homepage")
    else:
        # First-timer → kick off Todoist OAuth
        return redirect(url_for("main.login_todoist"))


@main.route("/login/todoist")
def login_todoist():
    return redirect(
        f"https://todoist.com/oauth/authorize?client_id={os.getenv('TODOIST_CLIENT_ID')}&scope=data:read_write&state=xyz&redirect_uri=http://localhost:8888/login/todoist/callback"
    )

# Todoist callback
@main.route("/login/todoist/callback")
def callback_todoist():
    code = request.args.get("code")
    response = requests.post("https://todoist.com/oauth/access_token", data={
        "client_id": os.getenv("TODOIST_CLIENT_ID"),
        "client_secret": os.getenv("TODOIST_CLIENT_SECRET"),
        "code": code,
        "redirect_uri": "http://localhost:8888/login/todoist/callback",
    })
    user = User.query.get(session["user_id"])
    user.todoist_token = response.json().get("access_token")
    db.session.commit()

    # After Todoist OAuth, send everyone to the React Homepage
    frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return redirect(f"{frontend}/homepage")

def fetch_google_calendar_events(user):
    headers = {
        "Authorization": f"Bearer {user.google_access_token}"
    }

    print("INSIDE FETCH GOOGLE CALENDAR")

    # Set timeMin and timeMax to cover only today's full range
    local_tz = ZoneInfo("America/Los_Angeles") # GET THIS FROM USER ADDRESS

    # Get *today* in that zone
    today_local = datetime.now(local_tz).date()

    now = datetime.utcnow().isoformat() + "Z"
    print(now)

    # Make a timezone‐aware datetime for 23:59:59 local
    end_of_day_local = datetime.combine(today_local, time(23, 59, 59), tzinfo=local_tz)

    # Convert it to UTC
    end_of_day_utc = end_of_day_local.astimezone(timezone.utc)

    # Format for Google API (Zulu)
    time_max = end_of_day_utc.isoformat().replace("+00:00", "Z")

    print("Local end of day:", end_of_day_local)
    print("UTC-formatted timeMax:", time_max)

    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    params = {
        'timeMin': now,
        'timeMax': time_max,
        'singleEvents': True,
        'orderBy': 'startTime',
        'maxResults': 50
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print("Failed to fetch events:", response.text)
        return

    events = response.json().get("items", [])
    print("EVENTS")
    print(events)
    for event in events:
        event_id = event.get("id")
        summary = event.get("summary", "No Title")
        description = event.get("description", "")
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        # start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        # end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        location_text = event.get("location")

        if not start or not end:
            continue

        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
            print(start_dt)
            print(end_dt)
        except Exception as e:
            print(f"Skipping event due to date parsing error: {e}")
            continue

        existing = RawTask.query.filter(
            (RawTask.source == 'google_calendar') & (
                (RawTask.external_id == event_id) |
                (
                    (RawTask.user_id == user.user_id) &
                    (RawTask.start_time == start_dt) &
                    (RawTask.end_time == end_dt)
                )
            )
        ).first()
        if existing:
            continue

        # Handle location creation
        location_obj = None
        if location_text:
            location_obj = Location.query.filter_by(user_id=user.user_id, name=location_text).first()
            if not location_obj:
                lat, lng = geocode_address(location_text)

                if lat is None or lng is None:
                    print(f"⚠️ Skipping location: '{location_text}' could not be geocoded.")
                    continue  # skip adding this task altogether if geocode fails

                location_obj = Location(
                    # NEED TO ADD A LOCATION ID HERE, NO NEED FOR USER_ID
                    user_id=user.user_id,
                    name=location_text,
                    latitude=lat,
                    longitude=lng,
                    address=location_text
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
            priority = 1,
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
    return [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]

def parse_ollama(dom_chunks, parse_description):
    prompt = ChatPromptTemplate.from_template(
        "You are tasked with extracting specific information from the following text content: {dom_content}. Please extract only: {parse_description}. No extra text. Return '' if nothing matches. Only output the requested data."
    )
    chain = prompt | model
    results = [
        chain.invoke({"dom_content": chunk, "parse_description": parse_description})
        for chunk in dom_chunks
    ]
    return "\n".join(results)

def parse_and_store_tasks(user):

    api = TodoistAPI(user.todoist_token)
    tasks = api.get_tasks()

    # Flatten nested lists if any
    flat_tasks = []
    for item in tasks:
        if isinstance(item, list):
            flat_tasks.extend(item)
        else:
            flat_tasks.append(item)

    todoist_lines = []
    for t in flat_tasks:
        due_str = t.due.string if hasattr(t, 'due') and t.due and hasattr(t.due, 'string') and t.due.string else 'None'
        todoist_lines.append(f"{t.content} at {due_str}")

    content_block = "\n".join(todoist_lines)

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

    parsed = parse_ollama(
        split_content(content_block),
        parse_description
    )

    parsed_tasks = parsed.splitlines()

    for line in parsed_tasks:
        match = re.match(
            r"task=(.*?),\s*location=(.*?),\s*date=(.*?),\s*time=(.*)",
            line.strip().strip("\""),
        )
        if not match:
            continue
        task_title, location_name, date, time_str = match.groups()

        # 🟢 Step 1: Try to resolve location from CalRoute DB / UserPrefs
        location = None
        if location_name.lower() != "none":
            location = resolve_location_for_task(user, location_name, task_title)

        print("This is the location")
        print(location)
        # 🟢 Step 2: If no location found, use Gemini + Google Maps
        if location is None:
            suggested_place = call_gemini_for_location(task_title)
            if suggested_place:
                # Get user home location for proximity search
                user_pref = UserPreference.query.filter_by(user_id=user.user_id).first()
                user_lat = user_lng = None
                if user_pref and user_pref.home_location_id:
                    home = Location.query.get(user_pref.home_location_id)
                    if home:
                        user_lat, user_lng = home.latitude, home.longitude

                # Only proceed if we have lat/lng to search nearby
                if user_lat and user_lng:
                    place_data = find_nearest_location(
                        api_key=current_app.config.get("GOOGLE_MAPS_API_KEY"),
                        query=suggested_place,
                        user_lat=user_lat,
                        user_lng=user_lng
                    )

                    if place_data:
                        # Check if location already exists
                        existing = Location.query.filter_by(
                            user_id=user.user_id,
                            name=place_data["name"]
                        ).first()
                        if existing:
                            location = existing
                        else:
                            location = Location(
                                user_id=user.user_id,
                                name=place_data["name"],
                                address=place_data["address"],
                                latitude=place_data["lat"],
                                longitude=place_data["lng"]
                            )
                            db.session.add(location)
                            db.session.commit()
        print("This is the location after llm")
        print(location)
        # 🟢 Step 3: Parse datetime
        start_time = None
        if date != "none" and time_str != "none":
            try:
                start_time = datetime.strptime(
                    f"{date} {time_str}", "%d %b %H:%M"
                ).replace(year=datetime.now().year)
            except:
                pass

        # 🟢 Step 4: Create RawTask
        raw_task = RawTask(
            user_id=user.user_id,
            source="todoist",
            external_id=f"{task_title}-{date}-{time_str}",
            title=task_title,
            location_id=location.location_id if location else None,
            start_time=start_time,
            end_time=None,
            due_date=start_time,
            priority=3,
            raw_data={},
        )
        try:
            db.session.add(raw_task)
            db.session.commit()
        except Exception:
            db.session.rollback()
            print("Could not insert:", task, e)
    print("Done with parser")

def geocode_address(addr):
    params = {
      "address": addr,
      "key":     GOOGLE_MAPS_API
    }
    resp = requests.get("https://maps.googleapis.com/maps/api/geocode/json?", params=params)
    print(resp)
    data = resp.json()
    print(data)
    print("GEOCODE REQUEST →", addr)
    print("PARAMS:", params)
    print("GEOCODE RESPONSE STATUS:", data.get("status"))
    if data.get("results"):
        loc = data["results"][0]["geometry"]["location"]
        print("→ lat,lng:", loc["lat"], loc["lng"])
        return loc["lat"], loc["lng"]
    print("→ no results")
    return None, None


# Protected schedule route

# @main.route("/schedule")
# def schedule():
#     user_id = session.get("user_id")
#     if not user_id:
#         return redirect(url_for("main.landing"))

#     user = User.query.get(user_id)
#     if not user or not user.google_access_token or not user.todoist_token:
#         return redirect(url_for("main.landing"))

#     # 1) Pull in calendar + todoist tasks
#     fetch_google_calendar_events(user)
#     parse_and_store_tasks(user)

#     # results = (
#     #     db.session.query(RawTask, Location.name.label("loc_name"))
#     #               .join(Location, RawTask.location_id == Location.location_id)
#     #               .filter(RawTask.user_id == user.user_id)
#     #               .order_by(RawTask.start_time)
#     #               .all()
#     # )

#     # print(results)
#     # # 3) Separate into raw_tasks list & geocode locations
#     # raw_tasks = []
#     # task_locations = []
#     # for raw_task, loc_name in results:
#     #     raw_tasks.append(raw_task)

#     #     if not loc_name:
#     #         continue

#     #     lat, lng = geocode_address(loc_name)
#     #     if lat is None or lng is None:
#     #         continue

#     #     task_locations.append({
#     #         "lat":   lat,
#     #         "lng":   lng,
#     #         "title": raw_task.title
#     #     })
#     # print(raw_tasks)
#     # print(task_locations)

#     try:
#         run_optimization(user)
#     except Exception as e:
#         print("inside /scheudule")
#         print("❌ Optimization error:", e)

#     results = (
#         db.session.query(ScheduledTask, Location)
#         .join(Location, ScheduledTask.location_id == Location.location_id)
#         .filter(ScheduledTask.user_id == user.user_id)
#         .order_by(ScheduledTask.scheduled_start_time)
#         .all()
#     )

#     task_locations = [
#         {
#             "lat": location.latitude,
#             "lng": location.longitude,
#             "title": sched_task.title
#         }
#         for sched_task, location in results if location.latitude and location.longitude
#     ]

#     return render_template(
#         "schedule.html",
#         user=user,
#         raw_tasks=[],  # or ScheduledTask.query.filter_by(user_id=user.user_id).all() if needed
#         task_locations=task_locations
#     )

@main.route("/api/tasks", methods=["GET"])
def get_scheduled_tasks():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login/google")

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # 1) If there are no existing scheduled tasks, run your scheduler
    existing_count = ScheduledTask.query.filter_by(user_id=user_id).count()
    if existing_count == 0:
        current_app.logger.debug(f"No scheduled tasks for user {user_id}. Generating schedule...")
        try:
            fetch_google_calendar_events(user)
            parse_and_store_tasks(user)
            # If you have an optimization step, run it here
            run_optimization(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error scheduling tasks for user {user_id}: {e}")

    # 2) Now fetch the scheduled tasks
    results = (
        db.session.query(ScheduledTask, Location)
        .join(Location, ScheduledTask.location_id == Location.location_id)
        .filter(ScheduledTask.user_id == user_id)
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )

    tasks = []
    for task, loc in results:
        tasks.append({
            "id":         task.sched_task_id,
            "title":      task.title,
            "start_time": task.scheduled_start_time.strftime("%-I:%M %p"),
            "end_time":   task.scheduled_end_time.strftime("%-I:%M %p"),
            "lat":        loc.latitude,
            "lng":        loc.longitude,
        })

    return jsonify({ "tasks": tasks })


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

