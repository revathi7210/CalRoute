from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    # Relationships to related tables
    calendar_events = db.relationship('CalendarEvent', backref='user', lazy=True, cascade='all, delete-orphan')
    keep_notes = db.relationship('KeepNote', backref='user', lazy=True, cascade='all, delete-orphan')
    scheduled_tasks = db.relationship('ScheduledTask', backref='user', lazy=True, cascade='all, delete-orphan')
    locations = db.relationship('Location', backref='user', lazy=True, cascade='all, delete-orphan')

class CalendarEvent(db.Model):
    __tablename__ = 'calendar_events'
    event_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    location = db.Column(db.String(255))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)

class KeepNote(db.Model):
    __tablename__ = 'keep_notes'
    note_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    title = db.Column(db.String(255))
    content = db.Column(db.Text)
    created_time = db.Column(db.DateTime)
    labels = db.Column(db.JSON)

class ScheduledTask(db.Model):
    __tablename__ = 'scheduled_tasks'
    task_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    scheduled_start_time = db.Column(db.DateTime)
    scheduled_end_time = db.Column(db.DateTime)
    status = db.Column(db.Enum('pending', 'completed', 'cancelled'), default='pending')
    priority = db.Column(db.Integer)
    task_logs = db.relationship('TaskLog', backref='scheduled_task', lazy=True, cascade='all, delete-orphan')

class Location(db.Model):
    __tablename__ = 'locations'
    location_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    name = db.Column(db.String(100))
    address = db.Column(db.String(255))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

class TaskLog(db.Model):
    __tablename__ = 'task_logs'
    log_id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('scheduled_tasks.task_id', ondelete='CASCADE'))
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    action = db.Column(db.String(255))
    notes = db.Column(db.Text)