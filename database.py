from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
client = AsyncIOMotorClient(MONGODB_URL)
database = client.hotmail_checker

# Collections
users_collection = database.users
runs_collection = database.runs

# ============================================================
# USER MODEL
# ============================================================
async def create_user(email: str, hashed_password: str):
    """Új felhasználó létrehozása"""
    user = {
        "email": email,
        "password": hashed_password,
        "created_at": datetime.utcnow(),
        "is_active": True
    }
    result = await users_collection.insert_one(user)
    return str(result.inserted_id)

async def get_user_by_email(email: str):
    """Felhasználó lekérése email alapján"""
    return await users_collection.find_one({"email": email})

# ============================================================
# RUN MODEL (futtatások)
# ============================================================
async def create_run(user_id: str, keyword: str, total: int):
    """Új futtatás létrehozása"""
    run = {
        "user_id": user_id,
        "keyword": keyword,
        "total": total,
        "checked": 0,
        "hits": 0,
        "custom": 0,
        "bad": 0,
        "retries": 0,
        "status": "running",  # running, finished, error
        "hit_lines": [],
        "custom_lines": [],
        "started_at": datetime.utcnow(),
        "finished_at": None
    }
    result = await runs_collection.insert_one(run)
    return str(result.inserted_id)

async def get_run(run_id: str):
    """Egy futtatás lekérése"""
    from bson import ObjectId
    try:
        return await runs_collection.find_one({"_id": ObjectId(run_id)})
    except:
        return None

async def update_run_stats(run_id: str, stats: dict):
    """Futtatás statisztikáinak frissítése"""
    from bson import ObjectId
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": stats}
        )
    except:
        pass

async def add_result_to_run(run_id: str, result_type: str, line: str):
    """Eredmény hozzáadása a futtatáshoz"""
    from bson import ObjectId
    field = "hit_lines" if result_type == "hit" else "custom_lines"
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$push": {field: line}}
        )
    except:
        pass

async def get_user_runs(user_id: str):
    """Felhasználó összes futtatásának lekérése"""
    cursor = runs_collection.find({"user_id": user_id}).sort("started_at", -1)
    return await cursor.to_list(length=100)

async def finish_run(run_id: str):
    """Futtatás lezárása"""
    from bson import ObjectId
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": {
                "status": "finished",
                "finished_at": datetime.utcnow()
            }}
        )
    except:
        pass

async def get_active_run(user_id: str):
    """User aktív futtatásának lekérése (ha van)"""
    return await runs_collection.find_one({
        "user_id": user_id,
        "status": "running"
    })
