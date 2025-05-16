from flask import Blueprint, jsonify, session, current_app
from website.extensions import db
from website.models import User, ScheduledTask, Location
from website.views.calendar import fetch_google_calendar_events
from website.views.todoist import parse_and_store_tasks
from website.optimize_routes import run_optimization

tasks_bp = Blueprint('tasks', __name__)

@tasks_bp.route("/api/tasks", methods=["GET"])
def get_scheduled_tasks():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    #  If no scheduled tasks, run the full pipeline
    if ScheduledTask.query.filter_by(user_id=user_id).count() == 0:
        try:
            #fetch_google_calendar_events(user)
            #parse_and_store_tasks(user)
            run_optimization(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Task scheduling failed: {e}")
            return jsonify({"error": "Task scheduling failed"}), 500

    #  Get scheduled tasks + join with location
    results = (
        db.session.query(ScheduledTask, Location)
        .join(Location, ScheduledTask.location_id == Location.location_id)
        .filter(ScheduledTask.user_id == user_id)
        .order_by(ScheduledTask.scheduled_start_time)
        .all()
    )
    print(results)

    tasks = []
    for sched_task, location in results:
        if not location:
            continue
        tasks.append({
            "id": sched_task.sched_task_id,
            "title": sched_task.title,
            "start_time": sched_task.scheduled_start_time.strftime("%-I:%M %p"),
            "end_time": sched_task.scheduled_end_time.strftime("%-I:%M %p"),
            "lat": location.latitude,
            "lng": location.longitude,
        })

    return jsonify({"tasks": tasks})
