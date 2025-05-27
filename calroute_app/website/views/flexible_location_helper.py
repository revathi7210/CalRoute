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
        # 4. Upsert Location
        loc = (
            Location.query
            .filter_by(latitude=poi["lat"], longitude=poi["lng"])  # dedupe by coords
            .first()
        )
        if not loc:
            # POI results from Google Maps already have a nice place name field
            # but we'll still run it through our extractor for consistency
            place_name = poi["name"] if "name" in poi else extract_place_name(poi["address"])
            loc = Location(
                name=place_name,
                address=poi["address"],
                latitude=poi["lat"],
                longitude=poi["lng"]
            )
            db.session.add(loc)
            db.session.flush()
        # 5. Update task location but preserve flexibility
        old_location_id = task.location_id
        task.location_id = loc.location_id  # Fixed field name from id to location_id
        # Keep is_location_flexible as 1 to ensure tasks stay flexible
        current_app.logger.info(f"[FLEXIBLE_LOCATION] Updated task {task.raw_task_id} location from {old_location_id} to {loc.location_id} while preserving flexibility")

    db.session.commit()
    current_app.logger.info("[FLEXIBLE_LOCATION] Committed all location changes to database")