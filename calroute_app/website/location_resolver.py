import os
import logging
from sqlalchemy import or_
from flask import current_app
from .models import Location, UserPreference, RawTask, ScheduledTask, User, db
from .google_maps_helper import geocode_address
from .location_utils import extract_place_name
from website.views.flexible_location_helper import update_flexible_task_locations
from website.optimize_routes import run_optimization
from website.extensions import db

logger = logging.getLogger(__name__)

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
        name=extract_place_name(location_name),  # Extract just the place name
        address=location_name,
        latitude=lat,
        longitude=lng
    )
    db.session.add(new_location)
    db.session.flush()  # populate location_id without committing
    return new_location


def handle_task_mutation(user_id: int) -> None:
    """
    Central handler after any task create/edit/delete:
    1. Update flexible locations via Google Places
    2. Remove old scheduled instances for those tasks
    3. Re-run route optimization
    """
    current_app.logger.info(f"[TASK_MUTATION] Starting task mutation handler for user_id: {user_id}")
    # 1. Update RawTask locations
    current_app.logger.info(f"[TASK_MUTATION] Updating flexible task locations for user_id: {user_id}")
    update_flexible_task_locations(user_id)
    current_app.logger.info(f"[TASK_MUTATION] Finished updating flexible task locations")

    # 2. Remove scheduled instances of any tasks that were flexible
    current_app.logger.info(f"[TASK_MUTATION] Querying flexible tasks to delete scheduled instances")
    # Query for flexible tasks - using == 1 instead of True for database compatibility
    current_app.logger.info(f"[TASK_MUTATION] Querying for is_location_flexible=1 (True in database)")
    flexible_task_ids = db.session.query(RawTask.raw_task_id).filter(
        RawTask.user_id == user_id,
        or_(
            RawTask.is_location_flexible == 1,  # Using 1 instead of True
            RawTask.location_id == None
        )
    ).all()
    
    flexible_task_ids = [task_id for (task_id,) in flexible_task_ids]
    current_app.logger.info(f"[TASK_MUTATION] Found {len(flexible_task_ids)} flexible tasks: {flexible_task_ids}")
    
    if flexible_task_ids:
        delete_count = ScheduledTask.query.filter(
            ScheduledTask.user_id == user_id,
            ScheduledTask.raw_task_id.in_(flexible_task_ids)
        ).delete(synchronize_session=False)
        current_app.logger.info(f"[TASK_MUTATION] Deleted {delete_count} scheduled instances of flexible tasks")
    else:
        current_app.logger.info(f"[TASK_MUTATION] No flexible tasks found to delete")

    # 3. Re-generate the schedule
    current_app.logger.info(f"[TASK_MUTATION] Starting schedule regeneration")
    user = User.query.get(user_id)
    if user:
        current_app.logger.info(f"[TASK_MUTATION] Found user {user.user_id}, running optimization")
        try:
            result = run_optimization(user)
            current_app.logger.info(f"[TASK_MUTATION] Optimization completed with result: {result}")
        except Exception as e:
            current_app.logger.error(f"[TASK_MUTATION] Error during optimization: {str(e)}")
            import traceback
            current_app.logger.error(f"[TASK_MUTATION] Traceback: {traceback.format_exc()}")
    else:
        current_app.logger.error(f"[TASK_MUTATION] User not found with ID: {user_id}")
    db.session.commit()
    current_app.logger.info(f"[TASK_MUTATION] Task mutation process completed for user_id: {user_id}")