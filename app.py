import os
import random
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename
from flask_cors import CORS 
from pymongo import MongoClient
from bson.objectid import ObjectId

# --- 1. CONFIGURATION ---
app = Flask(__name__)
CORS(app) 

# MongoDB Configuration: Fetch URI from environment variable for security and deployment
# Defaulting to the local URI for testing if the environment variable is not set
# NOTE: YOU MUST SET THIS ENVIRONMENT VARIABLE FOR ATLAS TO WORK!
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/') 
MONGO_DB_NAME = 'CampusCleanlinessDB'
MONGO_COLLECTION_NAME = 'incidents'

UPLOAD_FOLDER = 'uploads' 
os.makedirs(UPLOAD_FOLDER, exist_ok=True) 
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. DATABASE SETUP ---
def get_mongo_collection():
    """Initializes and returns the MongoDB collection object."""
    try:
        # Use the MONGO_URI set above (either local default or cloud variable)
        client = MongoClient(MONGO_URI)
        # Attempt to access a collection to check connectivity
        client.admin.command('ping') 
        db = client[MONGO_DB_NAME]
        print("INFO: Successfully connected to MongoDB.")
        return db[MONGO_COLLECTION_NAME]
    except Exception as e:
        print(f"ERROR: Could not connect to MongoDB. URI: {MONGO_URI}. Error: {e}")
        return None

# Global collection object (initialized once)
incidents_collection = get_mongo_collection()


# --- 3. HELPER: AI INFERENCE SIMULATION (Unchanged) ---
def run_ai_inference(image_path):
    import random
    types = ['Litter Detected', 'Overflowing Bin', 'Spill Detected', 'Scattered Trash', 'Debris Found', 'Graffiti']
    
    detection_type = random.choice(types)
    confidence = (85 + random.random() * 15)
    
    return {
        "detection_type": detection_type,
        "confidence": round(confidence, 1),
        "is_alert": (detection_type == 'Overflowing Bin' or detection_type == 'Spill Detected' or detection_type == 'Graffiti')
    }

# --- 4. API ENDPOINTS ---

@app.route('/')
def home():
    if incidents_collection is None:
        return "MongoDB Connection Failed. Check server logs and MONGO_URI environment variable.", 500
    return f"Campus Cleanliness Monitoring API is running (DB: {MONGO_DB_NAME})."

# Endpoint to receive image and run AI inference
@app.route('/api/detect_and_report', methods=['POST'])
def detect_and_report():
    if incidents_collection is None:
        return jsonify({"message": "Database unavailable"}), 503
        
    if 'image' not in request.files:
        return jsonify({"message": "No image file provided"}), 400

    file = request.files['image']
    location_id = request.form.get('location_id', 'Unknown Zone')
    
    # 1. Save the image evidence
    filename = secure_filename(f"{location_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # 2. Run AI Inference (Simulated)
    try:
        results = run_ai_inference(filepath)
    except Exception as e:
        return jsonify({"message": f"AI model inference failed: {e}"}), 500

    # 3. Log incident to the MongoDB database
    try:
        incident_doc = {
            "detection_type": results['detection_type'],
            "confidence": results['confidence'],
            "location_id": location_id,
            "timestamp": datetime.now(), 
            "status": "Unresolved",
            "evidence_path": filepath
        }
        
        insert_result = incidents_collection.insert_one(incident_doc)
        incident_id = str(insert_result.inserted_id) 
        
    except Exception as e:
        return jsonify({"message": f"MongoDB logging failed: {e}"}), 500
    
    # 4. Return results to the dashboard
    return jsonify({
        "success": True,
        "incident_id": incident_id,
        "location": location_id,
        "detection_type": results['detection_type'],
        "confidence": results['confidence'],
        "is_alert": results['is_alert']
    }), 201

# Endpoint to get aggregated reports (for Analytics/Heatmap tabs)
@app.route('/api/get_reports', methods=['GET'])
def get_reports():
    if incidents_collection is None:
        return jsonify({"message": "Database unavailable"}), 503

    # --- Aggregation Pipeline for Analytics ---
    
    # 1. Calculate Summary Metrics
    summary_pipeline = [
        {"$group": {
            "_id": None,
            "totalDetections": {"$sum": 1},
            "avgConfidence": {"$avg": "$confidence"},
            "totalAlerts": {"$sum": {
                "$cond": [
                    {"$in": ["$detection_type", ["Overflowing Bin", "Spill Detected", "Graffiti"]]}, 
                    1, 
                    0
                ]
            }}
        }}
    ]
    summary_result = list(incidents_collection.aggregate(summary_pipeline))
    
    if summary_result:
        summary = summary_result[0]
        total_detections = summary.get('totalDetections', 0)
        # Use try-except or check for None/zero division before rounding, though $avg handles None gracefully
        avg_confidence_val = summary.get('avgConfidence', 0)
        avg_confidence = f"{round(avg_confidence_val, 1)}%" if avg_confidence_val else "0%"
        total_alerts = summary.get('totalAlerts', 0)
    else:
        total_detections, avg_confidence, total_alerts = 0, "0%", 0

    # 2. Calculate Detection Types breakdown
    types_pipeline = [
        {"$group": {"_id": "$detection_type", "count": {"$sum": 1}}}
    ]
    types_raw = list(incidents_collection.aggregate(types_pipeline))
    detection_types = {item['_id']: item['count'] for item in types_raw}

    # 3. Calculate Hourly Data
    hourly_pipeline = [
        {"$group": {
            "_id": {"$hour": "$timestamp"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    hourly_raw = list(incidents_collection.aggregate(hourly_pipeline))
    hourly_data = [0] * 24
    for item in hourly_raw:
        hour = item['_id']
        if 0 <= hour < 24:
            hourly_data[hour] = item['count']

    # 4. Heatmap/Location Scores
    heatmap_data = list(incidents_collection.aggregate([
        {"$group": {"_id": "$location_id", "issueCount": {"$sum": 1}}},
        {"$sort": {"issueCount": -1}}
    ]))
    
    heatmap_scores = [{"location": item['_id'], "score": max(0, 100 - item['issueCount'] * 5)} for item in heatmap_data]


    return jsonify({
      "generatedAt": datetime.now().isoformat(),
      "summary": {
        "totalDetections": total_detections,
        "totalAlerts": total_alerts,
        "avgConfidence": avg_confidence,
      },
      "detectionTypes": detection_types,
      "hourlyData": hourly_data,
      "heatmapData": heatmap_scores
    })

# --- 5. RUN SERVER ---
if __name__ == '__main__':
    # MongoDB initialization happens via get_mongo_collection()
    print(f"Connecting to MongoDB at {MONGO_URI}")
    
    # NOTE: The default port 5000 is used here. Change this if you have conflicts.
    app.run(debug=True, port=5000)