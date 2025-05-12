// src/pages/LandingPage.js
import React from 'react';
import { Link } from 'react-router-dom';

const LandingPage = () => {
  return (
    <div>
      <h1>Welcome to CalRoute</h1>
      <p>Your intelligent task scheduler and route optimizer</p>
      <Link to="/login">Login</Link>
    </div>
  );
};

export default LandingPage;
