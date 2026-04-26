document.getElementById('triggerBtn').addEventListener('click', async () => {
    const statusDiv = document.getElementById('status');
    statusDiv.textContent = "Extracting ID...";
    statusDiv.style.color = "#333";
    
    // Get the current tab
    chrome.tabs.query({active: true, currentWindow: true}, function(tabs) {
        let url = tabs[0].url;
        
        // Ensure it's a classroom assignment page
        // Format: https://classroom.google.com/[u/0/]c/{courseId}/a/{courseWorkId}/details
        let match = url.match(/\/c\/([^/]+)\/(a|m)\/([^/]+)/);
        
        if (!match) {
            statusDiv.textContent = "Error: Not on an assignment page. Make sure you are viewing the specific assignment details.";
            statusDiv.style.color = "red";
            return;
        }

        let courseId = match[1];
        let courseWorkId = match[3];

        statusDiv.textContent = "Sending to Python server...";

        fetch('http://127.0.0.1:5000/process_assignment', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                courseId: courseId,
                courseWorkId: courseWorkId
            })
        })
        .then(response => response.json())
        .then(data => {
            if(data.success) {
                statusDiv.textContent = "Success! " + data.message;
                statusDiv.style.color = "green";
            } else {
                statusDiv.textContent = "Error: " + data.error;
                statusDiv.style.color = "red";
            }
        })
        .catch((error) => {
            statusDiv.textContent = "Server Error. Is server.py running?";
            statusDiv.style.color = "red";
            console.error('Error:', error);
        });
    });
});
