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

    # 2. Remove ONLY scheduled instances of tasks that were modified or have flexible locations
    current_app.logger.info(f"[TASK_MUTATION] Preparing to update scheduled instances")
    
    try:
        # Query for flexible tasks - using explicit boolean comparison for database compatibility
        current_app.logger.info(f"[TASK_MUTATION] Querying for flexible tasks and tasks with null locations")
        # Use SQLAlchemy functions to ensure proper SQL generation
        from sqlalchemy import func, or_
        
        # First try to get recently modified tasks (last 10 seconds)
        from datetime import datetime, timedelta
        recent_time = datetime.now() - timedelta(seconds=10)
        
        # Get both flexible tasks AND recently modified tasks
        flexible_task_ids = db.session.query(RawTask.raw_task_id).filter(
            RawTask.user_id == user_id,
            or_(
                RawTask.is_location_flexible == 1,  # Using 1 for True
                RawTask.location_id.is_(None),      # Using is_(None) for NULL check
                RawTask.updated_at >= recent_time   # Recently modified tasks
            )
        ).all()
        
        flexible_task_ids = [task_id for (task_id,) in flexible_task_ids]
        current_app.logger.info(f"[TASK_MUTATION] Found {len(flexible_task_ids)} tasks to update: {flexible_task_ids}")
        
        if flexible_task_ids:
            try:
                # Instead of deleting everything, get a backup of the existing scheduled tasks
                existing_tasks = ScheduledTask.query.filter(
                    ScheduledTask.user_id == user_id,
                    ScheduledTask.raw_task_id.in_(flexible_task_ids)
                ).all()
                
                # Make a backup of the raw_task_ids and their scheduled tasks
                task_backup = {}
                for task in existing_tasks:
                    if task.raw_task_id not in task_backup:
                        task_backup[task.raw_task_id] = []
                    task_backup[task.raw_task_id].append(task)
                    
                current_app.logger.info(f"[TASK_MUTATION] Made backup of {len(existing_tasks)} scheduled tasks")
                
                # Now delete the scheduled tasks - but only for the flexible/modified tasks
                delete_count = ScheduledTask.query.filter(
                    ScheduledTask.user_id == user_id,
                    ScheduledTask.raw_task_id.in_(flexible_task_ids)
                ).delete(synchronize_session=False)
                
                # Store the backup in the application context for possible recovery
                if not hasattr(current_app, 'scheduled_task_backups'):
                    current_app.scheduled_task_backups = {}
                current_app.scheduled_task_backups[user_id] = task_backup
                
                current_app.logger.info(f"[TASK_MUTATION] Deleted {delete_count} scheduled instances of flexible/modified tasks")
            except Exception as delete_err:
                current_app.logger.error(f"[TASK_MUTATION] Error deleting scheduled tasks: {str(delete_err)}")
                import traceback
                current_app.logger.error(f"[TASK_MUTATION] Delete traceback: {traceback.format_exc()}")
        else:
            current_app.logger.info(f"[TASK_MUTATION] No tasks found to update")
    except Exception as query_err:
        current_app.logger.error(f"[TASK_MUTATION] Error querying flexible tasks: {str(query_err)}")
        import traceback
        current_app.logger.error(f"[TASK_MUTATION] Query traceback: {traceback.format_exc()}")

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