import os
import random
from datetime import datetime
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask_cors import CORS
from pymongo import MongoClient

# --- 1. CONFIGURATION ---
app = Flask(__name__)
CORS(app)  # allow requests from your HTML page

# MongoDB (OPTIONAL: app still works even if this fails)
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
MONGO_DB_NAME = 'CampusCleanlinessDB'
MONGO_COLLECTION_NAME = 'incidents'

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# --- 2. DATABASE SETUP (NON-FATAL IF FAILS) ---
def get_mongo_collection():
    """Try to connect to MongoDB. If it fails, return None (app runs in demo mode)."""
    try:
        client = MongoClient(MONGO_URI)
        client.admin.command('ping')  # test connection
        db = client[MONGO_DB_NAME]
        print("INFO: Successfully connected to MongoDB.")
        return db[MONGO_COLLECTION_NAME]
    except Exception as e:
        print(f"WARNING: Could not connect to MongoDB. Running in DEMO mode. Error: {e}")
        return None


incidents_collection = get_mongo_collection()


# --- 3. FAKE AI INFERENCE (SIMULATION) ---
def run_ai_inference(image_path: str):
    """
    Simulated AI model.
    In real life you'd load a model and run prediction here.
    """
    types = ['Litter Detected', 'Overflowing Bin', 'Spill Detected',
             'Scattered Trash', 'Debris Found', 'Graffiti']

    detection_type = random.choice(types)
    confidence = 85 + random.random() * 15  # 85–100

    return {
        "detection_type": detection_type,
        "confidence": round(confidence, 1),
        "is_alert": detection_type in ['Overflowing Bin', 'Spill Detected', 'Graffiti']
    }


# --- 4. API ENDPOINTS ---

@app.route('/')
def home():
    """Health check endpoint."""
    db_status = "connected" if incidents_collection is not None else "unavailable (demo mode)"
    return f"Campus Cleanliness Monitoring API is running. DB status: {db_status}", 200


@app.route('/api/detect_and_report', methods=['POST'])
def detect_and_report():
    """
    Receive an image + optional location_id.
    Run AI inference and (if DB is available) log incident.
    Always returns success if inference works, even if DB is down.
    """
    if 'image' not in request.files:
        return jsonify({"success": False, "message": "No image file provided"}), 400

    file = request.files['image']
    location_id = request.form.get('location_id', 'Unknown Zone')

    # 1. Save uploaded image
    filename = secure_filename(f"{location_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # 2. Run AI inference
    try:
        results = run_ai_inference(filepath)
    except Exception as e:
        return jsonify({"success": False, "message": f"AI model inference failed: {e}"}), 500

    # 3. Try to log in DB (OPTIONAL)
    incident_id = None
    if incidents_collection is not None:
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
            # Do NOT fail the whole request, just log warning
            print(f"WARNING: MongoDB logging failed: {e}")
    else:
        print("INFO: No DB connection, skipping incident logging.")

    # 4. Response to frontend
    return jsonify({
        "success": True,
        "incident_id": incident_id,
        "location": location_id,
        "detection_type": results['detection_type'],
        "confidence": results['confidence'],
        "is_alert": results['is_alert']
    }), 201


@app.route('/api/get_reports', methods=['GET'])
def get_reports():
    """
    Analytics endpoint used by the dashboard.
    If DB is missing, return safe default values instead of 503.
    """
    # If no DB -> send zeros & static heatmap so UI still works
    if incidents_collection is None:
        summary = {
            "totalDetections": 0,
            "totalAlerts": 0,
            "avgConfidence": "0%",
        }
        detection_types = {}
        hourly_data = [0] * 24
        heatmap_scores = [
            {"location": "Library", "score": 100},
            {"location": "Cafeteria", "score": 100},
            {"location": "Dormitories", "score": 100},
            {"location": "Sports Complex", "score": 100},
            {"location": "Parking Lots", "score": 100},
        ]
        return jsonify({
            "generatedAt": datetime.now().isoformat(),
            "summary": summary,
            "detectionTypes": detection_types,
            "hourlyData": hourly_data,
            "heatmapData": heatmap_scores
        }), 200

    # --- REAL MONGO AGGREGATIONS BELOW ---

    # 1. Summary
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
        summary_doc = summary_result[0]
        total_detections = summary_doc.get('totalDetections', 0)
        avg_conf_val = summary_doc.get('avgConfidence', 0) or 0
        avg_confidence = f"{round(avg_conf_val, 1)}%"
        total_alerts = summary_doc.get('totalAlerts', 0)
    else:
        total_detections, avg_confidence, total_alerts = 0, "0%", 0

    # 2. Detection types
    types_pipeline = [
        {"$group": {"_id": "$detection_type", "count": {"$sum": 1}}}
    ]
    types_raw = list(incidents_collection.aggregate(types_pipeline))
    detection_types = {item['_id']: item['count'] for item in types_raw}

    # 3. Hourly data
    hourly_pipeline = [
        {"$group": {"_id": {"$hour": "$timestamp"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    hourly_raw = list(incidents_collection.aggregate(hourly_pipeline))
    hourly_data = [0] * 24
    for item in hourly_raw:
        hour = item['_id']
        if isinstance(hour, int) and 0 <= hour < 24:
            hourly_data[hour] = item['count']

    # 4. Heatmap scores per location
    heatmap_data = list(incidents_collection.aggregate([
        {"$group": {"_id": "$location_id", "issueCount": {"$sum": 1}}},
        {"$sort": {"issueCount": -1}}
    ]))

    # Higher issueCount -> lower score
    heatmap_scores = [
        {"location": item['_id'], "score": max(0, 100 - item['issueCount'] * 5)}
        for item in heatmap_data
    ]

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
    }), 200


# --- 5. RUN SERVER ---
if __name__ == '__main__':
    print(f"Starting API. MONGO_URI = {MONGO_URI}")
    port = int(os.environ.get("PORT", 5000))  # Render uses $PORT
    app.run(host="0.0.0.0", port=port)
