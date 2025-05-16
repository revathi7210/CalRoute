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

from flask import Blueprint, request, jsonify, session, redirect, url_for
from website.models import db, UserPreference, Location
from website.google_maps_helper import geocode_address

preferences_bp = Blueprint("preferences", __name__)

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

    def get_or_create_location(address):
        if not address:
            return None
        address = address.strip()
        lat, lng = geocode_address(address)
        if lat is None or lng is None:
            return None

        # Try by lat/lng uniqueness constraint
        location = Location.query.filter_by(latitude=lat, longitude=lng).first()
        if not location:
            location = Location(
                address=address,
                latitude=lat,
                longitude=lng,
            )
            db.session.add(location)
            db.session.flush()
        return location

    # Home location
    home_addr = data.get("home_address")
    home_location = get_or_create_location(home_addr)
    if home_location:
        pref.home_location_id = home_location.location_id

    # Favorite store location
    fav_addr = data.get("favorite_store_address")
    fav_location = get_or_create_location(fav_addr)
    if fav_location:
        pref.favorite_store_location_id = fav_location.location_id

    db.session.add(pref)
    db.session.commit()

    return jsonify({"message": "Preferences saved", "redirect": "/homepage"}), 200
