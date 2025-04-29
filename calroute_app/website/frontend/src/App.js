// src/App.js
import React from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';  // Import Routes instead of Switch
import HomePage from './pages/HomePage';  // Your Homepage component
import LoginPage from './pages/LoginPage';  // Your LoginPage component

function App() {
  // Redirect user to /login/google on Google login button click
  // const handleGoogleLogin = () => {
  //   // Sending POST request to backend to initiate Google login
  //   window.location.href = '/login/google';  // Redirect to backend Google login route
  // };

  return (
    <Router>
      <Routes>
        {/* Define routes for different pages */}
        <Route path="/" element={<LoginPage />} />
        <Route path="/schedule" element={<HomePage />} />
        {/* Add other routes here if necessary */}
      </Routes>
    </Router>
  );
}

export default App;
