from flask import Blueprint, jsonify, session, current_app, request
from website.extensions import db
from website.models import User, ScheduledTask, Location, RawTask
from website.views.calendar import fetch_google_calendar_events
from website.views.todoist import parse_and_store_tasks
from website.optimize_routes import run_optimization
from datetime import datetime, timezone
from sqlalchemy import and_
from website.google_maps_helper import geocode_address

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
            
            # Capture detailed optimization error
            try:
                success = run_optimization(user)
                if not success:
                    current_app.logger.error("Optimization returned False - no feasible solution found")
                    return jsonify({"error": "Could not find a feasible schedule"}), 500
            except Exception as optim_err:
                current_app.logger.error(f"Detailed optimization error: {str(optim_err)}")
                import traceback
                current_app.logger.error(f"Optimization traceback: {traceback.format_exc()}")
                return jsonify({"error": f"Optimization failed: {str(optim_err)}"}), 500
                
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Task scheduling failed: {e}")
            return jsonify({"error": f"Task scheduling failed: {str(e)}"}), 500

    #  Get scheduled tasks + join with location and raw task
    results = (
        db.session.query(ScheduledTask, Location, RawTask)
        .join(Location, ScheduledTask.location_id == Location.location_id)
        .join(RawTask, ScheduledTask.raw_task_id == RawTask.raw_task_id)
        .filter(ScheduledTask.user_id == user_id)
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )

    tasks = []
    for sched_task, location, raw_task in results:
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
            "is_completed": raw_task.status == "completed",
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
        
        # If lat/lng are provided directly, use them
        if data.get("lat") is not None and data.get("lng") is not None:
            lat = data["lat"]
            lng = data["lng"]
        else:
            # Try geocoding if API key is available
            lat, lng = geocode_address(location_str)
            if lat is None or lng is None:
                return jsonify({"error": "Could not geocode the provided location and no coordinates provided"}), 400

        # Round coordinates to 3 decimal places (about 100m precision)
        lat = round(lat, 3)
        lng = round(lng, 3)

        # First try to find location by exact address match
        location = Location.query.filter_by(address=location_str).first()
        
        # If no exact address match, try finding by coordinates
        if not location:
            location = Location.query.filter(
                db.func.abs(Location.latitude - lat) < 0.001,
                db.func.abs(Location.longitude - lng) < 0.001
            ).first()
        
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
    try:
        # Parse and validate times
        start_time = None
        end_time = None
        if data.get("start_time"):
            start_time = datetime.fromisoformat(data["start_time"])
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            # Ensure start time is today (UTC)
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today.replace(day=today.day + 1)
            if not (today <= start_time < tomorrow):
                return jsonify({"error": "Start time must be for today"}), 400
            
            # Ensure start time is in the future
            now = datetime.now(timezone.utc)
            if start_time.hour * 60 + start_time.minute <= now.hour * 60 + now.minute and start_time.date() == now.date():
                return jsonify({"error": "Start time must be after current time"}), 400

        if data.get("end_time"):
            end_time = datetime.fromisoformat(data["end_time"])
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            # Ensure end time is today (UTC)
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today.replace(day=today.day + 1)
            if not (today <= end_time < tomorrow):
                return jsonify({"error": "End time must be for today"}), 400
            
            # Ensure end time is after start time
            if start_time and (end_time.hour * 60 + end_time.minute <= start_time.hour * 60 + start_time.minute):
                return jsonify({"error": "End time must be after start time"}), 400

        # Log the times for debugging
        current_app.logger.info(f"Creating task with times: current={datetime.now(timezone.utc)}, start={start_time}, end={end_time}")

        raw_task = RawTask(
            user_id=user_id,
            source="manual",
            external_id=f"manual-{datetime.now().timestamp()}",
            title=data.get("title"),
            description=data.get("description", ""),
            start_time=start_time,
            end_time=end_time,
            due_date=start_time,  # Use start_time as due_date
            priority=data.get("priority", 3),
            duration=data.get("duration", 45),
            status="not_completed",
            location_id=location_id
        )

        db.session.add(raw_task)
        db.session.commit()
        
        # Run optimization to schedule the new task
        user = User.query.get(user_id)
        run_optimization(user)
        
        # Get the newly created scheduled task
        scheduled_task = ScheduledTask.query.filter_by(raw_task_id=raw_task.raw_task_id).first()
        if not scheduled_task:
            return jsonify({"error": "Task created but not scheduled"}), 500

        # Return the newly created task with all its details
        return jsonify({
            "id": scheduled_task.sched_task_id,
            "raw_task_id": raw_task.raw_task_id,
            "title": raw_task.title,
            "start_time": scheduled_task.scheduled_start_time.strftime("%-I:%M %p"),
            "end_time": scheduled_task.scheduled_end_time.strftime("%-I:%M %p"),
            "lat": location.latitude if location else None,
            "lng": location.longitude if location else None,
            "location_name": location.address if location else None,
            "location_address": location.address if location else None,
            "description": raw_task.description,
            "priority": raw_task.priority,
            "transit_mode": "car",
            "is_completed": False
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@tasks_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Get user first
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Start a transaction
        db.session.begin_nested()

        # Get the task with its location relationship
        task = RawTask.query.filter_by(raw_task_id=task_id, user_id=user_id).first()
        if not task:
            db.session.rollback()
            return jsonify({'error': 'Task not found'}), 404

        # Update basic task fields
        if 'title' in data:
            task.title = data['title']
        if 'description' in data:
            task.description = data['description']
        if 'priority' in data:
            task.priority = data['priority']
        if 'start_time' in data:
            task.start_time = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
        if 'end_time' in data:
            task.end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
        if 'duration' in data:
            task.duration = data['duration']

        # Handle location update if provided
        if any(key in data for key in ['location_name', 'location_address', 'lat', 'lng']):
            location_name = data.get('location_name') or data.get('location_address')
            lat = data.get('lat')
            lng = data.get('lng')

            if lat is not None and lng is not None:
                # Round coordinates to 3 decimal places (about 100m precision)
                lat = round(float(lat), 3)
                lng = round(float(lng), 3)
            elif location_name:
                # Try to geocode the location
                geocode_result = geocode_address(location_name)
                if not geocode_result:
                    db.session.rollback()
                    return jsonify({'error': 'Could not geocode the provided location'}), 400
                lat, lng = geocode_result
                lat = round(lat, 3)
                lng = round(lng, 3)
            else:
                db.session.rollback()
                return jsonify({'error': 'Either coordinates or location name must be provided'}), 400

            # Search for existing location by coordinates
            existing_location = Location.query.filter(
                db.func.abs(Location.latitude - lat) < 0.001,
                db.func.abs(Location.longitude - lng) < 0.001
            ).first()

            if existing_location:
                # Update existing location
                existing_location.address = location_name
                existing_location.latitude = lat
                existing_location.longitude = lng
                location = existing_location
            else:
                # Create new location
                location = Location(
                    address=location_name,
                    latitude=lat,
                    longitude=lng
                )
                db.session.add(location)
                db.session.flush()  # Get the location ID

            # Update task's location
            task.location_id = location.location_id

        # Commit the initial changes
        db.session.commit()

        # Run optimization to update the schedule
        try:
            # Delete all scheduled tasks for this user to ensure clean slate
            ScheduledTask.query.filter_by(user_id=user_id).delete()
            db.session.commit()

            # Run optimization to create new scheduled tasks for all tasks
            run_optimization(user)
            db.session.commit()

            # Verify that scheduled tasks were created
            scheduled_tasks = ScheduledTask.query.filter_by(user_id=user_id).all()
            if not scheduled_tasks:
                current_app.logger.warning(f"No scheduled tasks created after optimization for user {user_id}")
        except Exception as e:
            current_app.logger.error(f"Error updating schedule: {str(e)}")
            # Don't rollback the task update if optimization fails
            pass

        # Fetch the updated task with its location relationship
        updated_task = (
            RawTask.query
            .filter_by(raw_task_id=task_id)
            .join(Location, RawTask.location_id == Location.location_id)
            .first()
        )
        if not updated_task:
            return jsonify({'error': 'Failed to fetch updated task'}), 500

        # Get the location data
        location_data = None
        if updated_task.location_id:
            location = Location.query.get(updated_task.location_id)
            if location:
                location_data = {
                    'address': location.address,
                    'latitude': location.latitude,
                    'longitude': location.longitude
                }

        # Get the scheduled task data
        scheduled_task = ScheduledTask.query.filter_by(raw_task_id=updated_task.raw_task_id).first()
        scheduled_data = None
        if scheduled_task:
            scheduled_data = {
                'scheduled_start_time': scheduled_task.scheduled_start_time.strftime("%-I:%M %p") if scheduled_task.scheduled_start_time else None,
                'scheduled_end_time': scheduled_task.scheduled_end_time.strftime("%-I:%M %p") if scheduled_task.scheduled_end_time else None
            }

        return jsonify({
            'message': 'Task updated successfully',
            'task': {
                'id': updated_task.raw_task_id,
                'title': updated_task.title,
                'description': updated_task.description,
                'location': location_data['address'] if location_data else None,
                'lat': location_data['latitude'] if location_data else None,
                'lng': location_data['longitude'] if location_data else None,
                'priority': updated_task.priority,
                'start_time': scheduled_data['scheduled_start_time'] if scheduled_data else None,
                'end_time': scheduled_data['scheduled_end_time'] if scheduled_data else None,
                'duration': updated_task.duration,
                'raw_task_id': updated_task.raw_task_id
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@tasks_bp.route("/api/tasks/<int:raw_task_id>", methods=["DELETE"])
def delete_task(raw_task_id):
    try:
        user_id = session.get("user_id")
        if not user_id:
            current_app.logger.error("Delete task failed: Unauthorized - no user_id in session")
            return jsonify({"error": "Unauthorized"}), 401

        # Log the attempt to delete
        current_app.logger.info(f"Attempting to delete raw task {raw_task_id} for user {user_id}")

        # First check if the task exists and belongs to the user
        task = RawTask.query.filter_by(raw_task_id=raw_task_id, user_id=user_id).first()
        if not task:
            current_app.logger.warning(f"Delete task failed: Raw task {raw_task_id} not found for user {user_id}")
            return jsonify({"error": "Task not found"}), 404

        # Also delete any associated scheduled task
        scheduled_task = ScheduledTask.query.filter_by(raw_task_id=raw_task_id).first()
        if scheduled_task:
            current_app.logger.info(f"Deleting associated scheduled task {scheduled_task.sched_task_id}")
            db.session.delete(scheduled_task)

        # Delete the raw task
        current_app.logger.info(f"Deleting raw task {raw_task_id}")
        db.session.delete(task)
        db.session.commit()
        
        # Re-run optimization to update the schedule
        user = User.query.get(user_id)
        if not user:
            current_app.logger.error(f"Delete task failed: User {user_id} not found after deletion")
            return jsonify({"error": "User not found"}), 404

        current_app.logger.info(f"Running optimization after task deletion")
        run_optimization(user)
        db.session.commit()
        
        current_app.logger.info(f"Successfully deleted raw task {raw_task_id} and updated schedule")
        return jsonify({"message": "Task deleted successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete task failed with error: {str(e)}")
        return jsonify({"error": f"Failed to delete task: {str(e)}"}), 500

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
        # Get and update raw tasks
        raw_tasks = RawTask.query.filter(
            and_(
                RawTask.raw_task_id.in_(task_ids),
                RawTask.user_id == user_id
            )
        ).all()

        for raw_task in raw_tasks:
            raw_task.status = "completed"

        db.session.commit()

        # Get user and run optimization for remaining tasks
        user = User.query.get(user_id)
        run_optimization(user)

        return jsonify({"message": "Tasks completed successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to complete tasks: {e}")
        return jsonify({"error": "Failed to complete tasks"}), 500

@tasks_bp.route("/api/complete_task/<int:task_id>", methods=["POST"])
def complete_single_task(task_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Get and update the single raw task
        raw_task = RawTask.query.filter(
            and_(
                RawTask.raw_task_id == task_id,
                RawTask.user_id == user_id
            )
        ).first()

        if not raw_task:
            return jsonify({"error": "Task not found"}), 404

        raw_task.status = "completed"
        db.session.commit()

        # Get user and run optimization for remaining tasks
        user = User.query.get(user_id)
        run_optimization(user)

        return jsonify({
            "message": "Task completed successfully",
            "task_id": task_id,
            "status": "completed"
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to complete task: {e}")
        return jsonify({"error": "Failed to complete task"}), 500

@tasks_bp.route("/api/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task_completion(task_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Get the scheduled task
        sched_task = ScheduledTask.query.filter(
            and_(
                ScheduledTask.sched_task_id == task_id,
                ScheduledTask.user_id == user_id
            )
        ).first()

        if not sched_task:
            return jsonify({"error": "Scheduled task not found"}), 404

        # Get the corresponding raw task
        raw_task = RawTask.query.filter(
            and_(
                RawTask.raw_task_id == sched_task.raw_task_id,
                RawTask.user_id == user_id
            )
        ).first()

        if not raw_task:
            return jsonify({"error": "Task not found"}), 404

        # Toggle status
        if raw_task.status == "completed":
            raw_task.status = "not_completed"
        else:
            raw_task.status = "completed"
        db.session.commit()

        # Get user and run optimization for remaining tasks
        user = User.query.get(user_id)
        run_optimization(user)

        return jsonify({
            "message": "Task status changed successfully",
            "task_id": task_id,
            "raw_task_id": raw_task.raw_task_id,
            "status": raw_task.status
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to toggle task status: {e}")
        return jsonify({"error": "Failed to toggle task status"}), 500
