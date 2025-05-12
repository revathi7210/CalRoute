# File: optimize_routes.py

from flask import Blueprint, session, request, jsonify
from datetime import datetime, timedelta, time, date
from .extensions import db
from .models import RawTask, ScheduledTask, Location, User
from .maps_utils import build_distance_matrix, solve_tsp

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

# # --- Core Optimization Logic ---
# def run_optimization(user, current_lat=None, current_lng=None):
#     now = datetime.now()
#
#     raw_query = (
#         db.session.query(RawTask, Location)
#         .join(Location, RawTask.location_id == Location.location_id)
#         .filter(RawTask.user_id == user.user_id)
#         .filter(Location.user_id == user.user_id)
#     )
#
#     tasks_with_locations = [
#         (task, loc)
#         for (task, loc) in raw_query.all()
#         if not task.end_time or task.end_time >= now
#     ]
#
#     if not tasks_with_locations:
#         print("No tasks with valid upcoming times found.")
#         return False
#
#     # Use home as end node (from preference table later)
#     home_address = "Verano Place, Irvine, CA"
#     locations = []
#     durations = []
#     time_windows = []
#
#     if current_lat and current_lng:
#         locations.append({"lat": current_lat, "lng": current_lng, "title": "Current Location"})
#         durations.append(0)
#         time_windows.append((now.hour * 60 + now.minute, 1440))
#
#     for task, loc in tasks_with_locations:
#         if ScheduledTask.query.filter_by(raw_task_id=task.raw_task_id, status="completed").first():
#             continue  # skip completed tasks
#         locations.append(loc.address)
#         duration = int((task.end_time - task.start_time).total_seconds() // 60) if task.start_time and task.end_time else 30
#         durations.append(duration)
#
#         if task.priority == 1 and task.start_time and task.end_time:
#             start = task.start_time.hour * 60 + task.start_time.minute
#             end = task.end_time.hour * 60 + task.end_time.minute
#         else:
#             start, end = 0, 1440  # flexible if not strict priority 1
#         time_windows.append((start, end))
#
#     locations.append(home_address)
#     durations.append(0)
#     time_windows.append((0, 1440))
#
#     # Get distance matrix
#     address_list = [f"{loc['lat']},{loc['lng']}" if isinstance(loc, dict) else loc for loc in locations]
#     distance_matrix = build_distance_matrix(address_list)
#
#     route = solve_tsp(distance_matrix, durations, time_windows)
#     if not route:
#         return False
#
#     ScheduledTask.query.filter_by(user_id=user.user_id, status="pending").delete()
#     db.session.commit()
#
#     current_time = now
#     for idx in route:
#         if idx >= len(tasks_with_locations) + (1 if current_lat else 0):
#             continue  # skip home
#
#         offset = 1 if current_lat else 0
#         task_idx = idx - offset
#         if task_idx < 0:
#             continue  # current location
#         raw, _ = tasks_with_locations[task_idx]
#
#         dur = timedelta(minutes=int(durations[idx]))
#         sched = ScheduledTask(
#             user_id=user.user_id,
#             raw_task_id=raw.raw_task_id,
#             title=raw.title,
#             description=raw.description,
#             location_id=raw.location_id,
#             scheduled_start_time=current_time,
#             scheduled_end_time=current_time + dur,
#             status="pending",
#             priority=raw.priority,
#             travel_eta_minutes=0
#         )
#         db.session.add(sched)
#         current_time += dur
#
#     db.session.commit()
#     print("✅ Schedule stored.")
#     return True

import requests

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
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

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

    home_address = "Verano Place, Irvine, CA"
    #home_already_present = any("verano place" in loc.address.lower() for _, loc in tasks_with_locations)
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

        # duration in minutes (or a default)
        if task.start_time and task.end_time:
            delta = task.end_time - task.start_time
            dur = int(delta.total_seconds() // 60)
        else:
            dur = 30
        durations.append(dur)

        # time window
        start_min = (task.start_time.hour * 60 + task.start_time.minute) if task.start_time else 0
        end_min = (task.end_time.hour * 60 + task.end_time.minute) if task.end_time else 1440
        time_windows.append((start_min, end_min))

    
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
    route = solve_tsp(distance_matrix, task_durations=durations, time_windows=time_windows)
    ScheduledTask.query.filter_by(user_id=user.user_id).delete()
    db.session.commit()

    print("tasksloc")
    print(tasks_with_locations)
    current_time = datetime.combine(datetime.now().date(), time(8, 0))
    for idx in route:
        # if idx >= len(tasks_with_locations):
        #     print(f"Skipping index {idx} (home location)")
        #     continue

        # raw, _ = tasks_with_locations[idx]
        # dur = timedelta(minutes=int(durations[idx]))

        # skip both the start‐depot (0) and end‐depot (n-1)
        if idx == 0 or idx == n-1:
            continue

        # map the OR-Tools node back to your tasks_with_locations
        task_idx = idx - 1

        raw, _ = tasks_with_locations[task_idx]

        dur = timedelta(minutes=int(durations[idx]))

        sched = ScheduledTask(
            user_id=user.user_id,
            raw_task_id=raw.raw_task_id,
            title=raw.title,
            description=raw.description,
            location_id=raw.location_id,
            scheduled_start_time=current_time,
            scheduled_end_time=current_time + dur,
            status="pending",
            priority=raw.priority,
            travel_eta_minutes=0
        )
        db.session.add(sched)
        current_time += dur

    db.session.commit()
    print("✅ Schedule optimized.")
    return True

# optimize_routes.py

# def run_optimization(user, current_lat=None, current_lng=None):
#     # 1) Fetch raw tasks + locations
#     tasks = (
#         db.session.query(RawTask, Location)
#           .join(Location, RawTask.location_id == Location.location_id)
#           .filter(RawTask.user_id == user.user_id,
#                   Location.user_id == user.user_id)
#           .order_by(RawTask.start_time)
#           .all()
#     )
#     if not tasks:
#         print("No tasks to schedule.")
#         return False

#     print("\n🔍 Raw tasks:")
#     for i, (raw, loc) in enumerate(tasks):
#         print(f"  {i}: '{raw.title}' @ {loc.address} "
#               f"(prio={raw.priority}) "
#               f"{raw.start_time}–{raw.end_time}")

#     # 2) Build lists: depot → tasks → depot
#     now = datetime.now()
#     locs, durs, tws = [], [], []

#     # 2a) Starting depot
#     if current_lat and current_lng:
#         depot_addr = reverse_geocode_osm(current_lat, current_lng)
#         tw0 = (now.hour*60 + now.minute, 1440)
#     else:
#         depot_addr = "Verano Place, Irvine, CA"
#         tw0 = (0, 1440)
#     locs.append(depot_addr);  durs.append(0); tws.append(tw0)

#     # 2b) Each task
#     for raw, loc in tasks:
#         locs.append(loc.address)
#         if raw.start_time and raw.end_time:
#             service = int((raw.end_time - raw.start_time).total_seconds()//60)
#         else:
#             service = 30
#         durs.append(service)
#         if raw.priority == 1 and raw.start_time and raw.end_time:
#             start_min = raw.start_time.hour*60 + raw.start_time.minute
#             end_min   = raw.end_time.hour*60   + raw.end_time.minute
#         else:
#             start_min, end_min = 0, 1440
#         tws.append((start_min, end_min))

#     # 2c) Ending depot
#     locs.append(depot_addr); durs.append(0); tws.append((0,1440))

#     # 3) Debug prints
#     print("\n⏱ Service durations:")
#     for i, dd in enumerate(durs):
#         print(f"  {i}: {dd}min")
#     print("\n⏰ Time windows:")
#     for i, (s,e) in enumerate(tws):
#         print(f"  {i}: [{s},{e}]")

#     # 4) Build & solve
#     matrix = build_distance_matrix(locs)
#     route, mgr, routing, sol = solve_tsp(
#         matrix,
#         task_durations=durs,
#         time_windows=tws,
#         start_index=0,
#         end_index=len(locs)-1
#     )
#     if route is None:
#         print("❌ No feasible route found.")
#         return False

#     # 5) Print solver‐computed arrival times
#     time_dim = routing.GetDimensionOrDie("Time")
#     print("\n⏲️ Solver arrival times (minutes since midnight):")
#     for node in route:
#         idx = mgr.NodeToIndex(node)
#         arr = sol.Value(time_dim.CumulVar(idx))
#         print(f"  node {node}: arrives at {arr}min")

#     # 6) Delete only uncompleted
#     ScheduledTask.query.filter_by(user_id=user.user_id)\
#                        .filter(ScheduledTask.status!="completed")\
#                        .delete()
#     db.session.commit()

#     # 7) Write schedule back
#     today = date.today()
#     for node in route:
#         if node==0 or node==len(locs)-1:
#             continue
#         raw, _ = tasks[node-1]
#         svc   = durs[node]
#         idx   = mgr.NodeToIndex(node)
#         arr   = sol.Value(time_dim.CumulVar(idx))
#         start_dt = datetime.combine(today, datetime.min.time()) + timedelta(minutes=arr)
#         end_dt   = start_dt + timedelta(minutes=svc)

#         # override for strict‐priority
#         if raw.priority==1 and raw.start_time and raw.end_time:
#             start_dt, end_dt = raw.start_time, raw.end_time

#         print(f"  → scheduling '{raw.title}': {start_dt} → {end_dt}")
#         db.session.add(ScheduledTask(
#             user_id=user.user_id,
#             raw_task_id=raw.raw_task_id,
#             title=raw.title,
#             description=raw.description,
#             location_id=raw.location_id,
#             scheduled_start_time=start_dt,
#             scheduled_end_time=end_dt,
#             status="pending",
#             priority=raw.priority,
#             travel_eta_minutes=0
#         ))

#     db.session.commit()
#     print("✅ Schedule written to DB.")
#     return True