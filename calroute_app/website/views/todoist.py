import os
import re
from datetime import datetime
from flask import current_app
from website.extensions import db
from website.models import RawTask, Location, UserPreference
from website.location_resolver import resolve_location_for_task
from website.google_maps_helper import find_nearest_location
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

        from website.llm_utils import call_gemini_for_place_type, get_user_home_address, get_nearest_location_from_maps
        if location is None:
            print("before get home addr")
            home_address = get_user_home_address()
            print("after get home addr")
            generic_place = call_gemini_for_place_type(task_title, home_address)
            print(f"afetr generic place {generic_place}")
            suggested_place = get_nearest_location_from_maps(home_address, generic_place)
            print(f"after suggested place {suggested_place}")
            print("nexts")
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