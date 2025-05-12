// src/pages/ProfilePage.js
import React from 'react';
import './ProfilePage.css';

const ProfilePage = () => {
  return (
    <div className="profile-page">
      <div className="profile-sidebar">
        <h3>Profile</h3>
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

      <div className="profile-content">
        <h1>User Profile</h1>
        <div className="profile-info">
          <div className="profile-section">
            <h3>Full Name</h3>
            <p>John Doe</p>
          </div>
          <div className="profile-section">
            <h3>Email</h3>
            <p>john.doe@example.com</p>
          </div>
          <div className="profile-section">
            <h3>Location</h3>
            <p>New York, NY</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;
