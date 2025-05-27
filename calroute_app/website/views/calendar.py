import requests
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from flask import Blueprint
from website.extensions import db
from website.models import RawTask, Location
from website.google_maps_helper import geocode_address
from website.location_utils import extract_place_name

calendar_bp = Blueprint('calendar', __name__)

def fetch_google_calendar_events(user):
    if not user.google_access_token:
        return

    headers = {"Authorization": f"Bearer {user.google_access_token}"}
    local_tz = ZoneInfo("America/Los_Angeles")
    today_local = datetime.now(local_tz).date()
    
    start_of_day_local = datetime.combine(today_local, time.min, tzinfo=local_tz)
    end_of_day_local = datetime.combine(today_local, time(23, 59, 59), tzinfo=local_tz)

    time_min = start_of_day_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    time_max = end_of_day_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    print(f"▶️ Fetching calendar from {time_min} to {time_max}")

    response = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers=headers,
        params={
            'timeMin': time_min,
            'timeMax': time_max,
            'singleEvents': True,
            'orderBy': 'startTime'
        }
    )
    if response.status_code != 200:
        return

    events = response.json().get("items", [])
    for event in events:
        # Skip events that are already in our database
        if RawTask.query.filter_by(external_id=event["id"], source='google_calendar').first():
            continue

        # Get start and end times
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if not start or not end:
            continue

        # Convert to local timezone
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(local_tz)
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')).astimezone(local_tz)

        # Skip events that are not today
        if start_dt.date() != today_local:
            continue

        # Handle location
        location_obj = None
        loc_name = event.get("location")
        if loc_name:
            lat, lng = geocode_address(loc_name)
            if lat is not None:
                location_obj = Location.query.filter_by(latitude=lat, longitude=lng).first()
                if not location_obj:
                    location_obj = Location(
                        name=extract_place_name(loc_name),  # Extract just the place name
                        address=loc_name,
                        latitude=lat,
                        longitude=lng
                    )
                    db.session.add(location_obj)
                    try:
                        db.session.flush()
                    except IntegrityError:
                        db.session.rollback()
                        continue

        # Calculate duration in minutes
        duration = int((end_dt - start_dt).total_seconds() / 60)

        # Create the raw task
        raw_task = RawTask(
            user_id=user.user_id,
            source='google_calendar',
            external_id=event["id"],
            title=event.get("summary", "No Title"),
            description=event.get("description"),
            start_time=start_dt,
            end_time=end_dt,
            location_id=location_obj.location_id if location_obj else None,
            priority=1,  # Calendar events are high priority
            duration=duration,
            status='not_completed'
        )
        db.session.add(raw_task)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()