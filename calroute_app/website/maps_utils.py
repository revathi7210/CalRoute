# maps_utils.py
import googlemaps
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import os

GOOGLE_MAPS_API = os.environ.get("GOOGLE_MAPS_API_ID")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API)

def build_distance_matrix(locations, mode="driving"):
    matrix = []
    for i, origin in enumerate(locations):
        row = []
        for j, destination in enumerate(locations):
            if i == j:
                row.append(0)
            else:
                res = gmaps.distance_matrix(origin, destination, mode=mode)
                element = res['rows'][0]['elements'][0]
                if element['status'] == 'OK':
                    row.append(element['duration']['value'] // 60)
                else:
                    print(f"Could not route from {origin} to {destination}")
                    row.append(999999)
        matrix.append(row)
    return matrix

# def solve_tsp(distance_matrix, task_durations, time_windows, start_index, end_index):
#     n = len(distance_matrix)
#     manager = pywrapcp.RoutingIndexManager(n, 1, [start_index],[end_index])
#     routing = pywrapcp.RoutingModel(manager)

#     def time_callback(from_index, to_index):
#         return distance_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

#     transit_index = routing.RegisterTransitCallback(time_callback)
#     routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

#     routing.AddDimension(
#         transit_index,
#         120,
#         1440,
#         False,
#         "Time"
#     )
#     time_dim = routing.GetDimensionOrDie("Time")

#     for i in range(n):
#         idx = manager.NodeToIndex(i)
#         time_dim.SlackVar(idx).SetValue(task_durations[i])
#         start, end = time_windows[i]
#         time_dim.CumulVar(idx).SetRange(start, end)

#     for i in range(1, n):
#         routing.AddDisjunction([manager.NodeToIndex(i)], 1000)

#     search_params = pywrapcp.DefaultRoutingSearchParameters()
#     search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
#     search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
#     search_params.time_limit.seconds = 10

#     print("Solving TSP with time windows...")
#     solution = routing.SolveWithParameters(search_params)
#     if not solution:
#         print("Solver failed.")
#         return None

#     route = []
#     index = routing.Start(0)
#     while not routing.IsEnd(index):
#         route.append(manager.IndexToNode(index))
#         index = solution.Value(routing.NextVar(index))
#     route.append(manager.IndexToNode(index))
#     return route


#WITHOUT SLACKVAR
def solve_tsp(distance_matrix, task_durations, time_windows):
    n = len(distance_matrix)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index, to_index):
        """Travel time + service time at the from node"""
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node] + task_durations[from_node]

    transit_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    # Add Time dimension (travel time only)
    routing.AddDimension(
        transit_index,
        120,  # Allow 2 hours waiting time if needed
        1440,  # Max time per vehicle
        False,
        "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")

    for i in range(n):
        idx = manager.NodeToIndex(i)
        start, end = time_windows[i]
        time_dim.CumulVar(idx).SetRange(start, end)
        print(f"Node {i} → Duration: {task_durations[i]} min, TimeWindow: {start}-{end} min")

    for i in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(i)], 1000)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = 5

    print("🧠 Solving TSP with time windows...")
    solution = routing.SolveWithParameters(search_params)
    if not solution:
        print("❌ Solver failed.")
        return None

    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        route.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))
    route.append(manager.IndexToNode(index))
    return route



# def solve_tsp(distance_matrix, task_durations, time_windows,
#               start_index=0, end_index=0, horizon=1440, time_limit_s=5):
#     n = len(distance_matrix)
#     print(f"\n🔧 Setting up TSP with {n} nodes, depot={start_index}→{end_index}")
#     for i in range(n):
#         print(f"  Node {i}: service={task_durations[i]}min TW={time_windows[i]}")
#     manager = pywrapcp.RoutingIndexManager(n, 1, [start_index], [end_index])
#     routing = pywrapcp.RoutingModel(manager)

#     # transit = service at from + travel from→to
#     def travel_service(from_idx, to_idx):
#         i = manager.IndexToNode(from_idx)
#         j = manager.IndexToNode(to_idx)
#         return task_durations[i] + distance_matrix[i][j]

#     transit_cb = routing.RegisterTransitCallback(travel_service)
#     routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

#     routing.AddDimension(
#         transit_cb,
#         120,        # no slack
#         horizon,  # max total time
#         True,     # force start cumul = 0
#         "Time"
#     )
#     time_dim = routing.GetDimensionOrDie("Time")

#     # apply windows
#     for node in range(n):
#         idx = manager.NodeToIndex(node)
#         start, end = time_windows[node]
#         time_dim.CumulVar(idx).SetRange(start, end)

#     search_params = pywrapcp.DefaultRoutingSearchParameters()
#     search_params.first_solution_strategy = (
#         routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
#     )
#     search_params.local_search_metaheuristic = (
#         routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
#     )
#     search_params.time_limit.seconds = time_limit_s

#     print("🧠 Solving TSP …")
#     solution = routing.SolveWithParameters(search_params)
#     if solution is None:
#         print("❌ Solver failed.")
#         return None, manager, routing, None

#     # extract route
#     route = []
#     index = routing.Start(0)
#     while not routing.IsEnd(index):
#         route.append(manager.IndexToNode(index))
#         index = solution.Value(routing.NextVar(index))
#     route.append(manager.IndexToNode(index))

#     print("✅ Route found:", route)
#     return route, manager, routing, solution