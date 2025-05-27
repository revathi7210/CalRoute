from sqlalchemy import or_
from flask import current_app
from website.extensions import db
from website.models import RawTask, Location
from website.google_maps_helper import find_nearest_poi
from website.location_utils import extract_place_name

def update_flexible_task_locations(user_id: int) -> None:
    """
    For tasks with flexible or missing locations, find nearby POIs using google maps,
    update RawTask.location_id and mark them fixed.
    """
    current_app.logger.info(f"[FLEXIBLE_LOCATION] Starting update for user_id: {user_id}")
    # 1. Gather fixed-location coords
    fixed_coords = (
        db.session.query(Location.latitude, Location.longitude)
        .join(RawTask, RawTask.location_id == Location.location_id)
        .filter(RawTask.user_id == user_id, RawTask.is_location_flexible == 0)  # Using 0 instead of False
        .all()
    )
    current_app.logger.info(f"[FLEXIBLE_LOCATION] Found {len(fixed_coords)} fixed locations")
    
    if not fixed_coords:
        current_app.logger.info("[FLEXIBLE_LOCATION] No fixed locations found, skipping flexible location update")
        return

    # compute centroid
    avg_lat = sum(lat for lat, _ in fixed_coords) / len(fixed_coords)
    avg_lng = sum(lng for _, lng in fixed_coords) / len(fixed_coords)
    center = (avg_lat, avg_lng)
    current_app.logger.info(f"[FLEXIBLE_LOCATION] Computed center point: {center}")

    # 2. Query flexible or missing location tasks
    flexible_tasks = RawTask.query.filter(
        RawTask.user_id == user_id,
        or_(RawTask.is_location_flexible == 1, RawTask.location_id == None)  # Using 1 instead of True
    ).all()
    current_app.logger.info(f"[FLEXIBLE_LOCATION] Found {len(flexible_tasks)} flexible/missing location tasks")
    for task in flexible_tasks:
        current_app.logger.info(f"[FLEXIBLE_LOCATION] Flexible task: id={task.raw_task_id}, title={task.title}, place_type={task.place_type}")

    for task in flexible_tasks:
        # 3. Use place_type for POI search
        if not task.place_type:
            current_app.logger.info(f"[FLEXIBLE_LOCATION] Task {task.raw_task_id} has no place_type, skipping")
            continue
            
        current_app.logger.info(f"[FLEXIBLE_LOCATION] Finding POI for task {task.raw_task_id} with place_type: {task.place_type}")
        poi = find_nearest_poi(task.place_type, center)
        
        if not poi:
            current_app.logger.info(f"[FLEXIBLE_LOCATION] No POI found for {task.place_type}")
            continue
            
        current_app.logger.info(f"[FLEXIBLE_LOCATION] Found POI: {poi}")
        # 4. Upsert Location - First try to find by address
        poi_address = poi.get("address", "")
        poi_name = poi.get("name", "")
        
        try:
            # First check for existing location by address (case insensitive)
            loc = None
            if poi_address:
                # Use ilike for case-insensitive matching if database supports it
                # Filter with SQL LIKE to find similar addresses
                from sqlalchemy import func
                existing_locations = Location.query.filter(
                    func.lower(Location.address).contains(func.lower(poi_address.split(',')[0]))
                ).all()
                
                if existing_locations:
                    current_app.logger.info(f"[FLEXIBLE_LOCATION] Found {len(existing_locations)} potential matching locations by address")
                    # Pick the one with closest coordinates
                    closest_loc = None
                    min_dist = float('inf')
                    for l in existing_locations:
                        dist = ((l.latitude - poi["lat"])**2 + (l.longitude - poi["lng"])**2)**0.5
                        if dist < min_dist:
                            min_dist = dist
                            closest_loc = l
                    
                    # If distance is small enough, use the existing location
                    if min_dist < 0.0005:  # Approximately 50-100 meters depending on latitude
                        loc = closest_loc
                        current_app.logger.info(f"[FLEXIBLE_LOCATION] Using existing location '{loc.name}' with distance {min_dist}")
            
            # If no match by address, try by exact coordinates
            if not loc:
                loc = Location.query.filter_by(latitude=poi["lat"], longitude=poi["lng"]).first()
                if loc:
                    current_app.logger.info(f"[FLEXIBLE_LOCATION] Found exact coordinate match for location: {loc.name}")
            
            # If still no match, create a new location
            if not loc:
                # POI results from Google Maps already have a nice place name field
                # but we'll still run it through our extractor for consistency
                place_name = poi_name if poi_name else extract_place_name(poi_address)
                loc = Location(
                    name=place_name,
                    address=poi_address,
                    latitude=poi["lat"],
                    longitude=poi["lng"]
                )
                db.session.add(loc)
                db.session.flush()
                current_app.logger.info(f"[FLEXIBLE_LOCATION] Created new location: {place_name}")
        except Exception as loc_err:
            current_app.logger.error(f"[FLEXIBLE_LOCATION] Error finding/creating location: {str(loc_err)}")
            import traceback
            current_app.logger.error(f"[FLEXIBLE_LOCATION] Location error traceback: {traceback.format_exc()}")
            continue  # Skip this task if location handling fails
        # 5. Update task location but preserve flexibility
        old_location_id = task.location_id
        task.location_id = loc.location_id  # Fixed field name from id to location_id
        # Keep is_location_flexible as 1 to ensure tasks stay flexible
        current_app.logger.info(f"[FLEXIBLE_LOCATION] Updated task {task.raw_task_id} location from {old_location_id} to {loc.location_id} while preserving flexibility")

    db.session.commit()
    current_app.logger.info("[FLEXIBLE_LOCATION] Committed all location changes to database")