# CalRoute

An AI‑powered intelligent daily planner that automatically schedules your tasks and events by integrating calendar data, to‑do lists, and real‑time location inputs—optimizing your day for maximum efficiency.

---

## 🚀 Features

- **Natural‑Language Task Parsing**  
  Leverages a Large Language Model (LLM) to understand plain‑English task descriptions and infer intent (e.g., “Lunch with Sarah at noon”).

- **Hybrid Time‑Location Optimizer**  
  Uses Google Maps API + OR‑Tools to solve a Traveling Salesman Problem (TSP) with time windows, minimizing travel time and idle gaps.

- **Proximity‑Based Clustering & Rescheduling**  
  Dynamically reorders and batches nearby tasks; automatically adjusts your itinerary when delays or new tasks occur.

- **Multi‑Source Integration**  
  • Google Calendar & Todoist APIs for events & tasks  
  • Real‑time GPS or address lookup for location awareness  

- **Full‑Stack, Scalable Design**  
  • **Flask** backend with RESTful endpoints  
  • **React** frontend for real‑time UI updates  
  • **MySQL** for persistent storage  
  • **Docker** containers for local development & production  
  • Deployed on **AWS EC2** with auto‑scaling support

---

## 🛠️ Tech Stack

- **Backend**: Python 3.9+, Flask, SQLAlchemy, Celery  
- **Frontend**: React, React Router, Tailwind CSS  
- **Scheduling**: Google Maps API, Google Calendar API, Todoist API, OR‑Tools  
- **Data Storage**: MySQL (RDS), Redis (Celery broker)  
- **Infrastructure**: Docker, Docker Compose, AWS EC2, AWS Elastic Beanstalk  
- **NLP**: Gemini

