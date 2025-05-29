# website/google_maps_helper.py

import googlemaps
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)
def find_nearest_location(api_key, query, user_lat, user_lng, radius=5000):
    """
    Uses Google Places API to find the nearest location based on a query and user's lat/lng.
    Returns a dictionary with location details or None if not found.
    """
    try:
        gmaps = googlemaps.Client(key=api_key)
        places_result = gmaps.places_nearby(
            location=(user_lat, user_lng),
            radius=radius,
            keyword=query
        )

        results = places_result.get('results', [])
        if not results:
            return None

        # Take the top result
        place = results[0]
        return {
            'address': place.get('vicinity'),
            'lat': place['geometry']['location']['lat'],
            'lng': place['geometry']['location']['lng'],
            'place_id': place.get('place_id')
        }
    except Exception as e:
        logger.error(f"Google Maps API error: {e}")
        return None

def find_nearest_poi(query: str, center: tuple[float, float], radius: int = 5000) -> dict | None:
    """
    Find the nearest Place matching `query` around a center point.
    Returns a dict with name, address, lat, lng, or None if no result.
    """
    logger.info(f"[GOOGLE_MAPS] Finding nearest {query} around center {center} with radius {radius}m")
    try:
        # Get API key from config
        api_key = current_app.config.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            logger.error("[GOOGLE_MAPS] API key is not set in app config")
            return None
            
        # Initialize client
        logger.info(f"[GOOGLE_MAPS] Initializing Google Maps client")
        gmaps = googlemaps.Client(key=api_key)
        
        # Make the API request
        logger.info(f"[GOOGLE_MAPS] Sending places_nearby request for {query}")
        results = gmaps.places_nearby(
            location=center,
            radius=radius,
            keyword=query,
            type="point_of_interest"
        )
        logger.info(f"[GOOGLE_MAPS] Received response with {len(results.get('results', []))} results")
        candidates = results.get("results", [])
        if not candidates:
            logger.warning(f"[GOOGLE_MAPS] No results found for {query}")
            return None
            
        top = candidates[0]
        result = {
            "name": top.get("name"),
            "address": top.get("vicinity") or top.get("name"),
            "lat": top["geometry"]["location"]["lat"],
            "lng": top["geometry"]["location"]["lng"],
        }
        
        logger.info(f"[GOOGLE_MAPS] Found top match: {result['name']} at {result['lat']},{result['lng']}")
        return result
    except Exception as e:
        logger.error(f"[GOOGLE_MAPS] Google Places API error: {e}")
        import traceback
        logger.error(f"[GOOGLE_MAPS] Traceback: {traceback.format_exc()}")
        return None

def geocode_address(address):
    """
    Uses Google Geocoding API to get lat/lng for an address string.
    Returns (lat, lng) or (None, None) if not found.
    """
    api_key = current_app.config.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.error("Google Maps API key is not set in app config.")
        return None, None

    params = {
        "address": address,
        "key": api_key
    }

    try:
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params)
        response.raise_for_status()
        data = response.json()

        logger.info(f"GEOCODE REQUEST → {address}")
        logger.info(f"PARAMS: {params}")
        logger.info(f"GEOCODE RESPONSE STATUS: {data.get('status')}")

        if data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            logger.info(f"→ lat,lng: {loc['lat']}, {loc['lng']}")
            return loc["lat"], loc["lng"]

        logger.warning("→ no results")
        return None, None

    except Exception as e:
        logger.error(f"Google Geocoding API error: {e}")
        return None, None