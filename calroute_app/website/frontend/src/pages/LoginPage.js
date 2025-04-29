// src/pages/LoginPage.js
import React from 'react';
import './LoginPage.css';  // Include styling for the page

const LoginPage = () => {
  return (
    <div className="login-page">
      <h1>Login</h1>
      <p>Click below to login with Google</p>

      {/* Google Login Button */}
      <button
        className="google-btn"
        onClick={() => window.location.href = '/login/google'} // Redirect to backend Google login route
      >
        Login with Google
      </button>
    </div>
  );
};

export default LoginPage;
