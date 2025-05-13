# location_resolver.py

from .models import Location, UserPreference, db

def resolve_location_for_task(user, location_name, task_text):
    """
    Intelligent location resolver:
    1. If location_name is empty or 'none', return None
    2. Check if location exists in Locations table for this user
    3. Check user preference locations (home, favorite store)
    4. Otherwise return None
    """
    if not location_name or location_name.lower() in ['none', '']:
        return None

    # 1️⃣ Check direct match in Locations table
    location = Location.query.filter_by(user_id=user.user_id, name=location_name).first()
    if location:
        return location

    # 2️⃣ Check user preference locations
    user_pref = UserPreference.query.filter_by(user_id=user.user_id).first()
    if user_pref:
        possible_locations = []
        if user_pref.home_location_id:
            possible_locations.append(Location.query.get(user_pref.home_location_id))
        if user_pref.favorite_store_location_id:
            possible_locations.append(Location.query.get(user_pref.favorite_store_location_id))

        for loc in possible_locations:
            if loc and loc.name.lower() == location_name.lower():
                return loc

    # 3️⃣ No match found
    return None
