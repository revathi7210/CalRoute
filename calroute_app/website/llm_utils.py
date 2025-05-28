import google.generativeai as genai
from flask import current_app, session
from .models import UserPreference, Location, user_favorite_stores
import googlemaps
from website.extensions import db


# Valid Google Places types
VALID_GOOGLE_PLACE_TYPES = {
    "supermarket", "movie_theater", "gym", "pharmacy", "cafe",
    "clothing_store", "bank", "restaurant", "library", "book_store",
    "post_office", "hospital", "doctor", "dentist", "park"
}

# Mapping Gemini outputs to valid Google types
PLACE_TYPE_MAPPING = {
    "grocery_store": "supermarket",
    "movie_theater": "movie_theater",
    "coffee_shop": "cafe"
    # Add more if needed
}

def get_user_home_address():
    user_id = session.get("user_id")
    if not user_id:
        return None

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref or not pref.home_location_id:
        return None

    loc = Location.query.get(pref.home_location_id)
    return loc.address if loc else None

def call_gemini_for_place_type(task_text, home_address):
    api_key = current_app.config.get('GOOGLE_GENAI_API_KEY')
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash-latest")

    response = model.generate_content(
        f"""
You are a helpful assistant for a personal productivity app. Your job is to assign a generic **place type** to user tasks.

Task: "{task_text}"

Rules:
1. Return a real-world **place type** like "movie_theater", "grocery_store", "pharmacy", "gym", "cafe", etc.
2. Do NOT return business names or locations.
3. Do NOT say "unknown", "location", "place", or similar.
4. Use lowercase and underscores if needed.
5. for watch movie suggest "movie_theater"

Reply with only the place type.
"""
    )
            
    return response.text.strip().lower().replace(" ", "_")

def call_gemini_for_specific_business(task_text, home_address):
    api_key = current_app.config.get('GOOGLE_GENAI_API_KEY')
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash-latest")

    response = model.generate_content(
        f"""
You are a helpful assistant for a personal productivity app. Your job is to assign a real-world business or location to user tasks.

home address: {home_address}
task: "{task_text}"

Rules:
1. Suggest a real business name near the user's city that would help accomplish this task.
2. Avoid general terms like "store" or "place" — give an actual business name.
3. Prefer well-known local businesses in or near {home_address}.

Reply with just the business name.
"""
    )

    return response.text.strip()

def get_nearest_location_from_maps(home_address, task_text):
    maps_api_key = current_app.config.get('GOOGLE_MAPS_API_KEY')
    gmaps = googlemaps.Client(key=maps_api_key)

    # Step 1: Get general place type from LLM
    place_type = call_gemini_for_place_type(task_text, home_address)
    current_app.logger.info(f"Place type from Gemini: {place_type}")
    mapped_type = PLACE_TYPE_MAPPING.get(place_type, place_type)
    current_app.logger.info(f"Mapped place type: {mapped_type}")

    if mapped_type in VALID_GOOGLE_PLACE_TYPES:
        geocode_result = gmaps.geocode(home_address)
        if not geocode_result or len(geocode_result) == 0:
            current_app.logger.warning(f"No geocode results found for home address: {home_address}")
            return None
            
        try:
            latlng = geocode_result[0]['geometry']['location']
        except (IndexError, KeyError) as e:
            current_app.logger.error(f"Error accessing geocode result: {str(e)}")
            return None

        places_result = gmaps.places_nearby(
            location=latlng,
            radius=5000,
            type=mapped_type
        )

        results = places_result.get("results", [])
        if results:
            return results[0]["name"]
        else:
            current_app.logger.info("No places found for:", mapped_type)
            return None
    else:
        # Step 2: Fallback — Ask Gemini for a specific business name
        return call_gemini_for_specific_business(task_text, home_address)

def get_user_preferred_locations(user_id: int) -> dict:
    """
    Returns the user’s preferred gym address and a list of favorite grocery store addresses.
    """
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        return {'gym': None, 'grocery_stores': []}

    result = {'gym': None, 'grocery_stores': []}

    # Get gym location
    if pref.gym_location_id:
        gym_loc = Location.query.get(pref.gym_location_id)
        if gym_loc:
            result['gym'] = {
                'location_id': gym_loc.location_id,
                'address': gym_loc.address,
                'latitude': gym_loc.latitude,
                'longitude': gym_loc.longitude
            }

    # Get favorite grocery stores
    favorite_stores = db.session.query(Location).join(
        user_favorite_stores,
        Location.location_id == user_favorite_stores.c.location_id
    ).filter(
        user_favorite_stores.c.pref_id == pref.pref_id
    ).all()

    result['grocery_stores'] = [{
        'location_id': store.location_id,
        'address': store.address,
        'latitude': store.latitude,
        'longitude': store.longitude
    } for store in favorite_stores]

    return result


