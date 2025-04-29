// src/pages/Homepage.js
import React, { useState } from 'react';
import './HomePage.css';  // Add CSS for the homepage

// Task Component for displaying individual task
const TaskItem = ({ task, onEdit, onDelete, onComplete }) => {
  return (
    <div className="task-item">
      <div className="task-info">
        <h3>{task.title}</h3>
        <p>{task.time}</p>
      </div>
      <div className="task-actions">
        <button onClick={() => onEdit(task.id)} className="edit-btn">Edit</button>
        <button onClick={() => onComplete(task.id)} className="complete-btn">Complete</button>
        <button onClick={() => onDelete(task.id)} className="delete-btn">Delete</button>
      </div>
    </div>
  );
};

// Homepage Component that renders tasks and allows interactions
const HomePage = () => {
  const [tasks, setTasks] = useState([
    { id: 1, title: 'Quick call John', time: '10:00 - 10:30' },
    { id: 2, title: 'Shopping at Albertsons', time: '11:00 - 12:00' },
    { id: 3, title: 'ML Class', time: '13:00 - 14:30' },
    { id: 4, title: 'Reply to spam emails', time: '15:00 - 15:30' }
  ]);

  // Edit task (placeholder logic)
  const handleEdit = (id) => {
    alert(`Editing task with ID: ${id}`);
  };

  // Mark task as completed
  const handleComplete = (id) => {
    alert(`Task with ID: ${id} marked as completed`);
  };

  // Delete task
  const handleDelete = (id) => {
    setTasks(tasks.filter(task => task.id !== id));
  };

  return (
    <div className="homepage">
      <h1>Your Tasks</h1>
      <div className="task-list">
        {tasks.map(task => (
          <TaskItem
            key={task.id}
            task={task}
            onEdit={handleEdit}
            onDelete={handleDelete}
            onComplete={handleComplete}
          />
        ))}
      </div>
    </div>
  );
};

export default HomePage;
