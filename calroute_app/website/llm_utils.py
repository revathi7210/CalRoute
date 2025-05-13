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
        f"""
You are a helpful assistant for a personal productivity app. Your job is to assign a real-world location to user tasks.

Task: "{task_text}"

Rules:
1. Do not reply with "unknown", "none", or "not specified".
2. If the task clearly mentions a location, use that.
3. If the task does not mention a location, suggest the most common or likely place where such a task would happen a real life location name or company name (example: "buy groceries" → "local supermarket").
4. Return only the place name, not a sentence.

What is the most appropriate location for this task?
"""
    )

    suggested = response.text.strip().lower()
    return None if suggested == 'none' else suggested