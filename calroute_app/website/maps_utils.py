
    # maps_utils.py
import googlemaps
import os
import json
from datetime import datetime, timedelta
import time
import random
import math
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
    """Determine if a mode is appropriate for a given distance.
    This is a less restrictive version that allows more modes to be considered.
    """
    # For distance matrix calculation, we'll be less restrictive to allow more options
    # The final mode selection will happen in the optimizer based on actual distances
    
    # Walking only makes sense for short distances
    if mode == 'walking':
        return distance_minutes <= SHORT_DISTANCE_THRESHOLD * 1.5  # More lenient
    
    # Biking for short to medium distances
    elif mode == 'bicycling':
        return distance_minutes <= MEDIUM_DISTANCE_THRESHOLD * 1.5  # More lenient
    
    # Transit and driving for most distances except very short ones
    elif mode in ['transit', 'driving']:
        return True  # Allow these modes for any distance
    
    # Default to using any mode if not specified
    return True

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
    
    # Replace any remaining infinity values with more reasonable estimates
    for i in range(num_locations):
        for j in range(num_locations):
            if math.isinf(final_matrix[i][j]):
                # Check for airport in location string to use a reasonable airport travel time
                loc_i = locations[i].lower() if i < len(locations) else ""
                loc_j = locations[j].lower() if j < len(locations) else ""
                
                if 'airport' in loc_i or 'airport' in loc_j:
                    # Airport travel time is typically 30-60 minutes
                    estimate = 45  # 45 minutes as a reasonable airport travel time
                    print(f"Airport route detected between {i} and {j}, using estimated travel time of {estimate} minutes")
                else:
                    # For regular routes, estimate based on nearby routes that worked
                    estimates = []
                    for k in range(num_locations):
                        if k != i and k != j and not math.isinf(final_matrix[i][k]) and not math.isinf(final_matrix[k][j]):
                            # Route through k is viable
                            estimates.append(final_matrix[i][k] + final_matrix[k][j])
                    
                    if estimates:
                        # Take the minimum of all viable routes
                        estimate = min(estimates)
                        print(f"Estimated travel time between {i} and {j} as {estimate} minutes based on nearby routes")
                    else:
                        # Fallback to a more reasonable maximum time (60 minutes)
                        estimate = 60  # More reasonable fallback (1 hour)
                        print(f"Warning: No viable route estimates between {i} and {j}, using fallback of {estimate} minutes")
                
                final_matrix[i][j] = estimate
                
                # Use the first mode in the list for this route as a fallback
                if len(modes) > 0 and mode_matrix[i][j] == '':
                    mode_matrix[i][j] = modes[0]
    
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

def solve_tsp_custom(distance_matrix, task_durations, time_windows, mode_matrix=None, task_priorities=None):
    """A custom TSP solver using simulated annealing to better handle different transit modes.
    This implementation works well for small to medium sized problems (up to ~20 locations).
    
    Args:
        distance_matrix: Matrix of travel times between locations
        task_durations: List of task durations in minutes
        time_windows: List of time windows [(start, end)] in minutes for each location
        mode_matrix: Optional matrix of transit modes between locations
        task_priorities: Optional list of priorities for each task (1=highest, fixed time)
    
    Returns:
        Tuple of (route, start_times, total_travel_time, travel_modes)
    """
    import random
    import math
    import copy
    
    def calculate_route_cost(route, schedule):
        """Calculate the cost of a route with its schedule."""
        # Base cost is the total travel time
        cost = sum(distance_matrix[route[i]][route[i+1]] for i in range(len(route)-1))
        
        # Add penalties for time window violations
        for i, loc_idx in enumerate(route):
            if i > 0:  # Skip the start depot
                start_time = schedule[i]
                tw_start, tw_end = time_windows[loc_idx]
                
                # Penalty for arriving before the window starts
                if start_time < tw_start:
                    cost += 100 * (tw_start - start_time)
                
                # Penalty for arriving after the window ends
                if start_time > tw_end:
                    cost += 1000 * (start_time - tw_end)
                    
                # Add priority penalties - fixed time tasks (priority 1) get very high penalties
                if task_priorities and loc_idx < len(task_priorities):
                    if task_priorities[loc_idx] == 1 and (start_time < tw_start or start_time > tw_end):
                        cost += 10000  # Huge penalty for fixed-time tasks
        
        return cost
    
    def create_schedule(route):
        """Create a schedule (start times) for a given route."""
        schedule = [0] * len(route)  # Initialize with zeros
        current_time = time_windows[route[0]][0]  # Start at the earliest time for depot
        
        for i in range(1, len(route)):
            prev_idx = route[i-1]
            curr_idx = route[i]
            
            # Travel time from previous to current
            travel_time = distance_matrix[prev_idx][curr_idx]
            
            # Handle infinite travel time
            if math.isinf(travel_time):
                print(f"Warning: Infinite travel time detected in schedule between {prev_idx} and {curr_idx}")
                # More reasonable estimate for travel time
                if 'airport' in str(route).lower():
                    travel_time = 45  # 45 minutes for airport routes
                    print(f"Using estimated airport travel time of {travel_time} minutes")
                else:
                    travel_time = 60  # 1 hour for other routes
                    print(f"Using estimated travel time of {travel_time} minutes")
            
            # Add service time for previous location (if it's not the depot)
            if i > 1:  # Skip service time for depot
                service_time = task_durations[prev_idx]
                current_time += service_time
            
            # Travel to next location
            current_time += travel_time
            
            # Ensure we respect the earliest start time window
            tw_start, _ = time_windows[curr_idx]
            if current_time < tw_start:
                current_time = tw_start
            
            schedule[i] = current_time
        
        return schedule
    
    print("\n🔍 Starting custom TSP solver with simulated annealing")
    print(f"📍 Number of locations: {len(distance_matrix)}")
    
    # Log the distance matrix for debugging (truncated for readability)
    print("\n📊 Distance Matrix (sample):")
    for i in range(min(3, len(distance_matrix))):
        print(f"  Location {i}: {[round(d, 1) if not math.isinf(d) else 'inf' for d in distance_matrix[i][:min(5, len(distance_matrix))]]}") 
    
    # Log time windows
    print("\n🕓 Time Windows (first 5):")
    for i in range(min(5, len(time_windows))):
        print(f"  Location {i}: {time_windows[i]}")
    
    num_locations = len(distance_matrix)
    
    # Create initial route: start at depot (0), visit all locations, return to depot
    initial_route = [0] + list(range(1, num_locations-1)) + [num_locations-1]
    
    # If only 2 locations (start and end), return direct route
    if num_locations <= 2:
        schedule = create_schedule(initial_route)
        total_time = sum(distance_matrix[initial_route[i]][initial_route[i+1]] 
                         for i in range(len(initial_route)-1))
        
        if mode_matrix:
            travel_modes = [mode_matrix[initial_route[i]][initial_route[i+1]] 
                           for i in range(len(initial_route)-1)]
        else:
            travel_modes = None
            
        return initial_route, schedule, total_time, travel_modes
    
    # Simulated annealing parameters
    temperature = 1000.0
    cooling_rate = 0.95
    min_temperature = 1e-6
    
    # Initialize with current solution
    current_route = initial_route.copy()
    current_schedule = create_schedule(current_route)
    current_cost = calculate_route_cost(current_route, current_schedule)
    
    best_route = current_route.copy()
    best_schedule = current_schedule.copy()
    best_cost = current_cost
    
    iteration = 0
    max_iterations = 10000
    
    print("\n🧪 TSP optimization parameters:")
    print(f"  Temperature: {temperature}")
    print(f"  Cooling rate: {cooling_rate}")
    print(f"  Max iterations: {max_iterations}")
    
    # Start simulated annealing
    print("\n🔄 Beginning simulated annealing process...")
    while temperature > min_temperature and iteration < max_iterations:
        iteration += 1
        
        # Select two non-depot positions to swap
        i = random.randint(1, len(current_route) - 3)
        j = random.randint(i + 1, len(current_route) - 2)
        
        # Create new candidate route by swapping
        new_route = current_route.copy()
        new_route[i], new_route[j] = new_route[j], new_route[i]
        
        # Calculate new schedule and cost
        new_schedule = create_schedule(new_route)
        new_cost = calculate_route_cost(new_route, new_schedule)
        
        # Decide whether to accept the new solution
        cost_diff = new_cost - current_cost
        if cost_diff < 0 or random.random() < math.exp(-cost_diff / temperature):
            current_route = new_route
            current_schedule = new_schedule
            current_cost = new_cost
            
            # Update best solution if this is better
            if current_cost < best_cost:
                best_route = current_route.copy()
                best_schedule = current_schedule.copy()
                best_cost = current_cost
                print(f"Found better solution: {best_cost}")
        
        # Cool down
        temperature *= cooling_rate
        
        # Print progress occasionally
        if iteration % 500 == 0:
            print(f"Iteration {iteration}, Temperature: {temperature:.2f}, Current cost: {current_cost:.2f}, Best cost: {best_cost:.2f}")
    
    print(f"\n🏆 TSP optimization completed after {iteration} iterations")
    print(f"🛣️ Best route: {best_route}")
    print(f"📅 Best schedule (start times): {best_schedule}")
    
    if task_priorities:
        print("\n⭐ Task priorities:")
        for i, loc in enumerate(best_route):
            if loc < len(task_priorities):
                priority = task_priorities[loc]
                print(f"  Location {loc}: Priority {priority}")
    
    # Calculate and log time window adherence
    print("\n📋 Time window adherence:")
    for i, loc in enumerate(best_route):
        if i > 0:  # Skip depot
            start = best_schedule[i]
            tw_start, tw_end = time_windows[loc]
            status = "✅ On time"
            if start < tw_start:
                status = f"⚠️ Early by {tw_start - start} min"
            elif start > tw_end:
                status = f"❌ Late by {start - tw_end} min"
            print(f"  Location {loc}: Scheduled at {start}, Window {tw_start}-{tw_end}, {status}")
    
    # Calculate total travel time with protection against infinity values
    total_time = 0
    for i in range(len(best_route)-1):
        travel_time = distance_matrix[best_route[i]][best_route[i+1]]
        # Replace infinity with a large but finite number (8 hours in minutes)
        if math.isinf(travel_time):
            print(f"Warning: Infinite travel time detected between {best_route[i]} and {best_route[i+1]}")
            travel_time = 480  # 8 hours in minutes
        total_time += travel_time
    
    # Extract travel modes if provided
    if mode_matrix:
        travel_modes = []
        for i in range(len(best_route)-1):
            mode = mode_matrix[best_route[i]][best_route[i+1]]
            travel_modes.append(mode)
    else:
        travel_modes = None
    
    print(f"\n🎯 TSP solution found with total travel time: {total_time} minutes")
    
    # Log transit modes if available
    if travel_modes:
        print("\n🚗 Selected transit modes for each leg of the journey:")
        for i in range(len(best_route)-1):
            from_loc = best_route[i]
            to_loc = best_route[i+1]
            mode = travel_modes[i]
            travel_time = distance_matrix[from_loc][to_loc]
            if math.isinf(travel_time):
                travel_time = "estimated"
            print(f"  From {from_loc} to {to_loc}: {mode} ({travel_time} min)")
    
    return best_route, best_schedule, total_time, travel_modes

def solve_tsp(distance_matrix, task_durations, time_windows, mode_matrix=None, task_priorities=None):
    """Solve the Traveling Salesman Problem with time windows.
    This function uses a custom simulated annealing solver that works well with different transit modes.
    
    Args:
        distance_matrix: Matrix of travel times between locations
        task_durations: List of task durations in minutes
        time_windows: List of time windows [(start, end)] in minutes for each location
        mode_matrix: Optional matrix of transit modes between locations
        task_priorities: Optional list of priorities for each task (1=highest, fixed time)
    
    Returns:
        Tuple of (route, start_times, total_travel_time, travel_modes)
    """
    # Log the input parameters
    print("\n🧩 TSP SOLVER STARTED")
    print(f"🔢 Problem size: {len(distance_matrix)} locations")
    print(f"⏱️ Task durations: {task_durations}")
    print(f"🚗 Transit modes available: {bool(mode_matrix)}")
    
    # Use our custom solver instead of OR-Tools
    try:
        print("\n🔄 Using custom TSP solver (simulated annealing)...")
        route, start_times, total_travel_time, travel_modes = solve_tsp_custom(
            distance_matrix, 
            task_durations, 
            time_windows, 
            mode_matrix, 
            task_priorities
        )
        print("\n✅ Custom TSP solver completed successfully")
        print(f"📋 Final route: {route}")
        print(f"⏰ Start times: {start_times}")
        print(f"⌛ Total travel time: {total_travel_time} minutes")
        print(f"🚌 Transit modes selected: {travel_modes}")
        return route, start_times, total_travel_time, travel_modes
    except Exception as e:
        print(f"Custom TSP solver failed: {e}")
        print("Falling back to basic route...")
        
        # Define a fallback solution if the solver fails
        route = list(range(len(distance_matrix)))
        start_times = [time_windows[i][0] for i in range(len(distance_matrix))]
        
        # Calculate travel time with protection against infinity
        total_travel_time = 0
        for i in range(len(distance_matrix)-1):
            travel_time = distance_matrix[i][i+1]
            if math.isinf(travel_time):
                print(f"Warning: Infinite travel time in fallback between {i} and {i+1}")
                
                # Use more reasonable estimates based on the location type
                location_i = "" if i >= len(locations) else str(locations[i]).lower()
                location_i_plus_1 = "" if i+1 >= len(locations) else str(locations[i+1]).lower()
                
                if 'airport' in location_i or 'airport' in location_i_plus_1:
                    travel_time = 45  # 45 minutes for airport routes
                    print(f"Using estimated airport travel time of {travel_time} minutes")
                else:
                    travel_time = 60  # 1 hour for other routes
                    print(f"Using estimated travel time of {travel_time} minutes")
            total_travel_time += travel_time
        
        if mode_matrix:
            travel_modes = [mode_matrix[i][i+1] if i < len(distance_matrix)-1 else "" 
                          for i in range(len(distance_matrix)-1)]
        else:
            travel_modes = None
            
        return route, start_times, total_travel_time, travel_modes