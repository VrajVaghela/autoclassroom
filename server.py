from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
from classroom_api import get_assignment_details
from llm_generator import generate_code_from_instructions
from file_manager import save_generated_files

app = Flask(__name__)
# Allow the Chrome extension to make cross-origin requests to this server
CORS(app)

def decode_classroom_id(encoded_id):
    if not encoded_id:
        return encoded_id
    try:
        padded_id = encoded_id + '=' * (-len(encoded_id) % 4)
        decoded = base64.b64decode(padded_id).decode('utf-8')
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return encoded_id

@app.route('/process_assignment', methods=['POST'])
def process_assignment():
    data = request.json
    course_id = decode_classroom_id(data.get('courseId'))
    coursework_id = decode_classroom_id(data.get('courseWorkId'))
    
    if not course_id or not coursework_id:
        return jsonify({"error": "courseId and courseWorkId are required."}), 400
        
    try:
        print(f"--- Processing Course: {course_id}, Assigment: {coursework_id} ---")
        # 1. Fetch details
        title, instructions = get_assignment_details(course_id, coursework_id)
        if not instructions:
            return jsonify({"error": "Failed to fetch instructions or assignment is empty."}), 400
            
        print(f"Assignment fetched: {title}")
        print("Generating code via LLM...")
        
        # 2. Generate Code
        files_json = generate_code_from_instructions(title, instructions)
        if not files_json:
            return jsonify({"error": "LLM failed to generate proper code format."}), 500
            
        # 3. Save to disk
        target_dir = save_generated_files(title, files_json)
        
        return jsonify({"success": True, "message": f"Saved {len(files_json)} files to {target_dir}"})
        
    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("Starting AutoClassroom Server on http://localhost:5000")
    # Setting use_reloader=False stops it from double-triggering auth flow in dev
    app.run(port=5000, debug=True, use_reloader=False)
