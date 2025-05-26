from flask import Blueprint, jsonify, session, current_app, request
from website.extensions import db
from website.models import User, ScheduledTask, Location, RawTask
from website.views.calendar import fetch_google_calendar_events
from website.views.todoist import parse_and_store_tasks
from datetime import datetime
from sqlalchemy import and_, or_
import traceback
from website.google_maps_helper import geocode_address
from website.optimize_routes import run_optimization
from website.views.flexible_location_helper import update_flexible_task_locations
from website.location_resolver import handle_task_mutation

tasks_bp = Blueprint('tasks', __name__)

@tasks_bp.route("/api/tasks", methods=["GET"])
def get_scheduled_tasks():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    #  If no scheduled tasks, run the full pipeline
    if ScheduledTask.query.filter_by(user_id=user_id).count() == 0:
        try:
            fetch_google_calendar_events(user)
            parse_and_store_tasks(user)
            run_optimization(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Task scheduling failed: {e}")
            return jsonify({"error": "Task scheduling failed"}), 500

    #  Get scheduled tasks + join with location
    results = (
        db.session.query(ScheduledTask, Location)
        .join(Location, ScheduledTask.location_id == Location.location_id)
        .filter(ScheduledTask.user_id == user_id)
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )
    print(results)

    tasks = []
    for sched_task, location in results:
        if not location:
            continue
        tasks.append({
            "id": sched_task.sched_task_id,
            "raw_task_id": sched_task.raw_task_id if hasattr(sched_task, 'raw_task_id') else None,
            "title": sched_task.title,
            "start_time": sched_task.scheduled_start_time.strftime("%-I:%M %p"),
            "end_time": sched_task.scheduled_end_time.strftime("%-I:%M %p"),
            "lat": location.latitude,
            "lng": location.longitude,
            "location_name": getattr(location, 'name', None),
            "location_address": getattr(location, 'address', None),
            "description": getattr(sched_task, 'description', ''),
            "priority": getattr(sched_task, 'priority', 1),
            "transit_mode": getattr(sched_task, 'transit_mode', None),
            "is_completed": getattr(sched_task, 'is_completed', False),
        })

    return jsonify({"tasks": tasks})

@tasks_bp.route("/api/tasks", methods=["POST"])
def create_task():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or not data.get("title"):
        return jsonify({"error": "Title is required"}), 400

    # Handle location creation if location string is provided
    location_id = None
    if data.get("location_name") or data.get("location_address"):
        location_str = data.get("location_name") or data.get("location_address")
        # Get coordinates using geocoding
        lat, lng = geocode_address(location_str)
        if lat is None or lng is None:
            return jsonify({"error": "Could not geocode the provided location"}), 400

        # Check if location already exists
        location = Location.query.filter_by(latitude=lat, longitude=lng).first()
        if not location:
            location = Location(
                address=location_str,
                latitude=lat,
                longitude=lng
            )
            db.session.add(location)
            db.session.flush()  # This will get us the location_id without committing
        location_id = location.location_id

    # Create a new raw task
    raw_task = RawTask(
        user_id=user_id,
        source="manual",
        external_id=f"manual-{datetime.now().timestamp()}",
        title=data.get("title"),
        description=data.get("description"),
        start_time=datetime.fromisoformat(data.get("start_time")) if data.get("start_time") else None,
        end_time=datetime.fromisoformat(data.get("end_time")) if data.get("end_time") else None,
        due_date=datetime.fromisoformat(data.get("due_date")) if data.get("due_date") else None,
        priority=data.get("priority", 3),
        duration=data.get("duration", 45),  # Default 45 minutes if not specified
        status=data.get("status", "not_completed"),  # Default status if not specified
        location_id=location_id,  # Add the location_id to the raw task
        is_location_flexible=False,  # Add is_location_flexible flag
        place_type=None  # Add place type if provided
    )

    try:
        db.session.add(raw_task)
        db.session.commit()
        current_app.logger.info(f"Created new task: {raw_task.raw_task_id} - {raw_task.title}")
        
        try:
            # Run the optimization pipeline
            current_app.logger.info(f"Starting optimization for user {user_id} after task creation")
            handle_task_mutation(user_id)
            current_app.logger.info("Optimization completed successfully")
            
            # Return updated scheduled tasks
            return get_scheduled_tasks()
        except Exception as opt_error:
            # If optimization fails, we still created the task successfully
            # Log the error but return success to the user
            current_app.logger.error(f"Optimization error: {str(opt_error)}")
            current_app.logger.error(f"Optimization stack trace: {traceback.format_exc()}")
            
            return jsonify({
                "message": "Task created successfully, but schedule optimization failed", 
                "task_id": raw_task.raw_task_id,
                "title": raw_task.title,
                "optimization_error": str(opt_error)
            })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating task: {str(e)}")
        current_app.logger.error(f"Stack trace: {traceback.format_exc()}") 
        return jsonify({"error": str(e)}), 500


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    task = RawTask.query.filter_by(raw_task_id=task_id, user_id=user_id).first()
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Update task fields
    if "title" in data:
        task.title = data["title"]
    if "description" in data:
        task.description = data["description"]
    if "start_time" in data:
        task.start_time = datetime.fromisoformat(data["start_time"]) if data["start_time"] else None
    if "end_time" in data:
        task.end_time = datetime.fromisoformat(data["end_time"]) if data["end_time"] else None
    if "due_date" in data:
        task.due_date = datetime.fromisoformat(data["due_date"]) if data["due_date"] else None
    if "priority" in data:
        task.priority = data["priority"]
    if "duration" in data:
        task.duration = data["duration"]
    if "status" in data:
        task.status = data["status"]

    # Handle location update
    if data.get("location_name") or data.get("location_address"):
        location_str = data.get("location_name") or data.get("location_address")
        lat, lng = geocode_address(location_str)
        if lat is None or lng is None:
            return jsonify({"error": "Could not geocode the provided location"}), 400
        location = Location.query.filter_by(latitude=lat, longitude=lng).first()
        if not location:
            location = Location(
                address=location_str,
                latitude=lat,
                longitude=lng
            )
            db.session.add(location)
            db.session.flush()
        task.location_id = location.location_id

    try:
        db.session.commit()
        current_app.logger.info(f"Updated task: {task.raw_task_id} - {task.title}")
        
        try:
            # Run the optimization pipeline
            current_app.logger.info(f"Starting optimization for user {user_id} after task update")
            handle_task_mutation(user_id)
            current_app.logger.info("Optimization completed successfully")
            
            # Return updated scheduled tasks
            return get_scheduled_tasks()
        except Exception as opt_error:
            # If optimization fails, we still updated the task successfully
            # Log the error but return success to the user
            current_app.logger.error(f"Optimization error: {str(opt_error)}")
            current_app.logger.error(f"Optimization stack trace: {traceback.format_exc()}")
            
            return jsonify({
                "message": "Task updated successfully, but schedule optimization failed", 
                "task_id": task.raw_task_id,
                "title": task.title,
                "optimization_error": str(opt_error)
            })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating task: {str(e)}")
        current_app.logger.error(f"Stack trace: {traceback.format_exc()}") 
        return jsonify({"error": str(e)}), 500

@tasks_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    user_id = session.get("user_id")
    if not user_id:
        current_app.logger.error(f"Unauthorized: No user_id in session")
        return jsonify({"error": "Unauthorized"}), 401

    current_app.logger.info(f"Starting delete for task ID: {task_id}, user_id: {user_id}")
    
    # Check if any task with this ID exists, regardless of user
    current_app.logger.info(f"[DELETE] Checking for raw task with ID {task_id} in database")
    any_task = RawTask.query.filter_by(raw_task_id=task_id).first()
    if not any_task:
        current_app.logger.error(f"Task with ID {task_id} does not exist in database")
        return jsonify({"error": "Task not found in database"}), 404
    
    # Check if task belongs to current user
    current_app.logger.info(f"[DELETE] Task found, checking if it belongs to user {user_id}")
    task = RawTask.query.filter_by(raw_task_id=task_id, user_id=user_id).first()
    if not task:
        current_app.logger.error(f"Task exists but belongs to user {any_task.user_id}, not {user_id}")
        return jsonify({"error": "Task not found for current user"}), 404

    current_app.logger.info(f"[DELETE] Found task with ID: {task_id} for deletion")
    current_app.logger.info(f"[DELETE] Task to delete: Title='{task.title}', ID={task.raw_task_id}, User={task.user_id}")
    
    # Verify the task ID matches what was requested
    if task.raw_task_id != task_id:
        current_app.logger.error(f"[DELETE] ERROR: Task ID mismatch! Requested {task_id} but got {task.raw_task_id}")
        return jsonify({"error": "Task ID mismatch"}), 500

    # Delete both raw task and scheduled task
    try:
        # Get task details for verification one more time before deletion
        task_to_delete = RawTask.query.get(task_id)
        current_app.logger.info(f"[DELETE] Final verification - Task to delete: ID={task_to_delete.raw_task_id}, Title='{task_to_delete.title}'")
        
        # First, directly delete any scheduled tasks linked to this raw task ID
        # using a direct SQL-like query instead of ORM to avoid relationship issues
        current_app.logger.info(f"[DELETE] Deleting scheduled tasks for raw_task_id: {task_id}")
        
        # Get scheduled tasks first for logging
        scheduled_tasks = ScheduledTask.query.filter_by(raw_task_id=task_id).all()
        for st in scheduled_tasks:
            current_app.logger.info(f"[DELETE] Will delete scheduled task: ID={st.sched_task_id}, Title='{st.title}'")
        
        # Now delete them
        deleted_count = ScheduledTask.query.filter_by(raw_task_id=task_id).delete(synchronize_session=False)
        current_app.logger.info(f"[DELETE] Deleted {deleted_count} scheduled tasks")
        
        # Also delete any scheduled tasks that might be linked to this task through other means
        # (defensive deletion to clean up any potential orphaned tasks)
        deleted_extra = ScheduledTask.query.filter_by(user_id=user_id, title=task.title).delete(synchronize_session=False)
        if deleted_extra > 0:
            current_app.logger.info(f"[DELETE] Deleted {deleted_extra} additional scheduled tasks with matching title")
        
        # Then delete raw task - explicitly using the ID to ensure we're deleting the right task
        current_app.logger.info(f"[DELETE] Deleting raw task with ID={task_id}, Title='{task.title}'")
        RawTask.query.filter_by(raw_task_id=task_id).delete(synchronize_session=False)
        current_app.logger.info(f"[DELETE] Raw task {task_id} deleted successfully")
        db.session.commit()
        
        # Re-run optimization to update the schedule
        try:
            current_app.logger.info(f"Starting optimization for user {user_id} after task deletion")
            handle_task_mutation(user_id)
            current_app.logger.info("Optimization completed successfully")
        except Exception as opt_error:
            # If optimization fails, we still deleted the task successfully
            # Log the error but continue
            current_app.logger.error(f"Optimization error after deletion: {str(opt_error)}")
            current_app.logger.error(f"Optimization stack trace: {traceback.format_exc()}")
        
        return jsonify({"message": "Task deleted successfully"})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting task: {str(e)}")
        current_app.logger.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@tasks_bp.route("/api/pending_tasks", methods=["GET"])
def get_pending_tasks():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    current_time = datetime.now()
    
    # Get all tasks that were scheduled before current time and are not completed
    pending_tasks = (
        db.session.query(ScheduledTask, Location)
        .join(Location, ScheduledTask.location_id == Location.location_id)
        .join(RawTask, ScheduledTask.raw_task_id == RawTask.raw_task_id)
        .filter(
            and_(
                ScheduledTask.user_id == user_id,
                ScheduledTask.scheduled_end_time < current_time,
                RawTask.status != "completed"
            )
        )
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )

    tasks = []
    for sched_task, location in pending_tasks:
        if not location:
            continue
        tasks.append({
            "id": sched_task.sched_task_id,
            "raw_task_id": sched_task.raw_task_id,
            "title": sched_task.title,
            "start_time": sched_task.scheduled_start_time.strftime("%-I:%M %p"),
            "end_time": sched_task.scheduled_end_time.strftime("%-I:%M %p"),
            "lat": location.latitude,
            "lng": location.longitude,
        })

    return jsonify({"tasks": tasks})

@tasks_bp.route("/api/complete_tasks", methods=["POST"])
def complete_tasks():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or not isinstance(data.get("task_ids"), list):
        return jsonify({"error": "task_ids array is required"}), 400

    task_ids = data["task_ids"]
    
    try:
        # First, get all tasks that will be completed
        tasks_to_complete = (
            db.session.query(ScheduledTask, RawTask)
            .join(RawTask, ScheduledTask.raw_task_id == RawTask.raw_task_id)
            .filter(
                and_(
                    ScheduledTask.sched_task_id.in_(task_ids),
                    ScheduledTask.user_id == user_id
                )
            )
            .all()
        )

        # Store the completed tasks' information
        completed_tasks_info = []
        for sched_task, raw_task in tasks_to_complete:
            completed_tasks_info.append({
                'sched_task_id': sched_task.sched_task_id,
                'raw_task_id': sched_task.raw_task_id,
                'title': sched_task.title,
                'description': sched_task.description,
                'location_id': sched_task.location_id,
                'scheduled_start_time': sched_task.scheduled_start_time,
                'scheduled_end_time': sched_task.scheduled_end_time,
                'priority': sched_task.priority,
                'travel_eta_minutes': sched_task.travel_eta_minutes
            })
            raw_task.status = "completed"

        db.session.commit()

        # Get user and run optimization for remaining tasks
        user = User.query.get(user_id)
        handle_task_mutation(user_id)

        # After optimization, restore the completed tasks
        for task_info in completed_tasks_info:
            # Check if the task still exists (it might have been deleted during optimization)
            existing_task = ScheduledTask.query.get(task_info['sched_task_id'])
            if not existing_task:
                # If task was deleted, recreate it with the same information
                new_task = ScheduledTask(
                    sched_task_id=task_info['sched_task_id'],
                    user_id=user_id,
                    raw_task_id=task_info['raw_task_id'],
                    title=task_info['title'],
                    description=task_info['description'],
                    location_id=task_info['location_id'],
                    scheduled_start_time=task_info['scheduled_start_time'],
                    scheduled_end_time=task_info['scheduled_end_time'],
                    priority=task_info['priority'],
                    travel_eta_minutes=task_info['travel_eta_minutes']
                )
                db.session.add(new_task)

        db.session.commit()

        # Return success message - frontend will handle fetching updated tasks
        return jsonify({"message": "Tasks completed successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to complete tasks: {e}")
        return jsonify({"error": "Failed to complete tasks"}), 500