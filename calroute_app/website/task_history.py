import redis
import json
from datetime import datetime
from typing import List, Dict, Optional

class TaskHistoryManager:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.HISTORY_KEY_PREFIX = "user_task_history:"
        self.EXPIRY_DAYS = 30  # History will expire after 30 days

    def _get_user_history_key(self, user_id: str) -> str:
        """Generate Redis key for user's task history."""
        return f"{self.HISTORY_KEY_PREFIX}{user_id}"

    def add_completed_task(self, user_id: str, task_name: str, location: str) -> None:
        """
        Add a completed task to user's history.
        
        Args:
            user_id: The ID of the user
            task_name: Name of the completed task
            location: Location where the task was completed
        """
        task_data = {
            "task_name": task_name,
            "location": location,
            "completed_at": datetime.now().isoformat()
        }
        
        history_key = self._get_user_history_key(user_id)
        # Store task as JSON string in a list
        self.redis.rpush(history_key, json.dumps(task_data))
        # Set expiry on the key
        self.redis.expire(history_key, self.EXPIRY_DAYS * 24 * 60 * 60)

    def get_user_history(self, user_id: str, limit: Optional[int] = None) -> List[Dict]:
        """
        Get user's task history.
        
        Args:
            user_id: The ID of the user
            limit: Optional limit on number of tasks to return (most recent first)
            
        Returns:
            List of task dictionaries containing task_name, location, and completed_at
        """
        history_key = self._get_user_history_key(user_id)
        
        # Get all tasks if no limit specified, otherwise get the most recent ones
        if limit is None:
            tasks = self.redis.lrange(history_key, 0, -1)
        else:
            tasks = self.redis.lrange(history_key, -limit, -1)
        
        # Convert JSON strings back to dictionaries
        return [json.loads(task) for task in tasks]

    def clear_user_history(self, user_id: str) -> None:
        """
        Clear all task history for a user.
        
        Args:
            user_id: The ID of the user
        """
        history_key = self._get_user_history_key(user_id)
        self.redis.delete(history_key)

    def get_recent_locations(self, user_id: str, limit: int = 5) -> List[str]:
        """
        Get user's most recent task locations.
        
        Args:
            user_id: The ID of the user
            limit: Number of recent locations to return
            
        Returns:
            List of recent locations
        """
        history = self.get_user_history(user_id, limit=limit)
        return [task["location"] for task in history]