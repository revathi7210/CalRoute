from flask_sqlalchemy import SQLAlchemy
from .extensions import db

# ---------- Core Entities ----------
class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(100))
    google_access_token = db.Column(db.Text)
    todoist_token = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    raw_tasks = db.relationship('RawTask', backref='user', lazy=True, cascade='all, delete-orphan')
    scheduled_tasks = db.relationship('ScheduledTask', backref='user', lazy=True, cascade='all, delete-orphan')
    user_preferences = db.relationship('UserPreference', backref='user', lazy=True, cascade='all, delete-orphan')
    user_habits = db.relationship('UserHabit', backref='user', lazy=True, cascade='all, delete-orphan')

# ---------- Locations ----------

class Location(db.Model):
    __tablename__ = 'locations'
    location_id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(255))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    __table_args__ = (
        db.UniqueConstraint('latitude', 'longitude', name='uq_location_coords'),
    )

# ---------- Raw Tasks ----------

class RawTask(db.Model):
    __tablename__ = 'raw_tasks'
    raw_task_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    source = db.Column(db.Enum('google_calendar', 'todoist', 'manual', name='task_source'), nullable=False)
    external_id = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.location_id', ondelete='SET NULL'), nullable=True)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    priority = db.Column(db.Integer, default=3)
    duration = db.Column(db.Integer, nullable=True)  # Duration in minutes
    status = db.Column(db.Enum('not_completed', 'completed', name='task_status'), default='not_completed', nullable=False)
    imported_at = db.Column(db.DateTime, server_default=db.func.now())


# ---------- User Preferences ----------

class UserPreference(db.Model):
    __tablename__ = 'user_preferences'
    pref_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))

    max_daily_hours = db.Column(db.Float, default=8.0)
    work_start_time = db.Column(db.Time, nullable=True)
    work_end_time = db.Column(db.Time, nullable=True)

    travel_mode = db.Column(
        db.Enum('car', 'bike', 'bus_train', 'walking', 'rideshare',
                name='travel_mode'),
        default='car', nullable=True
    )

    prioritization_style = db.Column(
        db.Enum('important_first', 'quick_wins', 'balanced',
                name='prioritization_style'),
        default='balanced', nullable=False
    )

    # NEW fields to link to Location table
    home_location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.location_id', ondelete='SET NULL'),
        nullable=True
    )
    favorite_store_location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.location_id', ondelete='SET NULL'),
        nullable=True
    )

# ---------- User Habits ----------

class UserHabit(db.Model):
    __tablename__ = 'user_habits'
    habit_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    habit_type = db.Column(db.String(50))
    start_time = db.Column(db.Time)
    duration_minutes = db.Column(db.Integer)
    weight = db.Column(db.Float, default=1.0)

# ---------- Scheduled Tasks ----------

class ScheduledTask(db.Model):
    __tablename__ = 'scheduled_tasks'
    sched_task_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    raw_task_id = db.Column(db.Integer, db.ForeignKey('raw_tasks.raw_task_id', ondelete='SET NULL'), nullable=True)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.location_id', ondelete='SET NULL'), nullable=True)
    scheduled_start_time = db.Column(db.DateTime)
    scheduled_end_time = db.Column(db.DateTime)
    status = db.Column(db.Enum('pending', 'completed', 'cancelled', name='task_status'), default='pending')
    priority = db.Column(db.Integer, default=3)
    travel_eta_minutes = db.Column(db.Float)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
