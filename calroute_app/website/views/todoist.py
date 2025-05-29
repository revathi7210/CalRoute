import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import current_app
from website.extensions import db
from website.models import RawTask, Location, UserPreference
from website.location_resolver import resolve_location_for_task
from website.location_utils import extract_place_name
from website.google_maps_helper import find_nearest_location
from todoist_api_python.api import TodoistAPI
import google.generativeai as genai
from sqlalchemy.exc import IntegrityError
from website.llm_utils import call_gemini_for_place_type, get_user_home_address, get_nearest_location_from_maps, VALID_GOOGLE_PLACE_TYPES, get_user_preferred_locations

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
    result = resp.text.strip().lower() == "yes"
    print(f"LLM check result: {result}")
    return result

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
        \"task=..., location=..., date=..., time=...\n\n\"
        If a value is not mentioned, use 'none'. Only return this structured output — no explanation or extra text.
        Example:
        Input: go to Starbucks at 8 Apr 15:00
        Output: task=go to Starbucks, location=Starbucks, date=8 Apr, time=15:00

        Input: Learn DSA
        Output: task=Learn DSA, location=none, date=none, time=none"""
    )

    parsed = parse_gemini(split_content(content_block), parse_description)
    parsed_tasks = parsed.splitlines()

    def match_preferred_location(preferred_locations, task_title, place_type):
        task_lower = task_title.lower()
        if place_type == "gym" or any(x in task_lower for x in ['workout', 'exercise', 'gym']):
            gym = preferred_locations.get("gym")
            if gym:  # Only try to use gym if it exists in preferences
                return Location.query.get(gym["location_id"])
            print("No preferred gym location set, will try to find nearby gym")
            return None  # Return None to fall through to step 3

        if place_type in ["supermarket", "grocery_store"] or any(x in task_lower for x in ['buy', 'shop', 'grocery']):
            for store in preferred_locations.get("grocery_stores", []):
                if store and can_task_at_preferred(task_title, store["address"]):
                    return Location.query.get(store["location_id"])
            print("No preferred grocery store found, will try to find nearby store")
            return None  # Return None to fall through to step 3

        return None  # Return None for any other case to fall through to step 3

    for line in parsed_tasks:
        match = re.match(r"task=(.*?),\s*location=(.*?),\s*date=(.*?),\s*time=(.*)", line.strip().strip("\""))
        if not match:
            continue
        task_title, location_name, date, time_str = match.groups()
        if not task_title.strip():
            continue

        external_id = f"{task_title}-{date}-{time_str}"
        if RawTask.query.filter_by(external_id=external_id).first():
            continue

        preferred_locations = get_user_preferred_locations(user.user_id)
        print(f"Got preferred locations: {preferred_locations}")

        home_address = get_user_home_address()
        generic_place = call_gemini_for_place_type(task_title, home_address)
        print(f"LLM returned generic_place: {generic_place}")

        place_type = generic_place if generic_place else None
        location = None

        # 1️⃣ Use explicitly stated location from task
        if location_name.lower() != "none":
            try:
                location = resolve_location_for_task(user, location_name, task_title)
            except Exception as e:
                print(f"Error resolving location {e}")

        # 2️⃣ If not, try preferred locations
        if not location and place_type:
            location = match_preferred_location(preferred_locations, task_title, place_type)
            current_app.logger.info(f"Matched preferred location: {location}")
            
        # 3️⃣ If still not found, fallback to Maps + LLM
        if not location :
            suggested_place = get_nearest_location_from_maps(home_address, place_type)
            current_app.logger.info(f"Suggested place from maps: {suggested_place}")
            if suggested_place:
                user_pref = UserPreference.query.filter_by(user_id=user.user_id).first()
                user_lat = user_lng = None
                if user_pref and user_pref.home_location_id:
                    home = Location.query.get(user_pref.home_location_id)
                    if home:
                        user_lat, user_lng = home.latitude, home.longitude

                if user_lat and user_lng:
                    place_data = find_nearest_location(
                        api_key=current_app.config.get("GOOGLE_MAPS_API_KEY"),
                        query=suggested_place,
                        user_lat=user_lat,
                        user_lng=user_lng
                    )

                    if place_data:
                        location = Location.query.filter_by(
                            latitude=place_data["lat"],
                            longitude=place_data["lng"]
                        ).first()
                        if not location:
                            location = Location(
                                name=extract_place_name(place_data["address"]),
                                address=place_data["address"],
                                latitude=place_data["lat"],
                                longitude=place_data["lng"]
                            )
                            db.session.add(location)
                            try:
                                db.session.flush()
                            except IntegrityError:
                                db.session.rollback()
                                location = Location.query.filter_by(
                                    latitude=place_data["lat"],
                                    longitude=place_data["lng"]
                                ).first()

        print(f"Creating RawTask: title={task_title},  place_type={place_type}, location_id={location.location_id if location else None}")

        start_time = None
        if date != "none" and time_str != "none":
            try:
                start_time = datetime.strptime(f"{date} {time_str}", "%d %b %H:%M").replace(year=datetime.now().year)
            except Exception:
                pass

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
            existing_task.is_location_flexible = True
            existing_task.place_type = place_type
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
                priority=3,
                duration=45,
                status='not_completed',
                is_location_flexible=True,
                place_type=place_type
            )
            try:
                db.session.add(raw_task)
                db.session.commit()
                print(f"Committed RawTask: title={task_title},  place_type={place_type}, location_id={location.location_id if location else None}")
            except IntegrityError:
                db.session.rollback()
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
                    existing_task.is_location_flexible = True
                    existing_task.place_type = place_type
                    db.session.commit()
                    print(f"Updated existing RawTask after conflict: title={task_title},  place_type={place_type}, location_id={location.location_id if location else None}")


def get_today_tasks(todoist_token):
    try:
        tasks = tasks = get_today_tasks(todoist_token)
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