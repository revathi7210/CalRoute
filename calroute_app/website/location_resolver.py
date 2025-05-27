import os
from .models import Location, UserPreference, db
from .google_maps_helper import geocode_address

def resolve_location_for_task(user, location_name, task_text):
    """
    Intelligent location resolver:
    1. If location_name is empty or 'none', return None
    2. Try resolving coordinates from name using geocoding
    3. If coordinates exist in DB → return location
    4. Else → insert and return new location
    """
    if not location_name or location_name.lower() in ['none', '']:
        return None

    # Step 1: Try to resolve lat/lng from Google Maps
    lat, lng = geocode_address(location_name)
    if lat is None or lng is None:
        return None

    # Step 2: Check if location with same lat/lng already exists
    location = Location.query.filter_by(latitude=lat, longitude=lng).first()
    if location:
        return location

    # Step 3: Add new location
    new_location = Location(
        address=location_name,
        latitude=lat,
        longitude=lng
    )
    db.session.add(new_location)
    db.session.flush()  # populate location_id without committing
    return new_location