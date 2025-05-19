from flask import Blueprint, jsonify, session, current_app, request
from website.extensions import db
from website.models import User, ScheduledTask, Location, RawTask
from website.views.calendar import fetch_google_calendar_events
from website.views.todoist import parse_and_store_tasks
from website.optimize_routes import run_optimization
from datetime import datetime

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
            fetch_google_calendar_events(user)
            parse_and_store_tasks(user)
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

@tasks_bp.route("/api/tasks", methods=["POST"])
def create_task():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or not data.get("title"):
        return jsonify({"error": "Title is required"}), 400

    # Create a new raw task
    raw_task = RawTask(
        user_id=user_id,
        source="todoist",
        external_id=f"manual-{datetime.now().timestamp()}",
        title=data.get("title"),
        description=data.get("description"),
        start_time=datetime.fromisoformat(data.get("start_time")) if data.get("start_time") else None,
        end_time=datetime.fromisoformat(data.get("end_time")) if data.get("end_time") else None,
        due_date=datetime.fromisoformat(data.get("due_date")) if data.get("due_date") else None,
        priority=data.get("priority", 3),
        raw_data={}
    )

    try:
        db.session.add(raw_task)
        db.session.commit()
        
        # Run optimization to schedule the new task
        user = User.query.get(user_id)
        run_optimization(user)
        
        return jsonify({"message": "Task created successfully", "task_id": raw_task.raw_task_id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@tasks_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    task = RawTask.query.filter_by(raw_task_id=task_id, user_id=user_id).first()
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Update task fields
    if "title" in data:
        task.title = data["title"]
    if "description" in data:
        task.description = data["description"]
    if "start_time" in data:
        task.start_time = datetime.fromisoformat(data["start_time"]) if data["start_time"] else None
    if "end_time" in data:
        task.end_time = datetime.fromisoformat(data["end_time"]) if data["end_time"] else None
    if "due_date" in data:
        task.due_date = datetime.fromisoformat(data["due_date"]) if data["due_date"] else None
    if "priority" in data:
        task.priority = data["priority"]

    try:
        db.session.commit()
        
        # Re-run optimization to update the schedule
        user = User.query.get(user_id)
        run_optimization(user)
        
        return jsonify({"message": "Task updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@tasks_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    task = RawTask.query.filter_by(raw_task_id=task_id, user_id=user_id).first()
    if not task:
        return jsonify({"error": "Task not found"}), 404

    try:
        db.session.delete(task)
        db.session.commit()
        
        # Re-run optimization to update the schedule
        user = User.query.get(user_id)
        run_optimization(user)
        
        return jsonify({"message": "Task deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
