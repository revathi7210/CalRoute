// src/pages/LoginPage.js
import React from 'react';
import './LoginPage.css';  // Include styling for the page

const LoginPage = () => {
  // Handle Google Login Button Click
  const handleGoogleLogin = () => {
    // Redirect directly to homepage for now (skipping backend)
    window.location.href = '/homepage';  // Redirect to homepage directly
  };

  return (
    <div className="login-page">
      <h1>Login</h1>
      <p>Click below to login with Google</p>

      {/* Google Login Button */}
      <button 
        className="google-btn"
        onClick={handleGoogleLogin} // This will redirect to the homepage directly
      >
        Login with Google
      </button>
    </div>
  );
};

export default LoginPage;