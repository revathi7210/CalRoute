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
    
    # Schedule fixed-time tasks first with transit modes
    for task, location in tasks_with_locations:
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            # Find best transit mode based on distance from home to this location
            best_mode = default_mode
            
            # Force specific modes for certain keywords in the address or title
            if location:
                force_mode = None
                address_lower = location.address.lower() if location.address else ""
                title_lower = task.title.lower() if task.title else ""
                
                # Force driving for airport, long distances
                if 'airport' in address_lower or 'airport' in title_lower:
                    force_mode = 'car'
                    print(f"FORCING DRIVING MODE for airport location: {location.address}")
                elif any(keyword in address_lower for keyword in ['santa ana', 'tustin', 'newport', 'costa mesa']):
                    force_mode = 'car'
                    print(f"FORCING DRIVING MODE for distant location: {location.address}")
                
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
                                if distance < 1:  # Less than 1 km
                                    best_mode = 'walking' if 'walking' in user_transit_modes else default_mode
                                    print(f"Short distance ({distance:.2f} km): selecting walking mode")
                                elif distance < 5:  # Less than 5 km
                                    best_mode = 'bike' if 'bike' in user_transit_modes else default_mode
                                    print(f"Medium distance ({distance:.2f} km): selecting biking mode")
                                else:  # Over 5 km - driving
                                    best_mode = 'car' if 'car' in user_transit_modes else default_mode
                                    print(f"Long distance ({distance:.2f} km): selecting driving mode")
                            else:
                                print(f"No directions found for {task.title}, using default mode")
                    except Exception as e:
                        print(f"Error calculating distance for {task.title}: {e}")
                        # If we get an error, use driving for safety
                        if 'car' in user_transit_modes:
                            best_mode = 'car'
                            print(f"Error occurred - defaulting to driving mode for {task.title}")
            
            print(f"Final mode for {task.title}: {best_mode}")
            
            # Get user-friendly name for the transit mode
            display_mode = mode_display_names.get(best_mode, 'Driving')
            
            sched = ScheduledTask(
                user_id=user.user_id,
                raw_task_id=task.raw_task_id,
                title=task.title,
                description=task.description,
                location_id=task.location_id,
                scheduled_start_time=task.start_time,
                scheduled_end_time=task.end_time,
                priority=task.priority,
                travel_eta_minutes=0,
                transit_mode=display_mode  # Add transit mode
            )
            print(f"Fixed-time task {task.title} scheduled with transit mode: {display_mode}")
            db.session.add(sched)

    # Schedule flexible tasks based on the optimized route
    for i, idx in enumerate(route):
        if idx == 0 or idx == len(locations) - 1:
            continue

        task_idx = task_indices[idx - 1]
        raw, _ = tasks_with_locations[task_idx]
        dur = durations[idx]

        mins = start_times[idx]
        st = datetime.combine(today, time(mins // 60, mins % 60))
        et = st + timedelta(minutes=dur)
        
        # Get the transit mode for this task (from previous location)
        transit_mode = None
        if i > 0 and travel_modes and i-1 < len(travel_modes):  # Not the first task and within travel_modes bounds
            transit_mode = travel_modes[i-1]
            print(f"Task {raw.title}: Using transit mode {transit_mode}")
        else:
            print(f"Task {raw.title}: No transit mode available, using default")

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
        
        # Explicitly handle datetime objects to prevent 'not iterable' errors
        try:
            # Make sure start and end times are valid datetime objects
            if not isinstance(st, datetime) or not isinstance(et, datetime):
                print(f"Invalid datetime objects for task {raw.title} - st: {type(st)}, et: {type(et)}")
                # Use current time as fallback
                current_time = datetime.now()
                st = current_time
                et = current_time + timedelta(minutes=dur)
            
            sched = ScheduledTask(
                user_id=user.user_id,
                raw_task_id=raw.raw_task_id,
                title=raw.title,
                description=raw.description,
                location_id=raw.location_id,
                scheduled_start_time=st,
                scheduled_end_time=et,
                priority=raw.priority,
                travel_eta_minutes=0,
                transit_mode=display_mode  # Store the selected transit mode
            )
        except Exception as dt_err:
            print(f"Error handling datetime for task {raw.title}: {dt_err}")
            import traceback
            print(f"Datetime error traceback: {traceback.format_exc()}")
            # Create with default times
            now = datetime.now()
            sched = ScheduledTask(
                user_id=user.user_id,
                raw_task_id=raw.raw_task_id,
                title=raw.title,
                description=raw.description,
                location_id=raw.location_id,
                scheduled_start_time=now,
                scheduled_end_time=now + timedelta(minutes=dur),
                priority=raw.priority,
                travel_eta_minutes=0,
                transit_mode=display_mode
            )
        db.session.add(sched)

    db.session.commit()
    print("\n✅ Schedule optimized successfully.")
    print(f"Scheduled {len(route)-2} tasks")  # Subtract 2 for start/end depot
    return True