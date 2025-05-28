import requests


def extract_place_name(address_string: str) -> str:
    """
    Given a full address string, use OpenStreetMap's Nominatim search API
    to retrieve the official place name. Falls back to raw address if no result.

    Steps:
    1. Query Nominatim "search" endpoint with the address as a free-form query.
    2. Take the first result's `display_name`, split on commas, return the first segment.
    3. If the request fails or returns no results, return the original address.
    """
    if not address_string:
        return ""

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address_string,
        "format": "jsonv2",
        "limit": 1
    }
    headers = {"User-Agent": "CalRoute/1.0"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        results = resp.json()
        if results:
            display = results[0].get("display_name")
            if display:
                # Return the portion before the first comma
                return display.split(',')[0].strip()
    except Exception:
        pass

    # Fallback: return the raw address
    return address_string.strip()
