import pandas as pd
import googlemaps
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

# ==== CONFIG ====
API_KEY = 'AIzaSyC_Dz0XtugoW2odkRb-QGaMT96bA0y9YJs'  # Replace with your actual API key
gmaps = googlemaps.Client(key=API_KEY)

# ==== SAMPLE DATAFRAME ====
df = pd.DataFrame({
    'taskid': [0, 1, 2, 3],
    'taskname': ['Depot', 'Visit Prof', 'Conference', 'Meeting'],
    'location': [
        "76000 Verano Road, Irvine, CA",
        "San Jose State University, San Jose, CA",
        "Stanford University, Stanford, CA",
        "4541 Campus Dr, Irvine, CA 92612"
    ],
    'duration': [0, 50, 55, 90],
    'time_start': [0, 600, 500, 300],
    'time_end': [1440, 1100, 1000, 800],
    'mode': ['driving', 'driving', 'driving', 'walking']
})


# ==== STEP 1: Build Distance Matrix ====
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
                    row.append(element['duration']['value'] // 60)  # seconds → minutes
                else:
                    print(f"⚠️ Could not route from {origin} to {destination}")
                    row.append(999999)
        matrix.append(row)
    return matrix


# ==== STEP 2A: TSP with Constraints ====
def solve_tsp_with_constraints(distance_matrix, task_durations, time_windows):
    n = len(distance_matrix)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
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
        time_dim.SlackVar(idx).SetValue(task_durations[i])
        start, end = time_windows[i]
        time_dim.CumulVar(idx).SetRange(start, end)

    for i in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(i)], 1000)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = 3

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


# ==== RUN ====
if __name__ == "__main__":
    if not API_KEY or API_KEY == 'YOUR_API_KEY_HERE':
        print("❌ Please insert a valid API key.")
        exit()

    locations = df['location'].tolist()
    durations = df['duration'].tolist()
    time_windows = df[['time_start', 'time_end']].values.tolist()
    transport_mode = df['mode'].iloc[0]  # assuming same mode for all

    print("🛠 Building distance matrix...")
    distance_matrix = build_distance_matrix(locations, mode=transport_mode)

    route = solve_tsp_with_constraints(distance_matrix, durations, time_windows)

    if route:
        print("\n✅ Optimized Route:")
        for idx in route:
            row = df.iloc[idx]
            print(f" - {row.taskname} ({row.location})")
    else:
        print("❌ No feasible route found.")
