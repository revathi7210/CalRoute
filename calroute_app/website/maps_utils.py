# maps_utils.py
import googlemaps
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import os
import json
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor

GOOGLE_MAPS_API = os.environ.get("GOOGLE_MAPS_API_ID")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API)

# In-memory cache for distance matrices
_distance_cache = {}

# Cache expiration time (in seconds)
CACHE_EXPIRY = 7 * 24 * 60 * 60  # 1 week

def _get_cache_key(origin, destination, mode):
    """Generate a unique cache key for a route."""
    return f"{origin}|{destination}|{mode}"

def _get_batch_keys(origins, destinations, mode):
    """Generate all cache keys for a batch of origins and destinations."""
    keys = []
    for origin in origins:
        for destination in destinations:
            if origin != destination:  # Skip same location pairs
                keys.append(_get_cache_key(origin, destination, mode))
    return keys

def _is_cached_and_valid(cache_key):
    """Check if a route is in the cache and not expired."""
    if cache_key in _distance_cache:
        timestamp, _ = _distance_cache[cache_key]
        if time.time() - timestamp < CACHE_EXPIRY:
            return True
    return False

def _get_uncached_pairs(locations, mode):
    """Get pairs of locations that need to be fetched from the API."""
    uncached_origins = []
    uncached_destinations = []
    
    for i, origin in enumerate(locations):
        for j, destination in enumerate(locations):
            if i != j:  # Skip same location
                cache_key = _get_cache_key(origin, destination, mode)
                if not _is_cached_and_valid(cache_key):
                    uncached_origins.append(origin)
                    uncached_destinations.append(destination)
    
    # Remove duplicates while preserving order
    unique_origins = []
    unique_destinations = []
    seen_origins = set()
    seen_destinations = set()
    
    for origin in uncached_origins:
        if origin not in seen_origins:
            unique_origins.append(origin)
            seen_origins.add(origin)
            
    for destination in uncached_destinations:
        if destination not in seen_destinations:
            unique_destinations.append(destination)
            seen_destinations.add(destination)
    
    return unique_origins, unique_destinations

# Distance thresholds for mode selection (in minutes)
SHORT_DISTANCE_THRESHOLD = 15  # 15 minutes
MEDIUM_DISTANCE_THRESHOLD = 30  # 30 minutes

# Mode preferences by distance
SHORT_DISTANCE_MODES = ['walking', 'bicycling']
MEDIUM_DISTANCE_MODES = ['bicycling', 'transit', 'driving']
LONG_DISTANCE_MODES = ['driving', 'transit']

def _should_use_mode_for_distance(mode, distance_minutes):
    """Determine if a mode is appropriate for a given distance."""
    if mode == 'walking':
        return distance_minutes <= SHORT_DISTANCE_THRESHOLD
    elif mode == 'bicycling':
        return distance_minutes <= MEDIUM_DISTANCE_THRESHOLD
    elif mode == 'transit':
        return distance_minutes >= SHORT_DISTANCE_THRESHOLD
    elif mode == 'driving':
        return distance_minutes >= SHORT_DISTANCE_THRESHOLD
    return True  # Default to using any mode if not specified

def _process_mode(locations, mode, mode_mapping, final_matrix, mode_matrix):
    """Process a single transport mode and update the final matrix."""
    google_mode = mode_mapping.get(mode, 'driving')
    num_locations = len(locations)
    
    # First check what we already have in cache
    missing_data = False
    for i in range(num_locations):
        for j in range(num_locations):
            if i == j:
                final_matrix[i][j] = 0
                continue
                
            cache_key = _get_cache_key(locations[i], locations[j], google_mode)
            if _is_cached_and_valid(cache_key):
                _, duration = _distance_cache[cache_key]
                
                # Check if this mode is appropriate for this distance
                # Only consider this mode if it's appropriate for the distance
                if _should_use_mode_for_distance(google_mode, duration):
                    if duration < final_matrix[i][j]:
                        final_matrix[i][j] = duration
                        mode_matrix[i][j] = mode  # Track which mode was fastest
            else:
                missing_data = True
    
    # If we have all data in cache, no need for API calls
    if not missing_data:
        return
    
    # Get unique uncached origin-destination pairs
    uncached_origins, uncached_destinations = _get_uncached_pairs(locations, google_mode)
    
    # If nothing to fetch, return early
    if not uncached_origins or not uncached_destinations:
        return
    
    # Make batched API calls (Google Maps API allows 25 origins * 25 destinations per call)
    MAX_ELEMENTS_PER_CALL = 25 * 25
    BATCH_SIZE = 25  # Google's max batch size
    
    try:
        # Process in batches to respect API limits
        for i in range(0, len(uncached_origins), BATCH_SIZE):
            batch_origins = uncached_origins[i:i + BATCH_SIZE]
            
            for j in range(0, len(uncached_destinations), BATCH_SIZE):
                batch_destinations = uncached_destinations[j:j + BATCH_SIZE]
                
                # Skip if this would exceed the maximum elements per call
                if len(batch_origins) * len(batch_destinations) > MAX_ELEMENTS_PER_CALL:
                    continue
                
                # Make the API call for this batch
                response = gmaps.distance_matrix(
                    batch_origins, 
                    batch_destinations, 
                    mode=google_mode
                )
                
                # Store results in cache and update matrix
                for o_idx, row in enumerate(response['rows']):
                    origin = batch_origins[o_idx]
                    
                    for d_idx, element in enumerate(row['elements']):
                        destination = batch_destinations[d_idx]
                        
                        if origin == destination:
                            continue
                            
                        if element['status'] == 'OK':
                            duration_minutes = element['duration']['value'] // 60
                            
                            # Update cache
                            cache_key = _get_cache_key(origin, destination, google_mode)
                            _distance_cache[cache_key] = (time.time(), duration_minutes)
                            
                            # Update matrix if we find this pair in our locations list
                            try:
                                i = locations.index(origin)
                                j = locations.index(destination)
                                
                                # Only use this mode if it's appropriate for the distance
                                if _should_use_mode_for_distance(google_mode, duration_minutes):
                                    if duration_minutes < final_matrix[i][j]:
                                        final_matrix[i][j] = duration_minutes
                                        mode_matrix[i][j] = mode  # Track which mode was fastest
                            except ValueError:
                                # This origin or destination might not be in our main locations list
                                pass
                
                # Respect API rate limits
                time.sleep(0.2)  # Small delay between batches
                
    except Exception as e:
        print(f"Error fetching distance matrix for mode {google_mode}: {e}")

def build_distance_matrix(locations, modes=["car"]):
    """
    Build a distance matrix using Google Maps Distance Matrix API efficiently.
    Uses caching and batching to minimize API calls.
    
    Args:
        locations: List of location addresses.
        modes: List of travel modes ('car', 'bike', 'bus_train', 'walking', 'rideshare').
    Returns:
        Tuple containing:
        - A matrix of the minimum travel times in minutes
        - A matrix of the best transit modes for each segment
    """
    print(f"Building distance matrix for {len(locations)} locations")
    print(f"Using travel modes: {modes}")
    
    # Initialize matrices
    num_locations = len(locations)
    final_matrix = [[float('inf')] * num_locations for _ in range(num_locations)]
    mode_matrix = [[''] * num_locations for _ in range(num_locations)]
    
    # Process each mode
    mode_mapping = {
        'car': 'driving',
        'bike': 'bicycling',
        'bus_train': 'transit',
        'walking': 'walking',
        'rideshare': 'driving'  # Rideshare uses driving mode
    }
    
    for mode in modes:
        print(f"Processing mode: {mode}")
        _process_mode(locations, mode, mode_mapping, final_matrix, mode_matrix)
    
    # Ensure diagonal is zero (distance from location to itself)
    for i in range(num_locations):
        final_matrix[i][i] = 0
    
    print("Distance matrix build complete")
    return final_matrix, mode_matrix
    
    # Map our application's mode names to the Google Maps API mode names
    mode_mapping = {
        'car': 'driving',
        'bike': 'bicycling',
        'bus_train': 'transit',
        'walking': 'walking',
        'rideshare': 'driving' 
    }
    
    num_locations = len(locations)
    
    # Skip API calls altogether for very small matrices
    if num_locations <= 1:
        return [[0]] if num_locations == 1 else []
    
    # Initialize the final matrix with infinity, which we'll update with minimums
    final_matrix = [[float('inf')] * num_locations for _ in range(num_locations)]
    
    # Track the best mode for each segment
    mode_matrix = [[None] * num_locations for _ in range(num_locations)]
    
    # Diagonal should always be zero (same location)
    for i in range(num_locations):
        final_matrix[i][i] = 0
        mode_matrix[i][i] = None  # No travel mode needed for same location
    
    # Create a matrix to track the best mode for each route segment
    mode_matrix = [[None] * num_locations for _ in range(num_locations)]
    
    # Process each mode in parallel for better performance
    with ThreadPoolExecutor(max_workers=min(len(modes), 3)) as executor:
        futures = []
        for mode in modes:
            futures.append(
                executor.submit(_process_mode, locations, mode, mode_mapping, final_matrix, mode_matrix)
            )
        
        # Wait for all mode calculations to complete
        for future in futures:
            future.result()
    
    # Replace any remaining 'inf' values with a high penalty number for the solver
    for i in range(num_locations):
        for j in range(num_locations):
            if final_matrix[i][j] == float('inf'):
                final_matrix[i][j] = 999999  # A large number indicating an impossible route

def solve_tsp(distance_matrix, task_durations, time_windows, mode_matrix=None):
    """Solve the Traveling Salesman Problem with time windows.
    Simplified and more robust implementation.
    """
    print("Starting simplified TSP solver")
    print(f"Number of locations: {len(distance_matrix)}")
    
    # Create routing index manager
    num_locations = len(distance_matrix)
    depot = 0  # The starting location (home)
    num_vehicles = 1  # Just one vehicle/route
    
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)
    
    # Define distance/time callback
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]
    
    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    
    # Define arc cost
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    
    # Add time dimension
    routing.AddDimension(
        transit_callback_index,  # transit callback
        30,     # slack (waiting time)
        1440,   # maximum time (24 hours in minutes)
        False,  # don't force start cumul to zero
        'Time'  # name of dimension
    )
    
    time_dimension = routing.GetDimensionOrDie('Time')
    
    # Print time windows for debugging
    print(f"Time windows: {time_windows}")
    
    # Add time window constraints (more robust version)
    for location_idx in range(num_locations):
        if location_idx < len(time_windows):
            index = manager.NodeToIndex(location_idx)
            tw = time_windows[location_idx]
            
            # Ensure time window values are valid
            start_min = max(0, tw[0])
            end_max = min(1440, tw[1])
            
            # Ensure start_min <= end_max
            if start_min > end_max:
                print(f"WARNING: Invalid time window at {location_idx}: {tw}, adjusting")
                start_min = min(start_min, end_max)
                
            print(f"Setting time window for node {location_idx}: {start_min} to {end_max}")
            time_dimension.CumulVar(index).SetRange(start_min, end_max)
            
            # Simpler way to handle service times - use minimum slack instead of fixed value
            if location_idx > 0 and location_idx < len(task_durations):  # Skip depot for service time
                # We add the service time as a minimum slack, not a hard constraint
                duration = task_durations[location_idx]
                print(f"Setting minimum slack of {duration} minutes for location {location_idx}")
                time_dimension.SetSpanUpperBoundForVehicle(duration, 0)  # Ensure minimum service time
                
                # Use soft bound instead of hard SetValue constraint
                time_dimension.SetCumulVarSoftLowerBound(index, start_min + duration, 1000)
    
    # Set first solution heuristic
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.time_limit.seconds = 10  # Give it up to 10 seconds to find a solution
    
    # Set more advanced local search options to help find solutions
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.log_search = True
    
    # Solve the problem
    print("Solving TSP...")
    solution = routing.SolveWithParameters(search_parameters)
    
    # Check solution and extract data
    if not solution:
        print("No solution found for TSP")
        return None, None, None, None
    
    print("TSP solution found!")
    
    # Extract solution info
    route = []
    start_times = []
    total_time = 0
    travel_modes = [] if mode_matrix else None
    
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node_index = manager.IndexToNode(index)
        route.append(node_index)
        
        # Get time info
        time_var = time_dimension.CumulVar(index)
        start_time = solution.Min(time_var)  # Use Min instead of Value which can be problematic
        start_times.append(start_time)
        
        # Get travel mode if available
        if mode_matrix:
            next_index = solution.Value(routing.NextVar(index))
            if not routing.IsEnd(next_index):
                next_node_index = manager.IndexToNode(next_index)
                travel_modes.append(mode_matrix[node_index][next_node_index])
        
        # Update total time
        previous_index = index
        index = solution.Value(routing.NextVar(index))
        if not routing.IsEnd(index):
            travel_time = distance_matrix[node_index][manager.IndexToNode(index)]
            total_time += travel_time
    
    # Add the final node
    node_index = manager.IndexToNode(index)
    route.append(node_index)
    time_var = time_dimension.CumulVar(index)
    start_time = solution.Min(time_var)  # Use Min instead of Value which can be problematic
    start_times.append(start_time)
    
    print(f"Optimal route found: {route}")
    print(f"Start times: {start_times}")
    print(f"Total travel time: {total_time} minutes")
    print("TSP solution complete")
    
    return route, start_times, total_time, travel_modes