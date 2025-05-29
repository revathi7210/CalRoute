from flask_sqlalchemy import SQLAlchemy
from .extensions import db

# ---------- Association Tables ----------
# Multi-select for up to three transit modes per user preference
user_transit_modes = db.Table(
    'user_transit_modes',
    db.Column('pref_id', db.Integer, db.ForeignKey('user_preferences.pref_id', ondelete='CASCADE'), primary_key=True),
    db.Column('mode', db.String(20), db.ForeignKey('transit_mode_options.mode', ondelete='CASCADE'), primary_key=True)
)

# Multi-select for up to three favorite stores per user preference
user_favorite_stores = db.Table(
    'user_favorite_stores',
    db.Column('pref_id', db.Integer, db.ForeignKey('user_preferences.pref_id', ondelete='CASCADE'), primary_key=True),
    db.Column('location_id', db.Integer, db.ForeignKey('locations.location_id', ondelete='CASCADE'), primary_key=True)
)

# ---------- Core Entities ----------

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(100))
    google_access_token = db.Column(db.Text)
    todoist_token = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    raw_tasks = db.relationship('RawTask', backref='user', lazy=True, cascade='all, delete-orphan')
    scheduled_tasks = db.relationship('ScheduledTask', backref='user', lazy=True, cascade='all, delete-orphan')
    user_preferences = db.relationship('UserPreference', backref='user', lazy=True, cascade='all, delete-orphan')
    user_habits = db.relationship('UserHabit', backref='user', lazy=True, cascade='all, delete-orphan')


class Location(db.Model):
    __tablename__ = 'locations'
    location_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)  # Added name column
    address = db.Column(db.String(255), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    __table_args__ = (
        db.UniqueConstraint('latitude', 'longitude', name='uq_location_coords'),
    )


class TransitModeOption(db.Model):
    __tablename__ = 'transit_mode_options'
    mode = db.Column(db.String(20), primary_key=True)
    # You can enforce choices in your app logic: 'car','bike','bus_train','walking','rideshare'
    def __repr__(self):
        return f"<TransitModeOption {self.mode}>"


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
    duration = db.Column(db.Integer, nullable=True)  # in minutes
    status = db.Column(db.Enum('not_completed', 'completed', name='task_status'), default='not_completed', nullable=False)
    imported_at = db.Column(db.DateTime, server_default=db.func.now())
    is_location_flexible = db.Column(db.Boolean, default=False)
    place_type = db.Column(db.String(100), nullable=True)

class UserPreference(db.Model):
    __tablename__ = 'user_preferences'
    pref_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)

    max_daily_hours = db.Column(db.Float, default=8.0)
    work_start_time = db.Column(db.Time, nullable=True)
    work_end_time = db.Column(db.Time, nullable=True)

    prioritization_style = db.Column(
        db.Enum('important_first', 'quick_wins', 'balanced', name='prioritization_style'),
        default='balanced', nullable=False
    )

    # Multi-select relationships (enforce up to 3 in app logic)
    transit_modes = db.relationship(
        'TransitModeOption',
        secondary=user_transit_modes,
        collection_class=set,
        backref='user_preferences'
    )

    favorite_store_locations = db.relationship(
        'Location',
        secondary=user_favorite_stores,
        backref='favored_by_users'
    )

    # Home & Gym locations
    home_location_id = db.Column(db.Integer, db.ForeignKey('locations.location_id', ondelete='SET NULL'), nullable=True)
    gym_location_id = db.Column(db.Integer, db.ForeignKey('locations.location_id', ondelete='SET NULL'), nullable=True)

    home_location = db.relationship('Location', foreign_keys=[home_location_id], post_update=True, backref='home_for_preferences')
    gym_location = db.relationship('Location', foreign_keys=[gym_location_id], post_update=True, backref='gym_for_preferences')


class UserHabit(db.Model):
    __tablename__ = 'user_habits'
    habit_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    habit_type = db.Column(db.String(50))
    start_time = db.Column(db.Time)
    duration_minutes = db.Column(db.Integer)
    weight = db.Column(db.Float, default=1.0)


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
    priority = db.Column(db.Integer, default=3)
    travel_eta_minutes = db.Column(db.Float)
    transit_mode = db.Column(db.String(20), nullable=True)  # Store the selected transit mode
    created_at = db.Column(db.DateTime, server_default=db.func.now())
