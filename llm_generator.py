import os
import json
import re
from google import genai
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class CodeFile(BaseModel):
    filename: str
    content: str

SYSTEM_PROMPT = """
You are an expert programming assistant whose job is to read laboratory and assignment instructions, 
and generate the corresponding source code required to solve the assignment.

STRICT INSTRUCTION:
You MUST output ONLY a pure JSON array containing the code files. Do not output any conversational text or explanation. 
The JSON must follow this exact structure:
[
  {
    "filename": "hello.py",
    "content": "print('Hello World')\\n"
  }
]

CRITICAL: The `content` field MUST contain valid JSON strings. You MUST escape all newlines as `\\n` and double quotes as `\\"`. DO NOT use literal newlines inside the string values.
"""

def generate_code_from_instructions(assignment_title, instructions):
    """
    Sends the assignment instructions to Gemma and 
    returns a parsed JSON structure containing filenames and code content.
    """
    if not instructions.strip():
        print("No instructions found.")
        return []

    print("Sending context to Gemini...")
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty! Please paste your key in the .env file and save it.")
            
        # Initialize the new genai client
        client = genai.Client(api_key=api_key)
        
        user_prompt = f"{SYSTEM_PROMPT}\n\nAssignment Title: {assignment_title}\n\nHere are the instructions and requirements:\n\n{instructions}"
        
        response = client.models.generate_content(
            model='models/gemma-3-12b-it',
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.2,
            ),
        )
        
        # Gemma might wrap the JSON in markdown code blocks like ```json ... ```
        # We use regex to extract everything between the first [ and the last ]
        text = response.text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            clean_json = match.group(0)
        else:
            clean_json = text  # fallback

        files_json = json.loads(clean_json)
        return files_json
        
    except json.JSONDecodeError as e:
        print("Failed to parse output as JSON.")
        if hasattr(response, 'text'):
            print("Raw output:", response.text)
        return []
    except Exception as e:
        print(f"Error during LLM generation: {e}")
        return []

if __name__ == "__main__":
    # Small test
    test_title = "Lab 1: Python Basics"
    test_instructions = "Write a python script called main.py that prints 'Hello World'."
    res = generate_code_from_instructions(test_title, test_instructions)
    print(res)
