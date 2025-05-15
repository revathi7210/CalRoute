import requests
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from flask import Blueprint
from website.extensions import db
from website.models import RawTask, Location
from website.google_maps_helper import geocode_address

calendar_bp = Blueprint('calendar', __name__)

def fetch_google_calendar_events(user):
    headers = {"Authorization": f"Bearer {user.google_access_token}"}
    local_tz = ZoneInfo("America/Los_Angeles")
    today_local = datetime.now(local_tz).date()
    now = datetime.utcnow().isoformat() + "Z"
    end_of_day_local = datetime.combine(today_local, time(23,59,59), tzinfo=local_tz)
    time_max = end_of_day_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    response = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers=headers,
        params={
            'timeMin': now,
            'timeMax': time_max,
            'singleEvents': True,
            'orderBy': 'startTime'
        }
    )
    if response.status_code != 200:
        return

    events = response.json().get("items", [])
    for event in events:
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))

        if RawTask.query.filter_by(external_id=event["id"], source='google_calendar').first():
            continue

        loc_name = event.get("location")
        location_obj = None
        if loc_name:
            lat, lng = geocode_address(loc_name)
            if lat is not None:
                location_obj = Location(
                    user_id=user.user_id,
                    name=loc_name,
                    latitude=lat,
                    longitude=lng,
                    address=loc_name
                )
                db.session.add(location_obj)
                try:
                    db.session.flush()
                except IntegrityError:
                    db.session.rollback()

        raw_task = RawTask(
            user_id=user.user_id,
            source='google_calendar',
            external_id=event["id"],
            title=event.get("summary", "No Title"),
            description=event.get("description"),
            start_time=start_dt,
            end_time=end_dt,
            location_id=location_obj.location_id if location_obj else None,
            raw_data=event,
            priority=1
        )
        db.session.add(raw_task)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
