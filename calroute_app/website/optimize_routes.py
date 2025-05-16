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
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    success = run_optimization(user)
    if not success:
        return jsonify({"error": "Could not generate optimized route"}), 500

    return jsonify({"success": True, "message": "Schedule optimized."})

# --- Dynamic Schedule (re-optimization with current location) ---
@optimize_bp.route("/api/dynamic_schedule", methods=["POST"])
def dynamic_schedule():
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


def run_optimization(user, current_lat=None, current_lng=None):
    from .maps_utils import build_distance_matrix
    from .views.calendar import fetch_google_calendar_events
    from .views.todoist import parse_and_store_tasks

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    fetch_google_calendar_events(user)
    parse_and_store_tasks(user)
    tasks_with_locations = (
        db.session.query(RawTask, Location)
        .join(Location, RawTask.location_id == Location.location_id)
        .filter(
            RawTask.user_id == user.user_id,
            Location.user_id == user.user_id
        )
        .order_by(RawTask.start_time)
        .all()
    )

    if not tasks_with_locations:
        print("No tasks with locations found.")
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

    locations = []
    durations = []
    time_windows = []

    # 1) current-location first
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

    # 2) then each task
    for task, loc in tasks_with_locations:
        locations.append(loc.address)

        # 1) Determine service duration
        if task.start_time and task.end_time:
            dur = int((task.end_time - task.start_time).total_seconds() // 60)
        else:
            # No fixed start: default to 30 minutes
            dur = 30
        durations.append(dur)

        # 2) Build time window
        if task.start_time:
            start_min = task.start_time.hour * 60 + task.start_time.minute
        else:
            start_min = 0

        if task.end_time:
            end_min = task.end_time.hour * 60 + task.end_time.minute
        else:
            end_min = 24 * 60  # whole day

        time_windows.append((start_min, end_min))

    # for task, loc in tasks_with_locations:
    #     locations.append(loc.address)

    #     # duration in minutes (or a default)
    #     if task.start_time and task.end_time:
    #         delta = task.end_time - task.start_time
    #         dur = int(delta.total_seconds() // 60)
    #     else:
    #         dur = 30
    #     durations.append(dur)

    #     # time window
    #     start_min = (task.start_time.hour * 60 + task.start_time.minute) if task.start_time else 0
    #     end_min = (task.end_time.hour * 60 + task.end_time.minute) if task.end_time else 1440
    #     time_windows.append((start_min, end_min))

    
    locations.append(home_address)
    durations.append(0)
    time_windows.append((0, 1440))

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

    #start_index = 0
    #end_index = n-1
    #route = solve_tsp(distance_matrix, task_durations=durations, time_windows=time_windows, start_index=start_index, end_index=end_index)
    route, start_times = solve_tsp(distance_matrix, task_durations=durations, time_windows=time_windows)
    for i, start_time in enumerate(start_times):
        print(f"{i}: {start_time}")
    ## UPDATE THIS TO NOT DELETE
    ScheduledTask.query.filter_by(user_id=user.user_id).delete()
    db.session.commit()

    print("tasksloc")
    print(tasks_with_locations)
    today = datetime.now().date()
    for idx in route:
        # skip start and end depots
        if idx == 0 or idx == n-1:
            continue

        task_idx = idx - 1
        raw, _ = tasks_with_locations[task_idx]
        dur = durations[idx]

        # priority 1 → use original calendar times
        if raw.priority == 1 and raw.start_time and raw.end_time:
            st = raw.start_time
            et = raw.end_time
        else:
            # solver time in minutes since midnight
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