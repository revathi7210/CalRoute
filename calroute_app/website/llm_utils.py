import google.generativeai as genai
from flask import current_app

def call_gemini_for_location(task_text):
    """
    Uses Gemini to predict a likely location for a task.
    """
    api_key = current_app.config.get('GOOGLE_GENAI_API_KEY')
    if not api_key:
        raise ValueError("GOOGLE_GENAI_API_KEY is not set in app config.")

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-1.5-flash-latest")
    response = model.generate_content(
        f"The following is a personal task: \"{task_text}\".\n"
        f"Predict the most likely place where this would happen (example: Starbucks, Gym, Pharmacy).\n"
        f"Respond with only the place name. If unsure, respond 'none'."
    )

    suggested = response.text.strip().lower()
    return None if suggested == 'none' else suggested
