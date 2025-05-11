from flask import Blueprint, session, jsonify
from datetime import datetime, timedelta, time
from .extensions import db
from .models import RawTask, ScheduledTask, Location, User
from .maps_utils import build_distance_matrix
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

optimize_bp = Blueprint("optimize", __name__)

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


def run_optimization(user):
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
    home_already_present = any("verano place" in loc.address.lower() for _, loc in tasks_with_locations)

    locations = [loc.address for _, loc in tasks_with_locations]
    durations = [
        int((task.end_time - task.start_time).total_seconds() // 60)
        if task.end_time and task.start_time else 30
        for task, _ in tasks_with_locations
    ]
    time_windows = [
        (
            task.start_time.hour * 60 + task.start_time.minute if task.start_time else 0,
            task.end_time.hour * 60 + task.end_time.minute if task.end_time else 1440
        )
        for task, _ in tasks_with_locations
    ]

    if not home_already_present:
        locations.append(home_address)
        durations.append(0)
        time_windows.append((0, 1440))

    distance_matrix = build_distance_matrix(locations)
    n = len(distance_matrix)

    print("\n📍 Locations:")
    for i, addr in enumerate(locations):
        print(f"{i}: {addr}")

    print("\n📏 Distance Matrix (minutes):")
    for i, row in enumerate(distance_matrix):
        row_str = " | ".join(f"{val:>5}" for val in row)
        print(f"{i}: {row_str}")
    print("\n")

    start_index = 0
    end_index = n - 1 if not home_already_present else 0
    manager = pywrapcp.RoutingIndexManager(n, 1, [start_index], [end_index])
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index, to_index):
        return distance_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    routing.AddDimension(
        transit_index,
        120,
        1440,
        False,
        "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")

    for i in range(n):
        idx = manager.NodeToIndex(i)
        time_dim.SlackVar(idx).SetValue(int(durations[i]))
        start, end = time_windows[i]
        print(f"[{i}] Duration: {durations[i]}, TimeWindow: ({start}, {end})")
        time_dim.CumulVar(idx).SetRange(start, end)

    for i in range(1, n if home_already_present else n - 1):
        routing.AddDisjunction([manager.NodeToIndex(i)], 1000)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = 10

    print("🧠 Solving TSP...")
    solution = routing.SolveWithParameters(search_params)
    if not solution:
        print("❌ No feasible route found.")
        return False

    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        route.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))
    route.append(manager.IndexToNode(index))

    ScheduledTask.query.filter_by(user_id=user.user_id).delete()
    db.session.commit()

    current_time = datetime.combine(datetime.now().date(), time(8, 0))
    for idx in route:
        if idx >= len(tasks_with_locations):
            print(f"Skipping index {idx} (home location)")
            continue

        raw, _ = tasks_with_locations[idx]
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