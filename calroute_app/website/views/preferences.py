from flask import Blueprint, request, jsonify, session
from website.extensions import db
from website.models import User, UserPreference, Location
from website.google_maps_helper import geocode_address
from datetime import datetime, time

preferences_bp = Blueprint('preferences', __name__)

def parse_time_str(ts: str):
    if not ts:
        return None
    try:
        return time.fromisoformat(ts)
    except ValueError:
        try:
            return datetime.strptime(ts, "%H:%M").time()
        except ValueError:
            return None

@preferences_bp.route("/preferences", methods=["POST"])
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

    pref.max_daily_hours = data.get("max_daily_hours", pref.max_daily_hours)
    pref.travel_mode = data.get("travel_mode", pref.travel_mode)
    pref.prioritization_style = data.get("prioritization_style", pref.prioritization_style)
    pref.work_start_time = parse_time_str(data.get("work_start_time"))
    pref.work_end_time = parse_time_str(data.get("work_end_time"))

    # home location
    home_addr = data.get("home_address")
    if home_addr:
        lat, lng = geocode_address(home_addr)
        if lat is not None:
            loc = Location.query.filter_by(user_id=user_id, address=home_addr).first()
            if not loc:
                loc = Location(
                    user_id=user_id,
                    name=home_addr,
                    address=home_addr,
                    latitude=lat,
                    longitude=lng
                )
                db.session.add(loc)
                db.session.flush()
            pref.home_location_id = loc.location_id

    # favorite store location
    fav_addr = data.get("favorite_store_address")
    if fav_addr:
        lat, lng = geocode_address(fav_addr)
        if lat is not None:
            loc = Location.query.filter_by(user_id=user_id, address=fav_addr).first()
            if not loc:
                loc = Location(
                    user_id=user_id,
                    name=fav_addr,
                    address=fav_addr,
                    latitude=lat,
                    longitude=lng
                )
                db.session.add(loc)
                db.session.flush()
            pref.favorite_store_location_id = loc.location_id

    db.session.add(pref)
    db.session.commit()

    return jsonify({"message": "Preferences saved", "redirect": "/homepage"}), 200
