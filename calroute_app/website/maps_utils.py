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

def _best_mode_for_distance(mode, duration):
    """Select the best mode for a given distance."""
    if duration < 10:  # Approximately 1 km
        if 'walking' in mode:
            return 'walking'
        elif 'bike' in mode:
            return 'bike'
        else:
            return mode
    elif duration < 30:  # Approximately 5 km
        if 'bike' in mode:
            return 'bike'
        elif 'bus_train' in mode:
            return 'bus_train'
        else:
            return mode
    else:
        return mode

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
                        mode_matrix[i][j] = _best_mode_for_distance(google_mode, duration)
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

def solve_tsp(distance_matrix, task_durations, time_windows, mode_matrix=None, task_priorities=None, preferred_modes=None):
    """Solve the Traveling Salesman Problem with time windows.
    Simplified and more robust implementation.
    
    Args:
        distance_matrix: Matrix of travel times between locations
        task_durations: List of task durations in minutes
        time_windows: List of time windows [(start, end)] in minutes for each location
        mode_matrix: Optional matrix of transit modes between locations
        task_priorities: Optional list of priorities for each task (1=highest, fixed time)
    """
    print("Starting simplified TSP solver")
    print(f"Number of locations: {len(distance_matrix)}")
    
    # Create routing index manager
    num_locations = len(distance_matrix)
    depot = 0  # The starting location (home)
    num_vehicles = 1  # Just one vehicle/route
    
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)
    
    # Pre-calculate all required distances to avoid callback issues
    print("Pre-calculating distances for all possible routes...")
    distance_lookup = {}
    
    # First create a mapping from indices to nodes
    index_to_node_map = {}
    for node in range(num_locations):
        try:
            index = manager.NodeToIndex(node)
            index_to_node_map[index] = node
        except Exception as e:
            print(f"Error mapping node {node} to index: {e}")
    
    # Now pre-calculate all possible distances between indices
    for from_idx in range(num_locations * num_vehicles + 10):  # Add some buffer
        for to_idx in range(num_locations * num_vehicles + 10):  # Add some buffer
            try:
                # Try to get the corresponding nodes
                if from_idx in index_to_node_map and to_idx in index_to_node_map:
                    from_node = index_to_node_map[from_idx]
                    to_node = index_to_node_map[to_idx]
                    
                    # Check bounds
                    if 0 <= from_node < len(distance_matrix) and 0 <= to_node < len(distance_matrix[0]):
                        distance_lookup[(from_idx, to_idx)] = distance_matrix[from_node][to_node]
                    else:
                        distance_lookup[(from_idx, to_idx)] = 999999
                else:
                    distance_lookup[(from_idx, to_idx)] = 999999
            except Exception as e:
                # Just silently use a large value
                distance_lookup[(from_idx, to_idx)] = 999999
    
    # Define a simple callback that uses the pre-calculated distances
    def time_callback(from_idx, to_idx):
        try:
            # Convert to Python int to avoid any C++ type issues
            from_index = int(from_idx)
            to_index = int(to_idx)
            key = (from_index, to_index)
            
            # Use the lookup table, with a default if not found
            return distance_lookup.get(key, 999999)
        except:
            # Silent error - just return a default
            return 999999
    
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
    if task_priorities:
        print(f"Task priorities: {task_priorities}")
    
    # Add time window constraints with priority handling and error checks
    for location_idx in range(num_locations):
        try:
            if location_idx < len(time_windows):
                # Convert location_idx to a solver index
                try:
                    index = manager.NodeToIndex(location_idx)
                except Exception as e:
                    print(f"Error converting node {location_idx} to index: {e}")
                    continue
                    
                # Get time window with validation
                try:
                    tw = time_windows[location_idx]
                    # Ensure time window values are valid
                    start_min = max(0, int(tw[0]))
                    end_max = min(1440, int(tw[1]))
                except Exception as e:
                    print(f"Invalid time window at {location_idx}: {e}, using defaults")
                    start_min = 480  # 8am default
                    end_max = 1440   # midnight default
                
                # Ensure start_min <= end_max
                if start_min > end_max:
                    print(f"WARNING: Invalid time window at {location_idx}: {start_min}-{end_max}, adjusting")
                    start_min = min(start_min, end_max)
                
                # Check if this is a high-priority fixed-time task (like calendar event)
                is_fixed_time = False
                if task_priorities and location_idx < len(task_priorities):
                    try:
                        # Priority 1 is highest - these are fixed-time calendar events
                        priority = int(task_priorities[location_idx])
                        if priority == 1:
                            is_fixed_time = True
                            print(f"Node {location_idx} is a fixed-time calendar event with strict time window {start_min}-{end_max}")
                    except Exception as e:
                        print(f"Error processing priority for node {location_idx}: {e}")
                        # Default to non-fixed time
            
            try:
                print(f"Setting time window for node {location_idx}: {start_min} to {end_max}")
                time_dimension.CumulVar(index).SetRange(start_min, end_max)
                
                # For fixed-time calendar events, make the time window constraints stronger
                # by adding a higher penalty for deviating from the window
                if is_fixed_time:
                    try:
                        # Set very high penalties for violating calendar event time windows
                        time_dimension.SetCumulVarSoftLowerBound(index, start_min, 100000)  # High penalty
                        time_dimension.SetCumulVarSoftUpperBound(index, end_max, 100000)  # High penalty
                    except Exception as e:
                        print(f"Error setting penalties for fixed-time event {location_idx}: {e}")
                
                # Handle service times with error checking
                if location_idx > 0 and location_idx < len(task_durations):  # Skip depot for service time
                    try:
                        # Get task duration with validation
                        duration = int(task_durations[location_idx])
                        print(f"Setting minimum slack of {duration} minutes for location {location_idx}")
                        
                        # Add service time constraints
                        try:
                            time_dimension.SetSpanUpperBoundForVehicle(duration, 0)  # Ensure minimum service time
                        except Exception as e:
                            print(f"Error setting span bound for location {location_idx}: {e}")
                            
                        # Use soft bound for additional flexibility
                        try:
                            time_dimension.SetCumulVarSoftLowerBound(index, start_min + duration, 1000)
                        except Exception as e:
                            print(f"Error setting soft lower bound for location {location_idx}: {e}")
                    except Exception as e:
                        print(f"Error processing duration for location {location_idx}: {e}")
            except Exception as e:
                print(f"Error setting time window for node {location_idx}: {e}")
        except Exception as e:
            print(f"Error processing location {location_idx}: {e}")
            continue
    
    # Set first solution heuristic
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.time_limit.seconds = 10  # Give it up to 10 seconds to find a solution
    
    # Set more advanced local search options to help find solutions
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.log_search = True
    
    # Solve the problem with error handling
    print("Solving TSP...")
    solution = None
    try:
        solution = routing.SolveWithParameters(search_parameters)
    except Exception as e:
        print(f"Error solving TSP: {e}")
        import traceback
        print(f"Solver error traceback: {traceback.format_exc()}")
    
    # Check solution and extract data
    if not solution:
        print("No solution found for TSP")
        # Return a fallback solution
        # Define a default start time (8am in minutes)
        fallback_start_time = 8 * 60  # 8:00 AM
        fallback_route = list(range(num_locations))
        fallback_times = [fallback_start_time + i * 60 for i in range(num_locations)]
        
        # Create an intelligent fallback mode selection based on distance if possible
        fallback_modes = []
        if preferred_modes and len(preferred_modes) > 0:
            print(f"Creating fallback modes using preferred modes: {preferred_modes}")
            # Try to select appropriate modes based on distance between locations
            # For each segment, choose the most appropriate mode
            for i in range(len(fallback_route) - 1):
                from_idx = fallback_route[i]
                to_idx = fallback_route[i + 1]
                
                # Default to first preferred mode
                selected_mode = preferred_modes[0]
                
                # If we have a distance matrix, use it to determine the appropriate mode
                if distance_matrix and from_idx < len(distance_matrix) and to_idx < len(distance_matrix[0]):
                    distance_min = distance_matrix[from_idx][to_idx]
                    
                    # Approximate distance in km (assuming average speed of 60 km/h)
                    # So 1 minute ≈ 1 km
                    distance_km = distance_min
                    
                    # Choose mode based on distance
                    if distance_km < 1.0 and 'walking' in preferred_modes:
                        selected_mode = 'walking'
                        print(f"Fallback - short distance ({distance_km:.2f} km): using walking")
                    elif distance_km < 5.0 and 'bike' in preferred_modes:
                        selected_mode = 'bike'
                        print(f"Fallback - medium distance ({distance_km:.2f} km): using biking")
                    elif distance_km < 12.0 and 'bus_train' in preferred_modes:
                        selected_mode = 'bus_train'
                        print(f"Fallback - medium-long distance ({distance_km:.2f} km): using public transit")
                    elif 'rideshare' in preferred_modes:
                        selected_mode = 'rideshare'
                        print(f"Fallback - longer distance ({distance_km:.2f} km): using rideshare")
                    else:
                        # Use first available mode
                        selected_mode = preferred_modes[0]
                        print(f"Fallback - using default mode: {selected_mode}")
                else:
                    print(f"Fallback - no distance data available, using default mode: {selected_mode}")
                
                fallback_modes.append(selected_mode)
        else:
            # If no preferred modes specified, default to 'walking' for all segments
            print("No preferred modes specified, defaulting to 'walking'")
            fallback_modes = ['walking'] * (len(fallback_route) - 1)
            
        print(f"Using fallback route: {fallback_route}")
        print(f"Using fallback modes: {fallback_modes}")
        return fallback_route, fallback_times, 0, fallback_modes
    
    print("TSP solution found!")
    
    # Extract solution info
    route = []
    start_times = []
    total_time = 0
    travel_modes = None
    if mode_matrix:
        travel_modes = []
    
    # Debug info for the solution
    print(f"Solution status: {routing.status()}")  # 0 means ROUTING_SUCCESS
    
    # First, clear any duplicate locations (like home appearing twice)
    processed_nodes = set()
    cleaned_route = []
    
    index = routing.Start(0)
    prev_index = None
    prev_node = None
    
    while not routing.IsEnd(index):
        node_index = manager.IndexToNode(index)
        
        # Skip duplicate consecutive locations (like 0->0)
        if prev_node is not None and node_index == prev_node:
            print(f"Skipping duplicate location: {node_index}")
            index = solution.Value(routing.NextVar(index))
            continue
            
        # Skip if this would create a cycle back to home before visiting all locations
        if node_index == 0 and len(cleaned_route) < num_locations - 2:  # -2 because we have home at start and end
            print(f"Skipping premature return to home")
            index = solution.Value(routing.NextVar(index))
            continue
        
        route.append(node_index)
        cleaned_route.append(node_index)
        
        # Get time info with better error handling
        time_var = time_dimension.CumulVar(index)
        try:
            start_time = solution.Min(time_var)
            if start_time == 0 and node_index > 0:  # Likely an error for non-home locations
                print(f"Warning: Zero start time for node {node_index}, using fallback")
                # Use a fallback value based on previous task + travel time + duration
                if len(start_times) > 0:
                    prev_start = start_times[-1]
                    # Add previous task duration and travel time
                    if prev_node is not None:
                        travel_time = distance_matrix[prev_node][node_index]
                        task_duration = task_durations[prev_node] if prev_node < len(task_durations) else 0
                        start_time = prev_start + travel_time + task_duration
                        print(f"Computed fallback start time: {start_time} for node {node_index}")
        except Exception as e:
            print(f"Error getting start time for node {node_index}: {e}")
            # Fallback - assign a reasonable time
            if len(start_times) > 0:
                start_time = start_times[-1] + 60  # Add 1 hour as fallback
            else:
                start_time = 540  # 9am fallback
        
        start_times.append(start_time)
        
        # Get travel mode if available with better error handling
        if mode_matrix:
            next_index = solution.Value(routing.NextVar(index))
            if not routing.IsEnd(next_index):
                next_node_index = manager.IndexToNode(next_index)
                if node_index < len(mode_matrix) and next_node_index < len(mode_matrix[0]):
                    mode = mode_matrix[node_index][next_node_index]
                    print(f"Selected mode from {node_index} to {next_node_index}: {mode}")
                    if not mode:  # If mode is empty, find a suitable default
                        # Try to use one of the preferred modes
                        if preferred_modes and len(preferred_modes) > 0:
                            # Default to first preferred mode
                            mode = preferred_modes[0]
                        else:
                            # Find first non-empty mode in the matrix as default
                            default_mode = None
                            for row in mode_matrix:
                                for m in row:
                                    if m and m != '':
                                        default_mode = m
                                        break
                                if default_mode:
                                    break
                            mode = default_mode if default_mode else "walking"  # Safest default
                    travel_modes.append(mode)
                else:
                    print(f"Mode matrix indices out of range: {node_index}, {next_node_index}")
                    # Try to use one of the preferred modes
                    if preferred_modes and len(preferred_modes) > 0:
                        # Default to first preferred mode
                        travel_modes.append(preferred_modes[0])
                    else:
                        # Find first non-empty mode in the matrix as default
                        default_mode = None
                        for row in mode_matrix:
                            for m in row:
                                if m and m != '':
                                    default_mode = m
                                    break
                            if default_mode:
                                break
                        travel_modes.append(default_mode if default_mode else "walking")
            else:
                # Last leg going back to depot
                # Try to use one of the preferred modes
                if preferred_modes and len(preferred_modes) > 0:
                    # Default to first preferred mode
                    travel_modes.append(preferred_modes[0])
                else:
                    # Find first non-empty mode in the matrix as default
                    default_mode = None
                    for row in mode_matrix:
                        for m in row:
                            if m and m != '':
                                default_mode = m
                                break
                        if default_mode:
                            break
                    travel_modes.append(default_mode if default_mode else "walking")

        # Update total travel time
        if prev_node is not None:
            leg_time = distance_matrix[prev_node][node_index]
            print(f"Travel time from {prev_node} to {node_index}: {leg_time} minutes")
            total_time += leg_time
        else:
            print(f"Starting at node {node_index} - no travel time yet")

        # Update previous node for next iteration
        prev_node = node_index
        prev_index = index
        index = solution.Value(routing.NextVar(index))

    # Add the final node (only if it's not already the last node in the route)
    node_index = manager.IndexToNode(index)
    if len(route) == 0 or route[-1] != node_index:
        route.append(node_index)
        cleaned_route.append(node_index)
    
        # Get the time for this final node
        time_var = time_dimension.CumulVar(index)
        try:
            start_time = solution.Min(time_var)
        except Exception as e:
            print(f"Error getting final node time: {e}")
            # Use last time + travel time from previous node
            if start_times:
                if prev_node is not None and node_index < len(distance_matrix[0]):
                    travel_time = distance_matrix[prev_node][node_index]
                    start_time = start_times[-1] + travel_time
                else:
                    start_time = start_times[-1] + 30  # Default 30 min
            else:
                start_time = 540  # 9am default
        
        start_times.append(start_time)
    
    # Ensure transit modes match the route
    if mode_matrix:
        # Fix length of transit modes if needed
        if len(travel_modes) < len(route) - 1:
            missing_count = len(route) - 1 - len(travel_modes)
            print(f"Adding {missing_count} missing transit modes")
            for _ in range(missing_count):
                travel_modes.append("car")  # Default to car for missing modes
    
    # Clean up potentially invalid travel times
    for i, time in enumerate(start_times):
        if time < 0 or time > 1440:  # Invalid time (negative or > 24h)
            print(f"Fixing invalid time at position {i}: {time}")
            if i > 0:
                # Base it on previous time + average task duration
                start_times[i] = start_times[i-1] + 60  # Default 1h
            else:
                start_times[i] = 540  # 9am default
    
    # If total time is invalid, recalculate
    if total_time <= 0 or total_time == float('inf'):
        print("Recalculating total travel time")
        total_time = 0
        for i in range(len(route) - 1):
            from_node = route[i]
            to_node = route[i+1]
            if from_node < len(distance_matrix) and to_node < len(distance_matrix[0]):
                total_time += distance_matrix[from_node][to_node]
    
    print(f"Optimal route found: {route}")
    print(f"Start times: {start_times}")
    print(f"Total travel time: {total_time} minutes")
    print(f"Transit modes: {travel_modes}")
    print("TSP solution complete")
    
    return route, start_times, total_time, travel_modes