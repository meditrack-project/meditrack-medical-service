import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Medication, MedicationLog
from app.schemas import MedicationCreate, MedicationUpdate, ALLOWED_FREQUENCIES, ALLOWED_TIMES
from app.utils.auth import get_current_user_id
from app.cache import (
    cache_get, cache_set, cache_delete, cache_delete_pattern,
    key_medications, key_adherence, key_log_history,
    pattern_medical, pattern_ai,
    TTL_MEDICATIONS, TTL_ADHERENCE, TTL_LOG_HISTORY,
)

router = APIRouter(prefix="/api/medications", tags=["Medications"])


def medication_to_dict(med: Medication) -> dict:
    return {
        "id": str(med.id),
        "user_id": str(med.user_id),
        "name": med.name,
        "dosage": med.dosage,
        "frequency": med.frequency,
        "time_of_day": med.time_of_day,
        "start_date": med.start_date.isoformat(),
        "end_date": med.end_date.isoformat() if med.end_date else None,
        "notes": med.notes,
        "created_at": med.created_at.isoformat(),
    }


def validate_frequency(frequency: str):
    if frequency not in ALLOWED_FREQUENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": f"Invalid frequency. Allowed: {', '.join(ALLOWED_FREQUENCIES)}"},
        )


def validate_time_of_day(time_of_day: Optional[str]):
    if time_of_day is not None and time_of_day not in ALLOWED_TIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": f"Invalid time_of_day. Allowed: {', '.join(ALLOWED_TIMES)}"},
        )


@router.get("")
async def get_medications(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    cache_key = key_medications(user_id)
    cached = await cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached}

    meds = (
        db.query(Medication)
        .filter(Medication.user_id == uuid.UUID(user_id))
        .order_by(Medication.created_at.desc())
        .all()
    )
    result = [medication_to_dict(m) for m in meds]
    await cache_set(cache_key, result, TTL_MEDICATIONS)
    return {"success": True, "data": result}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_medication(
    body: MedicationCreate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    validate_frequency(body.frequency)
    validate_time_of_day(body.time_of_day)

    med = Medication(
        user_id=uuid.UUID(user_id),
        name=body.name,
        dosage=body.dosage,
        frequency=body.frequency,
        time_of_day=body.time_of_day,
        start_date=body.start_date,
        end_date=body.end_date,
        notes=body.notes,
    )
    db.add(med)
    db.commit()
    db.refresh(med)

    await cache_delete_pattern(pattern_medical(user_id))
    await cache_delete_pattern(pattern_ai(user_id))

    return {"success": True, "data": medication_to_dict(med)}


@router.get("/logs/today")
async def get_today_logs(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    today = date.today()
    uid = uuid.UUID(user_id)

    # Get all active medications for user
    meds = (
        db.query(Medication)
        .filter(
            Medication.user_id == uid,
            Medication.start_date <= today,
        )
        .filter(
            (Medication.end_date == None) | (Medication.end_date >= today)
        )
        .all()
    )

    # Auto-create log rows for today if they don't exist
    for med in meds:
        existing = (
            db.query(MedicationLog)
            .filter(
                MedicationLog.medication_id == med.id,
                MedicationLog.date == today,
            )
            .first()
        )
        if not existing:
            log = MedicationLog(
                user_id=uid,
                medication_id=med.id,
                date=today,
                taken=False,
            )
            db.add(log)

    db.commit()

    # Fetch all today's logs with medication info
    logs = (
        db.query(MedicationLog)
        .filter(MedicationLog.user_id == uid, MedicationLog.date == today)
        .all()
    )

    # Build response
    log_list = []
    taken_count = 0
    for log in logs:
        med = db.query(Medication).filter(Medication.id == log.medication_id).first()
        if med:
            log_list.append({
                "log_id": str(log.id),
                "medication": {
                    "id": str(med.id),
                    "name": med.name,
                    "dosage": med.dosage,
                    "time_of_day": med.time_of_day,
                },
                "taken": log.taken,
                "taken_at": log.taken_at.isoformat() if log.taken_at else None,
                "notes": log.notes,
            })
            if log.taken:
                taken_count += 1

    total = len(log_list)
    adherence_today = round((taken_count / total) * 100, 1) if total > 0 else 0.0

    return {
        "success": True,
        "data": {
            "date": today.isoformat(),
            "total": total,
            "taken_count": taken_count,
            "adherence_today": adherence_today,
            "logs": log_list,
        },
    }


@router.get("/logs/history")
async def get_log_history(
    days: int = Query(default=30, ge=1, le=90),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    cache_key = key_log_history(user_id, days)
    cached = await cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached}

    uid = uuid.UUID(user_id)
    start_date = date.today() - timedelta(days=days)

    logs = (
        db.query(MedicationLog)
        .filter(
            MedicationLog.user_id == uid,
            MedicationLog.date >= start_date,
        )
        .all()
    )

    # Group by date
    daily = {}
    for log in logs:
        d = log.date.isoformat()
        if d not in daily:
            daily[d] = {"date": d, "total": 0, "taken": 0, "skipped": 0}
        daily[d]["total"] += 1
        if log.taken:
            daily[d]["taken"] += 1
        else:
            daily[d]["skipped"] += 1

    result = []
    for d in sorted(daily.keys(), reverse=True):
        entry = daily[d]
        entry["adherence_percent"] = round((entry["taken"] / entry["total"]) * 100, 1) if entry["total"] > 0 else 0.0
        result.append(entry)

    await cache_set(cache_key, result, TTL_LOG_HISTORY)
    return {"success": True, "data": result}


@router.get("/adherence")
async def get_adherence(
    days: int = Query(default=30, ge=1, le=90),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    cache_key = key_adherence(user_id, days)
    cached = await cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached}

    uid = uuid.UUID(user_id)
    start_date = date.today() - timedelta(days=days)

    meds = db.query(Medication).filter(Medication.user_id == uid).all()

    per_medication = []
    for med in meds:
        logs = (
            db.query(MedicationLog)
            .filter(
                MedicationLog.medication_id == med.id,
                MedicationLog.date >= start_date,
            )
            .all()
        )
        total_days = len(logs)
        taken_days = sum(1 for l in logs if l.taken)
        percent = round((taken_days / total_days) * 100, 1) if total_days > 0 else 0.0
        per_medication.append({
            "medication_id": str(med.id),
            "name": med.name,
            "dosage": med.dosage,
            "total_days": total_days,
            "taken_days": taken_days,
            "percent": percent,
        })

    if per_medication:
        percentages = [m["percent"] for m in per_medication]
        overall_avg = round(sum(percentages) / len(percentages), 1)
        best = max(per_medication, key=lambda x: x["percent"])
        worst = min(per_medication, key=lambda x: x["percent"])
    else:
        overall_avg = 0.0
        best = {"name": "N/A", "percent": 0.0}
        worst = {"name": "N/A", "percent": 0.0}

    result = {
        "overall_avg": overall_avg,
        "best": {"name": best["name"], "percent": best["percent"]},
        "worst": {"name": worst["name"], "percent": worst["percent"]},
        "per_medication": per_medication,
    }

    await cache_set(cache_key, result, TTL_ADHERENCE)
    return {"success": True, "data": result}


@router.get("/{medication_id}")
async def get_medication(
    medication_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        med_uuid = uuid.UUID(medication_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"success": False, "message": "Invalid medication ID"})

    med = (
        db.query(Medication)
        .filter(Medication.id == med_uuid, Medication.user_id == uuid.UUID(user_id))
        .first()
    )
    if not med:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Medication not found"},
        )
    return {"success": True, "data": medication_to_dict(med)}


@router.put("/{medication_id}")
async def update_medication(
    medication_id: str,
    body: MedicationUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        med_uuid = uuid.UUID(medication_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"success": False, "message": "Invalid medication ID"})

    med = (
        db.query(Medication)
        .filter(Medication.id == med_uuid, Medication.user_id == uuid.UUID(user_id))
        .first()
    )
    if not med:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Medication not found"},
        )

    update_data = body.model_dump(exclude_unset=True)
    if "frequency" in update_data:
        validate_frequency(update_data["frequency"])
    if "time_of_day" in update_data:
        validate_time_of_day(update_data["time_of_day"])

    for field, value in update_data.items():
        setattr(med, field, value)

    db.commit()
    db.refresh(med)

    await cache_delete_pattern(pattern_medical(user_id))
    await cache_delete_pattern(pattern_ai(user_id))

    return {"success": True, "data": medication_to_dict(med)}


@router.delete("/{medication_id}")
async def delete_medication(
    medication_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        med_uuid = uuid.UUID(medication_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"success": False, "message": "Invalid medication ID"})

    med = (
        db.query(Medication)
        .filter(Medication.id == med_uuid, Medication.user_id == uuid.UUID(user_id))
        .first()
    )
    if not med:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Medication not found"},
        )

    db.delete(med)
    db.commit()

    await cache_delete_pattern(pattern_medical(user_id))
    await cache_delete_pattern(pattern_ai(user_id))

    return {"success": True, "message": "Medication deleted successfully"}


@router.put("/logs/{log_id}/taken")
async def mark_taken(
    log_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        log_uuid = uuid.UUID(log_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"success": False, "message": "Invalid log ID"})

    log = (
        db.query(MedicationLog)
        .filter(MedicationLog.id == log_uuid, MedicationLog.user_id == uuid.UUID(user_id))
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Medication log not found"},
        )

    log.taken = True
    log.taken_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(log)

    # Invalidate caches
    for d in [7, 30, 90]:
        await cache_delete(key_adherence(user_id, d))
        await cache_delete(key_log_history(user_id, d))
    await cache_delete_pattern(pattern_ai(user_id))

    return {
        "success": True,
        "data": {
            "log_id": str(log.id),
            "taken": log.taken,
            "taken_at": log.taken_at.isoformat(),
        },
    }


@router.put("/logs/{log_id}/skipped")
async def mark_skipped(
    log_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        log_uuid = uuid.UUID(log_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"success": False, "message": "Invalid log ID"})

    log = (
        db.query(MedicationLog)
        .filter(MedicationLog.id == log_uuid, MedicationLog.user_id == uuid.UUID(user_id))
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Medication log not found"},
        )

    log.taken = False
    log.taken_at = None
    db.commit()
    db.refresh(log)

    # Invalidate caches
    for d in [7, 30, 90]:
        await cache_delete(key_adherence(user_id, d))
        await cache_delete(key_log_history(user_id, d))
    await cache_delete_pattern(pattern_ai(user_id))

    return {
        "success": True,
        "data": {
            "log_id": str(log.id),
            "taken": log.taken,
            "taken_at": None,
        },
    }
