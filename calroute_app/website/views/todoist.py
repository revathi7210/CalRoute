import os
import re
from datetime import datetime
from flask import current_app
from website.extensions import db
from website.models import RawTask, Location, UserPreference
from website.location_resolver import resolve_location_for_task
from website.google_maps_helper import find_nearest_location
import math
from website.llm_utils import get_user_preferred_locations, get_user_home_address
from todoist_api_python.api import TodoistAPI
import google.generativeai as genai
from sqlalchemy.exc import IntegrityError

def split_content(content, chunk_size=500):
    return [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]

def parse_gemini(dom_chunks, parse_description):
    # ✅ Set up Gemini client
    genai.configure(api_key=os.environ['GOOGLE_GENAI_API_KEY'])
    model = genai.GenerativeModel("gemini-1.5-flash-latest") 

    results = []
    for chunk in dom_chunks:
        prompt = f"""
You are tasked with extracting specific information from the following text content:

{chunk}

Please extract only:
{parse_description}

No extra text. Return '' if nothing matches. Only output the requested data.
"""
        response = model.generate_content(prompt)
        results.append(response.text.strip())

    return "\n".join(results)

def haversine(lat1, lng1, lat2, lng2):
    # returns miles between two lat/lng
    R = 3958.8  # earth radius in miles
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def can_task_at_preferred(task_text: str, location_name: str) -> bool:
    print(f"task_text: {task_text}")
    print(f"location_name: {location_name}")
    print("hellos")
    prompt = f"""
You are a helpful assistant for a personal productivity app. Your job is to determine if a task can be done at a specific location.

Task: "{task_text}"
Location: "{location_name}"

Rules:
1. For any grocery shopping task (buying food, produce, groceries, etc.), ANY grocery store or supermarket is a valid location, regardless of the specific store name
2. For gym/exercise tasks, only gyms and fitness centers are valid locations
3. For other tasks, use common sense about what can be done where
4. Be inclusive - if a task could reasonably be done at a location, say yes
5. Reply with exactly "yes" or "no"

Examples:
- Task: "buy apples" at any grocery store → yes
- Task: "get groceries" at any supermarket → yes
- Task: "buy food" at any store that sells food → yes
- Task: "workout" at a gym → yes
- Task: "workout" at a grocery store → no

Reply with only "yes" or "no".
"""
    genai.configure(api_key=current_app.config['GOOGLE_GENAI_API_KEY'])
    model = genai.GenerativeModel("gemini-1.5-flash-latest")
    resp = model.generate_content(prompt)
    return resp.text.strip().lower() == "yes"

def parse_and_store_tasks(user):
    api = TodoistAPI(user.todoist_token)
    tasks = api.get_tasks()

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

    parsed = parse_gemini(
        split_content(content_block),
        parse_description
    )
    parsed_tasks = parsed.splitlines()

    pref_data = get_user_preferred_locations(user.user_id)
    home_address = get_user_home_address()

    for line in parsed_tasks:
        match = re.match(
            r"task=(.*?),\s*location=(.*?),\s*date=(.*?),\s*time=(.*)",
            line.strip().strip("\""),
        )
        if not match:
            continue
        task_title, location_name, date, time_str = match.groups()
        external_id = f"{task_title}-{date}-{time_str}"

        existing_task = RawTask.query.filter_by(external_id=external_id).first()
        if existing_task:
            continue

        location = None
        if location_name.lower() != "none":
            location = resolve_location_for_task(user, location_name, task_title)
        if not location and pref_data and home_address:
            user_pref = UserPreference.query.filter_by(user_id=user.user_id).first()
            if user_pref and user_pref.home_location_id:
                home = Location.query.get(user_pref.home_location_id)
                home_lat, home_lng = home.latitude, home.longitude

                # Determine which type of locations to check based on task content
                task_lower = task_title.lower()
                candidates = []
                
                # Check for grocery-related tasks
                grocery_keywords = {'buy', 'get', 'shop', 'grocery', 'food', 'market', 'store'}
                if any(keyword in task_lower for keyword in grocery_keywords):
                    candidates.extend(pref_data.get('grocery_stores', []))
                
                # Check for gym-related tasks
                gym_keywords = {'workout', 'exercise', 'gym', 'train', 'fitness'}
                if any(keyword in task_lower for keyword in gym_keywords):
                    if pref_data.get('gym'):
                        candidates.append(pref_data['gym'])

                # If no specific type matched, check all locations
                if not candidates:
                    if pref_data.get('gym'):
                        candidates.append(pref_data['gym'])
                    candidates.extend(pref_data.get('grocery_stores', []))

                for loc_entry in candidates:
                    if not loc_entry:
                        continue
                    dist = haversine(home_lat, home_lng,
                                     loc_entry['latitude'], loc_entry['longitude'])
                    if dist <= 25 and can_task_at_preferred(task_title, loc_entry['address']):
                        location = Location.query.get(loc_entry['location_id'])
                        break

        from website.llm_utils import call_gemini_for_place_type, get_nearest_location_from_maps
        if not location:
            generic_place   = call_gemini_for_place_type(task_title, home_address)
            suggested_place = get_nearest_location_from_maps(home_address, generic_place)
            if suggested_place:
                # find or insert in DB
                user_pref = UserPreference.query.filter_by(user_id=user.user_id).first()
                if user_pref and user_pref.home_location_id:
                    home = Location.query.get(user_pref.home_location_id)
                    user_lat, user_lng = home.latitude, home.longitude
                else:
                    user_lat = user_lng = None

                if user_lat and user_lng:
                    place_data = find_nearest_location(
                        api_key=current_app.config.get("GOOGLE_MAPS_API_KEY"),
                        query=suggested_place,
                        user_lat=user_lat,
                        user_lng=user_lng
                    )

                    if place_data:
                         # try reusing an existing Location
                        location = Location.query.filter_by(
                            latitude=place_data["lat"],
                            longitude=place_data["lng"]
                        ).first()
                        if not location:
                            location = Location(
                                address=place_data["address"],
                                latitude=place_data["lat"],
                                longitude=place_data["lng"]
                            )
                            db.session.add(location)
                            try:
                                # flush only this insert
                                db.session.flush()
                            except IntegrityError:
                                # another thread/process just inserted it—rollback & re-fetch
                                db.session.rollback()
                                location = Location.query.filter_by(
                                    latitude=place_data["lat"],
                                    longitude=place_data["lng"]
                                ).first()

        start_time = None
        if date != "none" and time_str != "none":
            try:
                start_time = datetime.strptime(
                    f"{date} {time_str}", "%d %b %H:%M"
                ).replace(year=datetime.now().year)
            except Exception:
                pass
        
        # avoid inserting the same Todoist task twice
        existing_task = RawTask.query.filter_by(
            user_id=user.user_id,
            source="todoist",
            external_id=external_id
        ).first()
        if existing_task:
            # update if location or time changed
            existing_task.location_id = location.location_id if location else existing_task.location_id
            existing_task.start_time = start_time or existing_task.start_time
            existing_task.due_date = start_time or existing_task.due_date
            existing_task.title = task_title
            db.session.commit()
        else:
            raw_task = RawTask(
                user_id=user.user_id,
                source="todoist",
                external_id=external_id,
                title=task_title,
                location_id=location.location_id if location else None,
                start_time=start_time,
                end_time=None,
                due_date=start_time,
                priority=1,
                duration=45,  # Default 45 minutes for Todoist tasks
                status='not_completed'
            )
            try:
                db.session.add(raw_task)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                # Try one more time to find the task in case it was created by another request
                existing_task = RawTask.query.filter_by(
                    user_id=user.user_id,
                    source="todoist",
                    external_id=external_id
                ).first()
                if existing_task:
                    existing_task.location_id = location.location_id if location else existing_task.location_id
                    existing_task.start_time = start_time or existing_task.start_time
                    existing_task.due_date = start_time or existing_task.due_date
                    existing_task.title = task_title
                    db.session.commit()


def get_today_tasks(todoist_token):
    try:
        api = TodoistAPI(todoist_token)
        tasks = api.get_tasks()  # Use the API to get tasks instead of recursive call
        today = datetime.now().date()
        today_tasks = []
        for task in tasks:
            if task.due and task.due.date:
                # Handle both date-only and datetime string cases
                task_date = datetime.fromisoformat(task.due.date).date()
                if task_date == today:
                    today_tasks.append(task)
        return today_tasks
    except Exception as e:
        print(f"Error fetching Todoist tasks: {e}")
        return []    