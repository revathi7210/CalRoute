from flask import Blueprint, request, jsonify, session, redirect, url_for
from website.extensions import db
from website.models import User, UserPreference, Location, TransitModeOption
from website.google_maps_helper import geocode_address
from datetime import datetime, time
from sqlalchemy import func
from website.location_utils import extract_place_name

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
            name=extract_place_name(address),
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
            'favorite_stores': [],
            'transit_modes': []
        })

    # Get location addresses if they exist
    home_location = Location.query.get(pref.home_location_id) if pref.home_location_id else None
    gym_location = Location.query.get(pref.gym_location_id) if pref.gym_location_id else None
    
    # Get favorite store addresses from the many-to-many relationship
    favorite_stores = [store.address for store in pref.favorite_store_locations]
    
    # Get transit modes from the many-to-many relationship
    transit_modes = [mode.mode for mode in pref.transit_modes]

    return jsonify({
        'max_daily_hours': pref.max_daily_hours,
        'work_start_time': pref.work_start_time.strftime('%H:%M') if pref.work_start_time else None,
        'work_end_time': pref.work_end_time.strftime('%H:%M') if pref.work_end_time else None,
        'travel_mode': pref.travel_mode if hasattr(pref, 'travel_mode') else 'car',
        'prioritization_style': pref.prioritization_style,
        'home_address': home_location.address if home_location else None,
        'gym_address': gym_location.address if gym_location else None,
        'favorite_stores': favorite_stores,
        'transit_modes': transit_modes
    })

@preferences_bp.route("/preferences", methods=["POST"])
def set_preferences():
    print("\n--- PREFERENCES ROUTE HIT ---")
    user_id = session.get("user_id")
    if not user_id:
        print("ERROR: User not in session.")
        return jsonify({"error": "Unauthorized"}), 401
    
    print(f"Authenticated user ID: {user_id}")

    data = request.get_json(silent=True)
    if not data:
        print("ERROR: No JSON data received.")
        return jsonify({"error": "Missing data"}), 400

    print("Received data from frontend:")
    print(data) # This is the most important print!

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        print("No existing preference found, creating a new one.")
        pref = UserPreference(user_id=user_id)
        db.session.add(pref)
    else:
        print("Found existing preference object.")

    # --- Update Basic Preferences ---
    pref.max_daily_hours = float(data.get('max_daily_hours', pref.max_daily_hours or 8.0))
    pref.work_start_time = parse_time_str(data.get('work_start_time')) or pref.work_start_time
    pref.work_end_time = parse_time_str(data.get('work_end_time')) or pref.work_end_time
    pref.prioritization_style = data.get('prioritization_style', pref.prioritization_style or 'balanced')
    print(f"Style set to: {pref.prioritization_style}")

    # --- Update Transit Modes (Many-to-Many) ---
    transit_mode_names = data.get('transit_modes', [])
    print(f"Received transit modes: {transit_mode_names}")
    
    pref.transit_modes.clear()
    if transit_mode_names:
        mode_objects = TransitModeOption.query.filter(TransitModeOption.mode.in_(transit_mode_names)).all()
        print(f"Found mode objects in DB: {[m.mode for m in mode_objects]}")
        for mode_obj in mode_objects:
            pref.transit_modes.add(mode_obj)

    # --- Update Locations (Single and Many-to-Many) ---
    # Home Location
    if home_addr := data.get("home_address"):
        print(f"Processing home address: {home_addr}")
        if home_location := get_or_create_location(home_addr):
            pref.home_location = home_location
            print(f"Set home location to ID: {home_location.location_id}")

    # Gym Location
    if gym_addr := data.get("gym_address"):
        print(f"Processing gym address: {gym_addr}")
        if gym_location := get_or_create_location(gym_addr):
            pref.gym_location = gym_location
            print(f"Set gym location to ID: {gym_location.location_id}")

    # Favorite Grocery Stores
    fav_store_addresses = data.get('favorite_stores', [])
    print(f"Processing favorite stores: {fav_store_addresses}")
    
    pref.favorite_store_locations.clear()
    if fav_store_addresses:
        for store_addr in fav_store_addresses:
            if store_location := get_or_create_location(store_addr):
                pref.favorite_store_locations.append(store_location)
                print(f"Appended store location ID: {store_location.location_id}")

    try:
        print("Attempting to commit to database...")
        db.session.commit()
        print("✅ COMMIT SUCCESSFUL!")
        return jsonify({"message": "Preferences saved successfully!"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"❌ DATABASE ERROR: {e}")
        return jsonify({"error": "An unexpected error occurred during save."}), 500