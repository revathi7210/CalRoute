CREATE DATABASE IF NOT EXISTS calroute_db;
/*USE calroute_db;

-- Users Table
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    phone_number VARCHAR(20) UNIQUE,
    location VARCHAR(255),
    preferences JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    due_date DATETIME,
    priority ENUM('LOW', 'MEDIUM', 'HIGH'),
    status ENUM('PENDING', 'COMPLETED', 'CANCELLED'),
    location VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Schedules Table
CREATE TABLE IF NOT EXISTS schedules (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    task_id INT NOT NULL,
    scheduled_time DATETIME,
    optimized_route_order INT,
    status ENUM('UPCOMING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- ️Routes Table
CREATE TABLE IF NOT EXISTS routes (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    task_id INT NOT NULL,
    origin VARCHAR(255),
    destination VARCHAR(255),
    travel_mode ENUM('WALKING', 'DRIVING', 'TRANSIT', 'BIKING'),
    estimated_time INT,
    distance FLOAT,
    route_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    message TEXT NOT NULL,
  	status ENUM('UNREAD', 'READ') DEFAULT 'UNREAD',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
 );

INSERT INTO users (full_name, email, phone_number, location, preferences)
VALUES 
('Alice Smith', 'alice@example.com', '1234567890', 'Irvine, CA', JSON_OBJECT('theme', 'dark')),
('Bob Johnson', 'bob@example.com', '0987654321', 'Los Angeles, CA', JSON_OBJECT('notifications', true));

INSERT INTO tasks (user_id, title, description, due_date, priority, status, location)
VALUES 
(1, 'Buy groceries', 'Buy fruits and vegetables', '2025-04-12 10:00:00', 'MEDIUM', 'PENDING', 'Trader Joes'),
(2, 'Meeting with client', 'Discuss Q2 plans', '2025-04-13 14:30:00', 'HIGH', 'PENDING', 'Downtown LA Office');

INSERT INTO schedules (user_id, task_id, scheduled_time, optimized_route_order, status)
VALUES
(1, 1, '2025-04-12 09:45:00', 1, 'UPCOMING'),
(2, 2, '2025-04-13 14:00:00', 1, 'UPCOMING');

INSERT INTO routes (user_id, task_id, origin, destination, travel_mode, estimated_time, distance, route_data)
VALUES
(1, 1, 'Irvine, CA', 'Trader Joes, Irvine', 'DRIVING', 15, 5.4, JSON_OBJECT('steps', 'Turn left at Main St, then right at Culver Dr')),
(2, 2, 'Santa Monica', 'Downtown LA', 'TRANSIT', 45, 18.2, JSON_OBJECT('steps', 'Take Metro E Line to 7th Street/Metro Center'));


INSERT INTO notifications (user_id, message, status)
VALUES 
(1, 'Your schedule is updated!', 'UNREAD'),
(2, 'Task reminder: Meeting with client at 2:30 PM', 'UNREAD');
*/