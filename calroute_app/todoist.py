from todoist_api_python.api import TodoistAPI
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

def split_content(content, chunk_size=500):
    return [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]

# Initialize the API
api = TodoistAPI("3f1ff07195dbbff57f579d47f2b4b936d9a4e06e")

template = (
    "You are tasked with extracting specific information from the following text content: {dom_content}. "
    "Please follow these instructions carefully: \n\n"
    "1. **Extract Information:** Only extract the information that directly matches the provided description: {parse_description}. \n"
    "2. **No Extra Content:** Do not include any additional text, comments, or explanations in your response. \n"
    "3. **Empty Response:** If no information matches the description, return an empty string ('').\n"
    "4. **Direct Data Only:** Your output should contain only the data that is explicitly requested, with no other text.\n"
)

model = OllamaLLM(model="llama3")

def parse_ollama(dom_chunks, parse_description):
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | model

    parsed_results = []

    for i , chunks in enumerate(dom_chunks, start = 1):
        response = chain.invoke({"dom_content": chunks, "parse_description": parse_description})

        print(f"Parsed batch {i} of {len(dom_chunks)}")
        parsed_results.append(response)

    return "\n".join(parsed_results)

try:
    # Fetch tasks
    tasks = api.get_tasks()
    
    if not tasks:
        print("No tasks found.")
        exit()

    # Prepare content for parsing
    dom_content = ""
    for task in tasks:
        task_name = task.content
        task_due = task.due.string if task.due else "None"
        dom_content += f"{task_name} at {task_due}\n"
    

    # Description for the LLM
    parse_description = (
    """For each line of the content, extract the following:\n
    • task: what needs to be done\n
    • location: place involved (if mentioned)\n
    • date: when it happens (use format 'DD Mon', e.g., '14 Apr') or 'none'\n
    • time: time of day (e.g., '16:00') or 'none'\n\n
    Return one line per task in this exact format:\n
    "task=..., location=..., date=..., time=...\n\n"
    If a value is not mentioned, use 'none'. Only return this structured output — no explanation or extra text.
    Example:
    Input: go to Starbucks at 8 Apr 15:00
    Output: task=go to Starbucks, location=Starbucks, date=8 Apr, time=15:00

    Input: Learn DSA
    Output: task=Learn DSA, location=none, date=none, time=none"""
    )

    print("Parsing content using AI...")

    # Split into chunks and process each chunk via local LLM
    chunks = split_content(dom_content)
    results = []

    response = parse_ollama(chunks, parse_description)
    results.append(response)

    print("\nFinal Combined Results:\n")
    print("\n".join(results))

except Exception as error:
    print("Error fetching tasks:", error)
