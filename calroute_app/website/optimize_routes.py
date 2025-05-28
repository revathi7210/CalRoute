# optimize_routes.py

from flask import Blueprint, session, request, jsonify
from datetime import datetime, timedelta, time, date
from .extensions import db
from .models import RawTask, ScheduledTask, Location, User, UserPreference
from .maps_utils import build_distance_matrix, solve_tsp, gmaps, GOOGLE_MAPS_API
import requests
import os

optimize_bp = Blueprint("optimize", __name__)

# ... (existing routes) ...

def run_optimization(user, current_lat=None, current_lng=None, sync_mode=False):
    """
    Run optimization for tasks based on different scenarios.

    Args:
        user: User object
        current_lat: Current latitude (optional)
        current_lng: Current longitude (optional)
        sync_mode: If True, fetches fresh data
    """
    print(f"Starting optimization for user {user.user_id}")
    print(f"Sync mode: {sync_mode}")
    
    from .views.calendar import fetch_google_calendar_events
    from .views.todoist import parse_and_store_tasks

    if sync_mode:
        print("Sync mode enabled - fetching fresh data from calendar and todoist")
        fetch_google_calendar_events(user)
        parse_and_store_tasks(user)
        db.session.commit()
        print("Data sync completed")

    tasks_with_locations = (
        db.session.query(RawTask, Location)
        .outerjoin(Location, RawTask.location_id == Location.location_id)
        .filter(
            RawTask.user_id == user.user_id,
            RawTask.status == 'not_completed'
        )
        .order_by(RawTask.start_time)
        .all()
    )

    print(f"Found {len(tasks_with_locations)} tasks with locations to optimize")
    
    if not tasks_with_locations:
        print("No tasks with locations found to optimize.")
        return False

    pref = UserPreference.query.filter_by(user_id=user.user_id).first()
    if not pref or not pref.home_location_id:
        print("User preferences or home location not set.")
        return False
        
    home_loc = Location.query.get(pref.home_location_id)
    if not home_loc:
        print("Home location not found in the database.")
        return False
    
    print(f"Using home location: {home_loc.address}")

    home_address = home_loc.address
    now = datetime.now()

    locations = []
    durations = []
    time_windows = []
    task_indices = []

    # Set default start time to 8am (480 minutes since midnight)
    default_start_time = 8 * 60  # 8:00 AM in minutes
    
    if current_lat and current_lng:
        current_loc = reverse_geocode_osm(current_lat, current_lng)
        locations.append(current_loc)
        durations.append(0)
        # If current time is after 8am, use current time, otherwise start at 8am
        current_minutes = now.hour * 60 + now.minute
        start_minutes = max(current_minutes, default_start_time)
        time_windows.append((start_minutes, 1440))
    else:
        locations.append(home_address)
        durations.append(0)
        time_windows.append((default_start_time, 1440))  # Start at 8am instead of midnight

    for i, (task, loc) in enumerate(tasks_with_locations):
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            continue

        locations.append(loc.address if loc else home_address)
        durations.append(task.duration if task.duration else 45)

        # If task has a start time, use it; otherwise use default 8am start
        default_start_time = 8 * 60  # 8:00 AM in minutes
        
        if task.start_time:
            start_min = task.start_time.hour * 60 + task.start_time.minute
        else:
            start_min = default_start_time
            
        end_min = task.end_time.hour * 60 + task.end_time.minute if task.end_time else 24 * 60
        time_windows.append((start_min, end_min))
        task_indices.append(i)

    locations.append(home_address)
    durations.append(0)
    # Allow return to home anytime after 8am
    default_start_time = 8 * 60  # 8:00 AM in minutes
    time_windows.append((default_start_time, 1440))

    # Fetch user's preferred transit modes from preferences
    user_transit_modes = {mode.mode for mode in pref.transit_modes}
    if not user_transit_modes:
        user_transit_modes = {'car'} # Default to car if no modes are set

    # Get both the distance matrix and mode matrix from build_distance_matrix
    distance_matrix, mode_matrix = build_distance_matrix(locations, modes=list(user_transit_modes))
    
    print("\n==== ROUTE OPTIMIZATION INPUTS ====")
    print(f"Locations: {locations}")
    print(f"Durations: {durations}")
    print(f"Time Windows: {time_windows}")
    print(f"Transit Modes: {user_transit_modes}")
    
    # Pass the mode_matrix to solve_tsp to get transit modes for each segment
    route, start_times, total_travel_time, travel_modes = solve_tsp(
        distance_matrix, 
        task_durations=durations, 
        time_windows=time_windows,
        mode_matrix=mode_matrix
    )
    
    print("\n==== ROUTE OPTIMIZATION OUTPUTS ====")
    print(f"Route: {route}")
    print(f"Start Times: {start_times}")
    print(f"Total Travel Time: {total_travel_time}")
    print(f"Travel Modes: {travel_modes}")
    
    if not route:
        print("\n❌ Could not find a solution.")
        return False
        
    print("\n✅ Solution found. Scheduling tasks...")

    ScheduledTask.query.filter_by(user_id=user.user_id).delete()
    db.session.commit()

    today = datetime.now().date()

    # Calculate travel times between consecutive locations
    travel_times = []
    print("\n==== CALCULATING TRAVEL TIMES ====")
    for i in range(len(route) - 1):
        from_idx = route[i]
        to_idx = route[i + 1]
        if from_idx < len(distance_matrix) and to_idx < len(distance_matrix[from_idx]):
            # Get the raw distance in seconds from the matrix
            raw_distance = distance_matrix[from_idx][to_idx]
            # Skip if it's the same location (inf or 0 distance)
            if raw_distance == float('inf') or raw_distance == 0:
                travel_time = 0
                print(f"Route segment {i}: {locations[from_idx]} -> {locations[to_idx]}")
                print(f"  Same location or invalid distance, setting travel time to 0")
            else:
                # Convert seconds to minutes
                travel_time = raw_distance / 60.0
                print(f"Route segment {i}: {locations[from_idx]} -> {locations[to_idx]}")
                print(f"  Raw distance: {raw_distance:.2f} seconds")
                print(f"  Travel time: {travel_time:.2f} minutes")
                print(f"  Mode: {travel_modes[i] if travel_modes and i < len(travel_modes) else 'unknown'}")
            travel_times.append(travel_time)
        else:
            travel_times.append(0)
            print(f"Warning: Invalid indices for travel time calculation: from_idx={from_idx}, to_idx={to_idx}")

    print(f"\nAll calculated travel times: {[f'{t:.2f} min' for t in travel_times]}")
    print(f"Total travel time from segments: {sum(travel_times):.2f} minutes")

    # Verify travel times are reasonable
    for i, t in enumerate(travel_times):
        if t > 0 and t < 1:  # If travel time is less than 1 minute
            print(f"Warning: Unrealistically short travel time ({t:.2f} min) for segment {i}")
            # Use Google Maps API to get a more accurate time if available
            if i < len(route) - 1 and GOOGLE_MAPS_API:
                try:
                    from_loc = locations[route[i]]
                    to_loc = locations[route[i + 1]]
                    mode = travel_modes[i] if travel_modes and i < len(travel_modes) else 'driving'
                    print(f"Fetching accurate travel time from Google Maps for {from_loc} to {to_loc}")
                    directions = gmaps.directions(from_loc, to_loc, mode=mode)
                    if directions and len(directions) > 0:
                        accurate_time = directions[0]['legs'][0]['duration']['value'] / 60.0
                        print(f"Google Maps travel time: {accurate_time:.2f} minutes")
                        travel_times[i] = accurate_time
                except Exception as e:
                    print(f"Error getting accurate travel time: {e}")

    # Map internal mode names to user-friendly names
    mode_display_names = {
        'car': 'Driving',
        'bike': 'Biking',
        'bus_train': 'Public Transit',
        'walking': 'Walking',
        'rideshare': 'Rideshare'
    }
    
    # Choose a default transit mode (first one available)
    default_mode = list(user_transit_modes)[0] if user_transit_modes else 'car'
    default_display_mode = mode_display_names.get(default_mode, 'Driving')
    print(f"Using default transit mode: {default_display_mode}")
    
    # Initialize variables for tracking previously scheduled tasks
    prev_task_end_time = None
    prev_task_location = None
    
    # Schedule fixed-time tasks first with transit modes
    for task, location in tasks_with_locations:
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            # Find best transit mode based on distance from home to this location
            best_mode = default_mode
            
            # Add buffer time calculation for fixed-time tasks
            travel_warning = None
            if prev_task_end_time and location and prev_task_location:
                try:
                    # Calculate travel time between previous task and this one
                    if GOOGLE_MAPS_API:
                        api_mode = best_mode
                        if api_mode == 'bike':
                            api_mode = 'bicycling'
                        elif api_mode == 'car':
                            api_mode = 'driving'
                            
                        directions = gmaps.directions(
                            prev_task_location.address,
                            location.address,
                            mode=api_mode
                        )
                        if directions and len(directions) > 0:
                            # Calculate required travel time
                            travel_time_minutes = directions[0]['legs'][0]['duration']['value'] // 60
                            
                            # Add buffer time for transitions between tasks (10 minutes)
                            buffer_time = 10
                            total_required_time = travel_time_minutes + buffer_time
                            
                            # Calculate available time between tasks
                            available_minutes = (task.start_time - prev_task_end_time).total_seconds() / 60
                            
                            # Check if enough time for travel + buffer
                            if available_minutes < total_required_time:
                                travel_warning = (f"⚠️ Warning: Only {int(available_minutes)} minutes available between tasks, " +
                                                f"but need {total_required_time} minutes ({travel_time_minutes} travel + {buffer_time} buffer)")
                                print(travel_warning)
                except Exception as e:
                    print(f"Error calculating travel time between tasks: {e}")
            
            # Force specific modes for certain keywords in the address or title
            if location:
                force_mode = None
                address_lower = location.address.lower() if location.address else ""
                title_lower = task.title.lower() if task.title else ""
                
                # Force rideshare or car for airport locations
                if 'airport' in address_lower or 'airport' in title_lower:
                    if 'rideshare' in user_transit_modes:
                        force_mode = 'rideshare'
                        print(f"FORCING RIDESHARE MODE for airport location: {location.address}")
                    elif 'car' in user_transit_modes:
                        force_mode = 'car'
                        print(f"FORCING DRIVING MODE for airport location: {location.address}")
                # Special handling for distant locations
                elif any(keyword in address_lower for keyword in ['santa ana', 'tustin', 'newport', 'costa mesa']):
                    if 'rideshare' in user_transit_modes:
                        force_mode = 'rideshare'
                        print(f"Using rideshare for distant location: {location.address}")
                    elif 'car' in user_transit_modes:
                        force_mode = 'car'
                        print(f"Using car for distant location: {location.address}")
                    elif 'bus_train' in user_transit_modes:
                        force_mode = 'bus_train'
                        print(f"Using public transit for distant location: {location.address}")
                
                if force_mode:
                    if force_mode in user_transit_modes:
                        best_mode = force_mode
                    print(f"Applied forced mode {force_mode} for {task.title} at {location.address}")
                else:
                    # Calculate distance from home to this location
                    try:
                        # Use direct Google Maps API call for this specific route
                        if GOOGLE_MAPS_API:
                            directions = gmaps.directions(
                                home_address,
                                location.address,
                                mode="driving"  # Just to get distance
                            )
                            if directions and len(directions) > 0:
                                # Extract distance in meters
                                distance_meters = directions[0]['legs'][0]['distance']['value']
                                distance = distance_meters / 1000  # km
                                
                                print(f"Distance calculation result for {task.title}: {distance:.2f} km")
                                
                                # Choose appropriate mode based on distance
                                if distance < 1 and 'walking' in user_transit_modes:  # Less than 1 km
                                    best_mode = 'walking'
                                    print(f"Short distance ({distance:.2f} km): selecting walking mode")
                                elif distance < 5 and 'bike' in user_transit_modes:  # Less than 5 km
                                    best_mode = 'bike'
                                    print(f"Medium distance ({distance:.2f} km): selecting biking mode")
                                elif distance < 12 and 'bus_train' in user_transit_modes:  # Less than 12 km
                                    best_mode = 'bus_train'
                                    print(f"Medium-long distance ({distance:.2f} km): selecting public transit mode")
                                elif 'rideshare' in user_transit_modes:  # Longer distances
                                    best_mode = 'rideshare'
                                    print(f"Long distance ({distance:.2f} km): selecting rideshare mode")
                                elif 'car' in user_transit_modes:  # If car is available
                                    best_mode = 'car'
                                    print(f"Long distance ({distance:.2f} km): selecting driving mode")
                                else:  # Fallback to first available mode
                                    best_mode = default_mode
                                    print(f"Using default mode {best_mode} for {distance:.2f} km distance")
                            else:
                                print(f"No directions found for {task.title}, using default mode")
                    except Exception as e:
                        print(f"Error calculating distance for {task.title}: {e}")
                        # If we get an error, use a fallback mode from user preferences
                        if 'car' in user_transit_modes:
                            best_mode = 'car'
                            print(f"Error occurred - using car mode for {task.title}")
                        elif 'rideshare' in user_transit_modes:
                            best_mode = 'rideshare'
                            print(f"Error occurred - using rideshare mode for {task.title}")
                        else:
                            # Use first available mode as fallback
                            best_mode = list(user_transit_modes)[0]
                            print(f"Error occurred - using {best_mode} mode for {task.title}")
            
            print(f"Final mode for {task.title}: {best_mode}")
            
            # Get user-friendly name for the transit mode
            display_mode = mode_display_names.get(best_mode, 'car')
            
            # Calculate travel time for this task
            travel_time = 0
            if GOOGLE_MAPS_API:
                try:
                    print(f"\nCalculating travel time for fixed task: {task.title}")
                    print(f"From: {home_address}")
                    print(f"To: {location.address}")
                    print(f"Mode: {best_mode}")
                    
                    # Ensure we're using a valid mode for Google Maps
                    gmaps_mode = best_mode
                    if best_mode == 'bike':
                        gmaps_mode = 'bicycling'  # Google Maps uses 'bicycling' instead of 'bike'
                    elif best_mode == 'bus_train':
                        gmaps_mode = 'transit'    # Google Maps uses 'transit' instead of 'bus_train'
                    
                    directions = gmaps.directions(
                        home_address,
                        location.address,
                        mode=gmaps_mode
                    )
                    if directions and len(directions) > 0:
                        raw_duration = directions[0]['legs'][0]['duration']['value']
                        travel_time = raw_duration / 60.0  # Convert to minutes
                        print(f"Raw duration: {raw_duration} seconds")
                        print(f"Calculated travel time: {travel_time:.2f} minutes")
                    else:
                        print("No directions found, using fallback calculation")
                        # Fallback to distance-based calculation if no directions
                        if location and home_loc:
                            # Rough estimate: 1 km = 2 minutes by car, 4 minutes by bike, 12 minutes walking
                            distance_km = ((location.latitude - home_loc.latitude) ** 2 + 
                                         (location.longitude - home_loc.longitude) ** 2) ** 0.5 * 111
                            if best_mode == 'car':
                                travel_time = distance_km * 2
                            elif best_mode == 'bike':
                                travel_time = distance_km * 4
                            else:  # walking
                                travel_time = distance_km * 12
                            print(f"Fallback travel time: {travel_time:.2f} minutes")
                except Exception as e:
                    print(f"Error calculating travel time for {task.title}: {e}")
                    # Use fallback calculation on error
                    if location and home_loc:
                        distance_km = ((location.latitude - home_loc.latitude) ** 2 + 
                                     (location.longitude - home_loc.longitude) ** 2) ** 0.5 * 111
                        travel_time = distance_km * 2  # Default to car speed
                        print(f"Fallback travel time after error: {travel_time:.2f} minutes")
            
            sched = ScheduledTask(
                user_id=user.user_id,
                raw_task_id=task.raw_task_id,
                title=task.title,
                description=task.description,
                location_id=task.location_id,
                scheduled_start_time=task.start_time,
                scheduled_end_time=task.end_time,
                priority=task.priority,
                travel_eta_minutes=travel_time,
                transit_mode=best_mode  # Store canonical value
            )
            print(f"Fixed-time task {task.title} scheduled with transit mode: {display_mode}")
            db.session.add(sched)
            
            # Update previous task info for next iteration
            prev_task_end_time = task.end_time
            prev_task_location = location

    # Schedule flexible tasks based on the optimized route
    for i, idx in enumerate(route):
        if idx == 0 or idx == len(locations) - 1:
            continue

        task_idx = task_indices[idx - 1]
        raw, _ = tasks_with_locations[task_idx]
        dur = durations[idx]

        mins = start_times[idx]
        suggested_start = datetime.combine(today, time(mins // 60, mins % 60))
        
        # Add buffer time logic for transitions between tasks
        buffer_minutes = 15  # Buffer time between tasks (15 minutes for transitions)
        
        # If we have a previous task and travel time, ensure enough time for travel + buffer
        if i > 0 and travel_times and i-1 < len(travel_times):
            travel_time_mins = travel_times[i-1]
            
            # Find the previously scheduled task (if any)
            prev_end_time = None
            for prev_sched in db.session.query(ScheduledTask).filter_by(user_id=user.user_id).all():
                if prev_sched.scheduled_end_time:
                    if not prev_end_time or prev_sched.scheduled_end_time > prev_end_time:
                        prev_end_time = prev_sched.scheduled_end_time
            
            if prev_end_time:
                # Calculate earliest possible start time: previous end + travel time + buffer
                total_transition_time = travel_time_mins + buffer_minutes
                earliest_possible_start = prev_end_time + timedelta(minutes=total_transition_time)
                
                # If suggested start is earlier than possible considering travel + buffer, adjust it
                if suggested_start < earliest_possible_start:
                    print(f"Adjusting start time for {raw.title} to account for travel + buffer:")
                    print(f"  Original: {suggested_start.strftime('%H:%M')}")
                    print(f"  New: {earliest_possible_start.strftime('%H:%M')} (+{total_transition_time} min: {travel_time_mins} travel + {buffer_minutes} buffer)")
                    suggested_start = earliest_possible_start
        
        # Set final start and end times
        st = suggested_start
        et = st + timedelta(minutes=dur)
        
        # Get the transit mode for this task (from previous location)
        transit_mode = None
        
        # Check if this task is at an airport location
        loc = Location.query.filter_by(location_id=raw.location_id).first()
        if loc and ('airport' in loc.address.lower() or 'airport' in raw.title.lower()):
            if 'rideshare' in user_transit_modes:
                transit_mode = 'rideshare'
                print(f"Airport location detected for {raw.title}: using rideshare")
            elif 'car' in user_transit_modes:
                transit_mode = 'car'
                print(f"Airport location detected for {raw.title}: using car")
        # Otherwise use the transit mode from the solver
        elif i > 0 and travel_modes and i-1 < len(travel_modes):  # Not the first task and within travel_modes bounds
            transit_mode = travel_modes[i-1]
            print(f"Task {raw.title}: Using transit mode {transit_mode}")
        else:
            print(f"Task {raw.title}: No transit mode available, using default")
            
        # If we have locations for previous and current tasks, calculate distance to make a better mode decision
        if not transit_mode and i > 0 and raw.location_id:
            prev_idx = route[i-1]
            if prev_idx > 0 and prev_idx < len(locations):
                try:
                    if GOOGLE_MAPS_API:
                        # Get distance between locations
                        directions = gmaps.directions(
                            locations[prev_idx],
                            loc.address,
                            mode="driving"  # Just to get distance
                        )
                        if directions and len(directions) > 0:
                            distance_meters = directions[0]['legs'][0]['distance']['value']
                            distance = distance_meters / 1000  # km
                            print(f"Distance for {raw.title}: {distance:.2f} km")
                            
                            # Choose mode based on distance
                            if distance < 1 and 'walking' in user_transit_modes:
                                transit_mode = 'walking'
                                print(f"Short distance ({distance:.2f} km): using walking")
                            elif distance < 5 and 'bike' in user_transit_modes:
                                transit_mode = 'bike'
                                print(f"Medium distance ({distance:.2f} km): using biking")
                            elif distance < 12 and 'bus_train' in user_transit_modes:
                                transit_mode = 'bus_train'
                                print(f"Medium-long distance ({distance:.2f} km): using public transit")
                            elif 'rideshare' in user_transit_modes:
                                transit_mode = 'rideshare'
                                print(f"Long distance ({distance:.2f} km): using rideshare")
                            elif 'car' in user_transit_modes:
                                transit_mode = 'car'
                                print(f"Long distance ({distance:.2f} km): using driving")
                            else:
                                # Fallback to first available mode
                                transit_mode = default_mode
                                print(f"Using default mode {transit_mode} for {distance:.2f} km distance")
                except Exception as e:
                    print(f"Error determining distance-based mode: {e}")

        # Get travel time for this task
        travel_time = travel_times[i-1] if i > 0 and i-1 < len(travel_times) else 0
        print(f"\nFlexible task: {raw.title}")
        print(f"Using travel time: {travel_time:.2f} minutes")
        print(f"Transit mode: {transit_mode if transit_mode else default_mode}")
        print(f"Scheduled time: {st.strftime('%H:%M')} - {et.strftime('%H:%M')} (includes {buffer_minutes} min buffer for transitions)")
        
        # Map internal mode names to user-friendly names
        mode_display_names = {
            'car': 'Driving',
            'bike': 'Biking',
            'bus_train': 'Public Transit',
            'walking': 'Walking',
            'rideshare': 'Rideshare'
        }
        
        # Get user-friendly name for the transit mode
        display_mode = mode_display_names.get(transit_mode, 'Driving') if transit_mode else 'Driving'
        
        sched = ScheduledTask(
            user_id=user.user_id,
            raw_task_id=raw.raw_task_id,
            title=raw.title,
            description=raw.description,
            location_id=raw.location_id,
            scheduled_start_time=st,
            scheduled_end_time=et,
            priority=raw.priority,
            travel_eta_minutes=travel_time,
            transit_mode=transit_mode if transit_mode else default_mode  # Store canonical value but respect user preferences
        )
        db.session.add(sched)

    db.session.commit()
    print("\n✅ Schedule optimized successfully.")
    print(f"Scheduled {len(route)-2} tasks")  # Subtract 2 for start/end depot
    return True