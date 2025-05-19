from flask import Blueprint, request, jsonify, session, redirect, url_for
from website.extensions import db
from website.models import User, UserPreference, Location
from website.google_maps_helper import geocode_address
from datetime import datetime, time
from sqlalchemy import func

preferences_bp = Blueprint("preferences", __name__)

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

def get_or_create_location(address):
    """Helper function to get existing location or create a new one."""
    if not address:
        return None
    address = address.strip()
    lat, lng = geocode_address(address)
    if lat is None or lng is None:
        return None

    # Round coordinates to 5 decimal places (about 1.1 meters precision)
    lat = round(lat, 5)
    lng = round(lng, 5)

    # First try to find by exact address
    location = Location.query.filter_by(address=address).first()
    if location:
        return location

    # Then try to find by nearby coordinates (within ~10 meters)
    location = Location.query.filter(
        func.abs(Location.latitude - lat) < 0.0001,  # ~11 meters
        func.abs(Location.longitude - lng) < 0.0001  # ~11 meters at equator
    ).first()
    if location:
        return location

    # If no existing location found, create new one
    try:
        location = Location(
            address=address,
            latitude=lat,
            longitude=lng,
        )
        db.session.add(location)
        db.session.flush()
        return location
    except IntegrityError:
        db.session.rollback()
        # If we got an integrity error, try one more time to find an existing location
        # (in case one was created by another request)
        location = Location.query.filter(
            func.abs(Location.latitude - lat) < 0.0001,
            func.abs(Location.longitude - lng) < 0.0001
        ).first()
        if location:
            return location
    return None

@preferences_bp.route("/preferences", methods=["GET"])
def get_preferences():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.landing"))

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        return jsonify({
            'max_daily_hours': 8.0,
            'work_start_time': '09:00',
            'work_end_time': '17:00',
            'travel_mode': 'car',
            'prioritization_style': 'balanced',
            'home_address': None,
            'favorite_store_address': None
        })

    # Get location addresses if they exist
    home_location = Location.query.get(pref.home_location_id) if pref.home_location_id else None
    fav_store_location = Location.query.get(pref.favorite_store_location_id) if pref.favorite_store_location_id else None

    return jsonify({
        'max_daily_hours': pref.max_daily_hours,
        'work_start_time': pref.work_start_time.strftime('%H:%M') if pref.work_start_time else None,
        'work_end_time': pref.work_end_time.strftime('%H:%M') if pref.work_end_time else None,
        'travel_mode': pref.travel_mode,
        'prioritization_style': pref.prioritization_style,
        'home_address': home_location.address if home_location else None,
        'favorite_store_address': fav_store_location.address if fav_store_location else None
    })

@preferences_bp.route("/preferences", methods=["POST"])
def set_preferences():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.landing"))

    data = request.json or request.form
    if not data:
        return jsonify({"error": "Missing data"}), 400

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        pref = UserPreference(user_id=user_id)

    # Update basic preferences
    if 'max_daily_hours' in data:
        pref.max_daily_hours = float(data['max_daily_hours'])
    if 'work_start_time' in data:
        pref.work_start_time = parse_time_str(data['work_start_time'])
    if 'work_end_time' in data:
        pref.work_end_time = parse_time_str(data['work_end_time'])
    if 'travel_mode' in data:
        pref.travel_mode = data['travel_mode']
    if 'prioritization_style' in data:
        pref.prioritization_style = data['prioritization_style']

    # Handle locations
    home_addr = data.get("home_address")
    home_location = get_or_create_location(home_addr)
    if home_location:
        pref.home_location_id = home_location.location_id

    fav_addr = data.get("favorite_store_address")
    fav_location = get_or_create_location(fav_addr)
    if fav_location:
        pref.favorite_store_location_id = fav_location.location_id

    try:
        db.session.add(pref)
        db.session.commit()
        return jsonify({"message": "Preferences saved", "redirect": "/homepage"}), 200
    except IntegrityError as e:
        db.session.rollback()
        return jsonify({"error": "Failed to save preferences"}), 500
