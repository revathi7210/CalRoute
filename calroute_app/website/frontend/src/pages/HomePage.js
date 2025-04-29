// src/pages/HomePage.js
import React from 'react';
import './HomePage.css';

const HomePage = () => {
  const tasks = [
    { id: 1, title: 'Quick call John', time: '10:00 - 10:30' },
    { id: 2, title: 'Shopping at Albertsons', time: '11:00 - 12:00' },
    { id: 3, title: 'ML Class', time: '13:00 - 14:30' },
    { id: 4, title: 'Reply to spam emails', time: '15:00 - 15:30' },
  ];

  return (
    <div className="homepage">
      <div className="sidebar">
        <h3>CalRoute</h3>
        <nav>
          <ul>
            <li><a href="/homepage">Home</a></li>
            <li><a href="/profile">Profile</a></li>
            <li><a href="/calendar">Calendar</a></li>
            <li><a href="/routes">Routes</a></li>
            <li><a href="/manage-tasks">Manage Tasks</a></li>
          </ul>
        </nav>
      </div>

      <div className="main-content">
        <h1>Today's Schedule</h1>
        <ul className="task-list">
          {tasks.map(task => (
            <li key={task.id} className="task-item">
              <div>
                <h3>{task.title}</h3>
                <p>{task.time}</p>
              </div>
              <div className="task-actions">
                <button>Edit</button>
                <button>Completed</button>
                <button>Delete</button>
              </div>
            </li>
          ))}
        </ul>

        {/* Map container */}
        <div className="map-container">
          <h2>Task Locations</h2>
          <div id="map" style={{ width: '100%', height: '400px' }}></div>
        </div>
      </div>
    </div>
  );
};

export default HomePage;
