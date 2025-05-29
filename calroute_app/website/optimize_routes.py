# optimize_routes.py

from flask import Blueprint, session, request, jsonify
from datetime import datetime, timedelta, time, date
from .extensions import db
from .models import RawTask, ScheduledTask, Location, User, UserPreference
from .maps_utils import build_distance_matrix, solve_tsp, gmaps, GOOGLE_MAPS_API
import requests
import os
from flask import current_app

optimize_bp = Blueprint("optimize", __name__)

def check_and_adjust_time(start_time, end_time, scheduled_slots, task_name, buffer_minutes=15):
    """
    Check if a proposed time slot conflicts with any existing scheduled slots.
    If conflict found, adjusts the time to avoid overlap, and clamps to today.
    
    Args:
        start_time: Proposed start datetime for the task
        end_time:   Proposed end datetime for the task
        scheduled_slots: List of tuples (start, end, name) of already scheduled tasks
        task_name: Name of the task being scheduled
        buffer_minutes: Buffer time between tasks in minutes
        
    Returns:
        Tuple of (adjusted_start_time, adjusted_end_time, was_adjusted)
    """
    # 1) Clamp any "tomorrow" back into today at latest 23:59
    today = start_time.date()
    end_of_day = datetime.combine(today, time(23, 59))
    was_adjusted = False

    if start_time > end_of_day:
        # push the slot to end exactly at 23:59
        duration = (end_time - start_time).total_seconds() / 60
        end_time = end_of_day
        start_time = end_of_day - timedelta(minutes=duration)
        was_adjusted = True
        print(f"⚠️  Clamped {task_name} to today's end: {start_time.time()}–{end_time.time()}")

    # Sort existing slots by start time
    sorted_slots = sorted(scheduled_slots, key=lambda x: x[0])

    for slot_start, slot_end, slot_name in sorted_slots:
        # 2) Check for overlap
        if start_time < slot_end and end_time > slot_start:
            print(f"⚠️  Conflict: {task_name} ({start_time.time()}–{end_time.time()}) "
                  f"with {slot_name} ({slot_start.time()}–{slot_end.time()})")
            
            # 3) Push start past that slot + buffer
            duration = (end_time - start_time).total_seconds() / 60
            start_time = slot_end + timedelta(minutes=buffer_minutes)
            end_time   = start_time + timedelta(minutes=duration)
            was_adjusted = True
            print(f"↪️  Adjusted to {start_time.time()}–{end_time.time()}")

            # 4) **Re-clamp** if that pushed us past midnight
            if start_time.date() != today or start_time > end_of_day:
                # same clamp logic as above
                end_time = end_of_day
                start_time = end_of_day - timedelta(minutes=duration)
                print(f"⚠️  Post-adjust clamp for {task_name}: {start_time.time()}–{end_time.time()}")

            # 5) Recurse to check against earlier slots again
            return check_and_adjust_time(start_time, end_time, scheduled_slots, task_name, buffer_minutes)

    current_app.logger.info(f"Final adjusted time for {task_name}: {start_time.time()}–{end_time.time()}")    
    return start_time, end_time, was_adjusted

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
    
    # Track all scheduled time slots to prevent overlaps
    scheduled_slots = []
    
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
        # clamp end-of-day to 23:59 → minute 1439
        time_windows.append((start_minutes, 23*60 + 59))
    else:
        locations.append(home_address)
        durations.append(0)
        # clamp to 23:59 instead of 24:00
        time_windows.append((default_start_time, 23*60 + 59))
    # Priority list for tasks (lower number = higher priority)
    task_priorities = []
    
    for i, (task, loc) in enumerate(tasks_with_locations):
        locations.append(loc.address if loc else home_address)
        durations.append(task.duration if task.duration else 45)

        # If task has a start time, use it; otherwise use default 8am start
        default_start_time = 8 * 60  # 8:00 AM in minutes
        
        # Set priority based on source and time constraints
        if task.source == 'google_calendar' and task.start_time and task.end_time:
            # Google Calendar events with fixed times get highest priority (1)
            priority = 1
            start_min = task.start_time.hour * 60 + task.start_time.minute
            end_min = task.end_time.hour * 60 + task.end_time.minute
            print(f"Fixed time calendar event: {task.title} with window {start_min}-{end_min}")
        else:
            # Todoist tasks or flexible calendar tasks get lower priority (2)
            priority = 2
            if task.start_time:
                start_min = task.start_time.hour * 60 + task.start_time.minute
            else:
                start_min = default_start_time
                
            end_min = task.end_time.hour * 60 + task.end_time.minute if task.end_time else 24 * 60
        
        time_windows.append((start_min, end_min))
        task_priorities.append(priority)
        task_indices.append(i)

    locations.append(home_address)
    durations.append(0)
    # Allow return to home anytime after 8am
    default_start_time = 8 * 60  # 8:00 AM in minutes
    time_windows.append((default_start_time, 23*60 + 59))

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
    print(f"Task Priorities: {task_priorities}")
    print(f"Transit Modes: {user_transit_modes}")
    
    # Add priorities for home locations (start and end)
    full_priorities = [3]  # Start location (home/current) with lowest priority
    full_priorities.extend(task_priorities)
    full_priorities.append(3)  # End location (home) with lowest priority
    
    print(f"Full priorities including home locations: {full_priorities}")
    
    # Pass the mode_matrix and task_priorities to solve_tsp
    route, start_times, total_travel_time, travel_modes = solve_tsp(
        distance_matrix, 
        task_durations=durations, 
        time_windows=time_windows,
        mode_matrix=mode_matrix,
        task_priorities=full_priorities
    )
    
    # Calculate travel times between locations for each segment of the route
    travel_times = []
    for i in range(len(route) - 1):
        from_idx = route[i]
        to_idx = route[i + 1]
        if from_idx < len(distance_matrix) and to_idx < len(distance_matrix[0]):
            travel_time = distance_matrix[from_idx][to_idx]
            travel_times.append(travel_time)
            print(f"Travel leg {i}: From {from_idx} to {to_idx} = {travel_time} minutes")
        else:
            travel_times.append(0)
            print(f"Invalid indices for travel time calculation: {from_idx} to {to_idx}")
    
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
    #default_display_mode = mode_display_names.get(default_mode, 'Driving')
    print(f"\n💾 Available transit modes: {user_transit_modes}")
    print(f"🚗 Using default transit mode: {default_mode}")
    
    # Keep track of tasks we've already scheduled to prevent duplicates
    scheduled_task_ids = set()
    scheduled_slots = []  # For time conflict checking

    print(f"\nRoute: {route}")
    print(f"Task indices: {task_indices}")
    print(f"Locations: {locations}")

    # Schedule flexible tasks based on the optimized route
    for i, idx in enumerate(route):
        if idx == 0 or idx == len(locations) - 1:
            continue

        task_idx = task_indices[idx-1]
        raw, _ = tasks_with_locations[task_idx]
        dur = durations[idx]

        # Skip if we've already scheduled this task
        if raw.raw_task_id in scheduled_task_ids:
            print(f"Skipping duplicate task: {raw.title} (ID: {raw.raw_task_id})")
            continue
            
        # Mark this task as scheduled
        scheduled_task_ids.add(raw.raw_task_id)
        
        # Get the travel time for this leg of the journey
        travel_time = 0
        if i < len(travel_times):
            travel_time = travel_times[i]
            print(f"Using travel time {travel_time} minutes for task {raw.title}")

        # For Google Calendar events, use their original fixed times
        if raw.source == 'google_calendar' and raw.start_time and raw.end_time:
            print(f"Preserving original fixed time for calendar event: {raw.title}")
            st = datetime.combine(today, raw.start_time.time())
            et = datetime.combine(today, raw.end_time.time())
            current_app.logger.info(f"Unadjusted start time for {raw.title}: {st}")
            current_app.logger.info(f"Unadjusted end time for {raw.title}: {et}")
        else:
            # For flexible tasks, use the optimized times from TSP solver
            mins = start_times[idx]
            day_offset    = mins // (24*60)                    # how many whole days to skip
            minute_of_day = mins % (24*60)
            hour          = minute_of_day // 60
            minute        = minute_of_day % 60

            st = datetime.combine(today + timedelta(days=day_offset),time(hour, minute))
            et = st + timedelta(minutes=dur)
            current_app.logger.info(f"Unadjusted start time for {raw.title}: {st}")
            current_app.logger.info(f"Unadjusted end time for {raw.title}: {et}")
        
        # Check if this task conflicts with any previously scheduled tasks
        # and adjust the time if needed
        st, et, was_adjusted = check_and_adjust_time(st, et, scheduled_slots, raw.title)

        current_app.logger.info(f"Adjusted start time for {raw.title}: {st}")
        current_app.logger.info(f"Adjusted end time for {raw.title}: {et}")
        
        # Get the location for this task
        if raw.location_id:
            location = Location.query.filter_by(location_id=raw.location_id).first()
        
        # Calculate distance-based transit mode selection
        calculated_mode = travel_modes[i-1]
        print(f"!!!!!!!!!!Travel modes: {travel_modes}")
        print(f"Calculated mode: {calculated_mode}")
        
        # Special case: Always use car for airport tasks if available
        if raw.title and ('airport' in raw.title.lower()) and 'car' in user_transit_modes:
            calculated_mode = 'car'
            print(f"Using car for airport task: {raw.title}")
        elif location and GOOGLE_MAPS_API:
            try:
                # Calculate the distance to determine appropriate mode
                directions = gmaps.directions(
                    home_address,
                    location.address,
                    mode="driving"  # Just to get distance
                )
                
                if directions and len(directions) > 0:
                    # Extract distance in km
                    distance_km = directions[0]['legs'][0]['distance']['value'] / 1000
                    print(f"Distance for {raw.title}: {distance_km:.2f} km")
                    
                    # Distance-based mode selection using user preferences
                    # IMPORTANT: Print what mode we're selecting AND what the display name will be
                    if distance_km < 1 and 'walking' in user_transit_modes:
                        calculated_mode = 'walking'
                        print(f"Short distance ({distance_km:.2f} km): selecting walking mode for {raw.title}")
                        print(f"Display mode will be: {mode_display_names.get('walking', 'Walking')}")
                    # elif distance_km < 2 and 'bike' in user_transit_modes:
                    #     calculated_mode = 'bike'
                    #     print(f"Medium distance ({distance_km:.2f} km): selecting biking mode for {raw.title}")
                    #     print(f"Display mode will be: {mode_display_names.get('bike', 'Biking')}")
                    # elif distance_km < 15 and 'bus_train' in user_transit_modes:
                    #     calculated_mode = 'bus_train'
                    #     print(f"Medium-long distance ({distance_km:.2f} km): selecting public transit for {raw.title}")
                    #     print(f"Display mode will be: {mode_display_names.get('bus_train', 'Public Transit')}")
                    # elif 'car' in user_transit_modes:
                    #     calculated_mode = 'car'
                    #     print(f"Long distance ({distance_km:.2f} km): selecting driving mode for {raw.title}")
                        # print(f"Display mode will be: {mode_display_names.get('car', 'Driving')}")
            except Exception as e:
                print(f"Error calculating distance-based mode for {raw.title}: {e}")
        
        # Choose the final transit mode based on priority:
        # 1. Distance-based calculation (most accurate)
        # 2. The mode from TSP solution (good estimate)
        # 3. Default mode (fallback)
        if calculated_mode:
            transit_mode = calculated_mode
            print(f"Using distance-based transit mode for {raw.title}: {transit_mode}")
        elif travel_modes and i < len(travel_modes):
            transit_mode = travel_modes[i]
            print(f"Using TSP-recommended transit mode for {raw.title}: {transit_mode}")
        else:
            # If we couldn't determine a mode, use the default
            transit_mode = default_mode
            print(f"Using default transit mode for {raw.title}: {transit_mode}")
        
        # Fallback to default mode if no appropriate mode was found
        if not transit_mode:
            transit_mode = default_mode
            print(f"Task {raw.title}: Using default transit mode: {transit_mode}")

        # Map internal mode names to user-friendly names
        mode_display_names = {
            'car': 'Driving',
            'bike': 'Biking',
            'bus_train': 'Public Transit',
            'walking': 'Walking',
            'rideshare': 'Rideshare'
        }
        
        # For logging only - get user-friendly name for the transit mode
        display_mode = mode_display_names.get(transit_mode, 'Driving') if transit_mode else 'Driving'
        print(f"Task {raw.title}: Using transit mode {transit_mode} (displays as {display_mode})")
        
        # Explicitly handle datetime objects to prevent 'not iterable' errors
        try:
            # Make sure start and end times are valid datetime objects
            if not isinstance(st, datetime) or not isinstance(et, datetime):
                print(f"Invalid datetime objects for task {raw.title} - st: {type(st)}, et: {type(et)}")
                # Use current time as fallback
                current_time = datetime.now()
                current_app.logger.info(f"Using current time as fallback for task {raw.title}: {current_time.time()}")
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
                travel_eta_minutes=travel_time,  # Use the calculated travel time
                transit_mode=transit_mode  # Store the internal transit mode name
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
                travel_eta_minutes=travel_time,  # Use the calculated travel time
                transit_mode=transit_mode  # Store the internal transit mode name
            )
        db.session.add(sched)
        
        # Add this task to the scheduled slots to prevent future tasks from overlapping
        scheduled_slots.append((st, et, raw.title))
        # Keep the slots sorted by start time
        scheduled_slots.sort(key=lambda x: x[0])

    db.session.commit()
    print("\n✅ Schedule optimized successfully.")
    print(f"Scheduled {len(route)-2} tasks")  # Subtract 2 for start/end depot
    return True
