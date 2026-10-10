import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="HROne Employee Attendance & Analytics API",
    version="1.0.0"
)

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "hrone_assignment")

client = MongoClient(MONGODB_URL)
db = client[DATABASE_NAME]

employees_collection = db["employees"]
attendance_collection = db["attendance_logs"]
audit_collection = db["audit_trails"]

class PunchRequest(BaseModel):
    emp_code: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    location: str | None = None

class ManualCorrectionRequest(BaseModel):
    emp_code: str
    date: str  # Format: YYYY-MM-DD
    punch_in: datetime | None = None
    punch_out: datetime | None = None
    reason: str
    requested_by: str

@app.get("/")
def read_root():
    return {"message": "Welcome to HROne Employee Attendance & Analytics API"}

@app.post("/api/attendance/punch-in", status_code=status.HTTP_201_CREATED)
def punch_in(payload: PunchRequest):
    log_doc = {
        "emp_code": payload.emp_code,
        "date": payload.timestamp.strftime("%Y-%m-%d"),
        "status": "PRESENT",
        "punch_in": payload.timestamp,
        "punch_out": None,
        "work_hours": 0.0,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [{"action": "PUNCH_IN", "timestamp": payload.timestamp}]
    }
    result = attendance_collection.insert_one(log_doc)
    return {"status": "success", "message": "Punch-in recorded successfully", "id": str(result.inserted_id)}

@app.post("/api/attendance/punch-out", status_code=status.HTTP_201_CREATED)
def punch_out(payload: PunchRequest):
    # Find existing open log for today
    today_str = payload.timestamp.strftime("%Y-%m-%d")
    log = attendance_collection.find_one({"emp_code": payload.emp_code, "date": today_str, "punch_out": None})
    if log:
        p_in = log.get("punch_in")
        work_hours = round((payload.timestamp - p_in).total_seconds() / 3600, 2) if p_in else 0.0
        attendance_collection.update_one(
            {"_id": log["_id"]},
            {
                "$set": {"punch_out": payload.timestamp, "work_hours": work_hours},
                "$push": {"history": {"action": "PUNCH_OUT", "timestamp": payload.timestamp}}
            }
        )
        return {"status": "success", "message": "Punch-out recorded successfully"}
    
    raise HTTPException(status_code=404, detail="No active punch-in found for today")

@app.post("/api/attendance/correction", status_code=status.HTTP_201_CREATED)
def manual_correction(payload: ManualCorrectionRequest):
    audit_doc = {
        "emp_code": payload.emp_code,
        "date": payload.date,
        "punch_in": payload.punch_in,
        "punch_out": payload.punch_out,
        "reason": payload.reason,
        "requested_by": payload.requested_by,
        "status": "APPROVED",
        "created_at": datetime.utcnow()
    }
    result = audit_collection.insert_one(audit_doc)
    
    # Update or insert attendance record
    work_hours = 0.0
    if payload.punch_in and payload.punch_out:
        work_hours = round((payload.punch_out - payload.punch_in).total_seconds() / 3600, 2)

    attendance_collection.update_one(
        {"emp_code": payload.emp_code, "date": payload.date},
        {
            "$set": {
                "status": "PRESENT",
                "punch_in": payload.punch_in,
                "punch_out": payload.punch_out,
                "work_hours": work_hours
            },
            "$push": {"history": {"action": "MANUAL_CORRECTION", "timestamp": datetime.utcnow(), "reason": payload.reason}}
        },
        upsert=True
    )
        
    return {"status": "success", "message": "Manual correction applied", "audit_id": str(result.inserted_id)}

@app.get("/api/analytics/summary")
def get_attendance_summary(emp_code: str | None = None):
    pipeline = []
    if emp_code:
        pipeline.append({"$match": {"emp_code": emp_code}})
    
    pipeline.extend([
        {
            "$group": {
                "_id": "$emp_code",
                "total_logs": {"$sum": 1},
                "total_work_hours": {"$sum": "$work_hours"},
                "average_work_hours": {"$avg": "$work_hours"},
                "last_activity": {"$max": "$punch_in"}
            }
        }
    ])
    
    summary = list(attendance_collection.aggregate(pipeline))
    return {"status": "success", "data": summary}

@app.get("/api/analytics/audit-trail")
def get_audit_trails(emp_code: str | None = None):
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    audits = list(audit_collection.find(query, {"_id": 0}))
    return {"status": "success", "data": audits}