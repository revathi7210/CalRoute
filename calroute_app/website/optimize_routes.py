# File: optimize_routes.py

from flask import Blueprint, session, request, jsonify
from datetime import datetime, timedelta, time, date
from .extensions import db
from .models import RawTask, ScheduledTask, Location, User, UserPreference
from .maps_utils import build_distance_matrix, solve_tsp
import requests

optimize_bp = Blueprint("optimize", __name__)

# --- Morning Schedule (default from home) ---
@optimize_bp.route("/api/optimize_schedule", methods=["POST"])
def optimize_schedule():
    """Initial schedule optimization with fresh data sync"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    success = run_optimization(user, sync_mode=True)
    if not success:
        return jsonify({"error": "Could not generate optimized route"}), 500

    return jsonify({"success": True, "message": "Schedule optimized."})

# --- Dynamic Schedule (re-optimization with current location) ---
@optimize_bp.route("/api/dynamic_schedule", methods=["POST"])
def dynamic_schedule():
    """Re-optimize schedule based on current location"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    lat = request.json.get("lat")
    lng = request.json.get("lng")
    print(lat)
    print(lng)

    if lat is None or lng is None:
        return jsonify({"error": "Current location required"}), 400

    success = run_optimization(user, current_lat=lat, current_lng=lng)
    if not success:
        return jsonify({"error": "Could not generate updated route"}), 500

    return jsonify({"success": True, "message": "Route re-optimized from current location."})

@optimize_bp.route("/api/reoptimize", methods=["POST"])
def reoptimize_schedule():
    """Re-optimize schedule after task edits"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    success = run_optimization(user)
    if not success:
        return jsonify({"error": "Could not re-optimize schedule"}), 500

    return jsonify({"success": True, "message": "Schedule re-optimized."})

@optimize_bp.route("/api/check-pending-tasks", methods=["GET"])
def check_pending_tasks():
    """Check for tasks that need completion status"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    # Get tasks scheduled for today that are not completed
    today = datetime.now().date()
    pending_tasks = (
        ScheduledTask.query
        .filter(
            ScheduledTask.user_id == user_id,
            ScheduledTask.scheduled_start_time >= today,
            ScheduledTask.scheduled_start_time < today + timedelta(days=1),
            ScheduledTask.status == "pending"
        )
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )

    tasks_data = [{
        "scheduled_task_id": task.scheduled_task_id,
        "title": task.title,
        "scheduled_start_time": task.scheduled_start_time.isoformat(),
        "scheduled_end_time": task.scheduled_end_time.isoformat()
    } for task in pending_tasks]

    return jsonify({
        "has_pending_tasks": len(pending_tasks) > 0,
        "tasks": tasks_data
    })

@optimize_bp.route("/api/update-task-completion", methods=["POST"])
def update_task_completion():
    """Update completion status of tasks"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "completed_tasks" not in data:
        return jsonify({"error": "No completion data provided"}), 400

    completed_task_ids = data["completed_tasks"]
    
    # Update scheduled tasks
    for task_id in completed_task_ids:
        scheduled_task = ScheduledTask.query.get(task_id)
        if scheduled_task and scheduled_task.user_id == user_id:
            scheduled_task.status = "completed"
            
            # Update corresponding raw task
            raw_task = RawTask.query.get(scheduled_task.raw_task_id)
            if raw_task:
                raw_task.status = "completed"

    db.session.commit()

    # Reoptimize remaining tasks
    user = User.query.get(user_id)
    success = run_optimization(user)

    return jsonify({
        "success": True,
        "message": "Task completion status updated and schedule reoptimized",
        "reoptimization_success": success
    })

def reverse_geocode_osm(lat: float, lng: float) -> str | None:
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lng,
        "format": "jsonv2",
    }
    resp = requests.get(url, params=params, headers={"User-Agent": "CalRoute/1.0"}).json()
    return resp.get("display_name")


# → "Google Building 41, Amphitheatre Parkway, Mountain View, Santa Clara County, California, 94043, United States"


def run_optimization(user, current_lat=None, current_lng=None, sync_mode=False):
    """
    Run optimization for tasks based on different scenarios.
    
    Args:
        user: User object
        current_lat: Current latitude (optional)
        current_lng: Current longitude (optional)
        sync_mode: If True, fetches fresh data from Todoist and Calendar
    """
    from .maps_utils import build_distance_matrix
    from .views.calendar import fetch_google_calendar_events
    from .views.todoist import parse_and_store_tasks

    # If in sync mode, fetch fresh data from external sources
    if sync_mode:
        fetch_google_calendar_events(user)
        parse_and_store_tasks(user)
        db.session.commit()

    # Get tasks with locations
    tasks_with_locations = (
        db.session.query(RawTask, Location)
        .outerjoin(Location, RawTask.location_id == Location.location_id)
        .filter(
            RawTask.user_id == user.user_id,
            RawTask.status == 'not_completed'  # Only optimize incomplete tasks
        )
        .order_by(RawTask.start_time)
        .all()
    )

    if not tasks_with_locations:
        print("No tasks found.")
        return False

    pref = UserPreference.query.filter_by(user_id=user.user_id).first()
    if not pref or not pref.home_location_id:
        print("No home_location_id set in user preferences.")
        return False
    home_loc = Location.query.get(pref.home_location_id)
    if not home_loc:
        print(f"Home location not found (id={pref.home_location_id}).")
        return False

    home_address = home_loc.address
    print(home_address)
    now = datetime.now()

    # Prepare optimization data
    locations = []
    durations = []
    time_windows = []
    task_indices = []  # Keep track of which tasks we're including

    # Add starting location (current location or home)
    if current_lat and current_lng:
        current_loc = reverse_geocode_osm(current_lat, current_lng)
        print("current_loc:", current_loc)
        locations.append(current_loc)
        durations.append(0)
        time_windows.append((now.hour * 60 + now.minute, 1440))
    else:
        locations.append(home_address)
        durations.append(0)
        time_windows.append((0, 1440))

    # Add task locations
    for i, (task, loc) in enumerate(tasks_with_locations):
        # Skip tasks that have fixed times (like calendar events)
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            continue

        # For tasks without location, use home address
        if not loc:
            locations.append(home_address)
        else:
            locations.append(loc.address)
        
        # Use task duration if specified, otherwise use default
        duration = task.duration if task.duration else 45
        durations.append(duration)

        # Build time window based on task constraints
        if task.start_time:
            start_min = task.start_time.hour * 60 + task.start_time.minute
        else:
            start_min = 0

        if task.end_time:
            end_min = task.end_time.hour * 60 + task.end_time.minute
        else:
            end_min = 24 * 60  # whole day

        time_windows.append((start_min, end_min))
        task_indices.append(i)

    # Add home location as end point
    locations.append(home_address)
    durations.append(0)
    time_windows.append((0, 1440))

    # Build and solve
    distance_matrix = build_distance_matrix(locations)
    n = len(distance_matrix)

    print("\n📍 Locations:")
    for i, addr in enumerate(locations):
        print(f"{i}: {addr}")
    
    for i, dur in enumerate(durations):
        print(f"{i}: {dur}")
    
    for i, time_window in enumerate(time_windows):
        print(f"{i}: {time_window}")

    print("\n📏 Distance Matrix (minutes):")
    for i, row in enumerate(distance_matrix):
        row_str = " | ".join(f"{val:>5}" for val in row)
        print(f"{i}: {row_str}")
    print("\n")

    route, start_times = solve_tsp(distance_matrix, task_durations=durations, time_windows=time_windows)
    for i, start_time in enumerate(start_times):
        print(f"{i}: {start_time}")

    # Clear existing scheduled tasks
    ScheduledTask.query.filter_by(user_id=user.user_id).delete()
    db.session.commit()

    print("tasksloc")
    print(tasks_with_locations)
    today = datetime.now().date()

    # First, schedule fixed-time tasks (like calendar events)
    for task, _ in tasks_with_locations:
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            sched = ScheduledTask(
                user_id=user.user_id,
                raw_task_id=task.raw_task_id,
                title=task.title,
                description=task.description,
                location_id=task.location_id,
                scheduled_start_time=task.start_time,
                scheduled_end_time=task.end_time,
                status="pending",
                priority=task.priority,
                travel_eta_minutes=0
            )
            db.session.add(sched)

    # Then schedule flexible tasks
    for idx in route:
        # Skip start and end depots
        if idx == 0 or idx == len(locations)-1:
            continue

        task_idx = task_indices[idx - 1]  # Adjust index to account for skipped tasks
        raw, _ = tasks_with_locations[task_idx]
        dur = durations[idx]

        # Use solver's optimized times
        mins = start_times[idx]
        st = datetime.combine(today, time(mins // 60, mins % 60))
        et = st + timedelta(minutes=dur)

        sched = ScheduledTask(
            user_id=user.user_id,
            raw_task_id=raw.raw_task_id,
            title=raw.title,
            description=raw.description,
            location_id=raw.location_id,
            scheduled_start_time=st,
            scheduled_end_time=et,
            status="pending",
            priority=raw.priority,
            travel_eta_minutes=0
        )
        db.session.add(sched)

    db.session.commit()
    print("✅ Schedule optimized.")
    return True