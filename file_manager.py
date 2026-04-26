import os
import re

# We'll save all files into this base folder as requested by user
LAB_BASE_PATH = r"E:\vraj\Projects\autoclassroom\lab"

def sanitize_folder_name(name):
    """Remove special characters that are invalid in Windows paths."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

def save_generated_files(assignment_title, files_json):
    """
    Creates a folder for the assignment and saves all generated files there.
    """
    if not files_json:
        print("No files to save.")
        return None
        
    folder_name = sanitize_folder_name(assignment_title)
    target_dir = os.path.join(LAB_BASE_PATH, folder_name)
    
    # Ensure directory exists
    os.makedirs(target_dir, exist_ok=True)
    
    print(f"Saving {len(files_json)} files to: {target_dir}")
    
    saved_files = []
    
    for file_obj in files_json:
        # Some LLMs might embed directories in the filename like "src/main.py".
        filename = file_obj.get("filename", "unknown.txt")
        # Ensure it doesn't traverse up
        filename = filename.replace("../", "").replace("..\\", "")
        
        file_path = os.path.join(target_dir, filename)
        
        # Ensure subdirectories exist for this specific file
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        content = file_obj.get("content", "")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        saved_files.append(file_path)
        print(f"Saved: {filename}")
        
    return target_dir
