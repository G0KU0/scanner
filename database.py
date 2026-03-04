from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")

if not MONGODB_URL:
    raise ValueError(
        "❌ MONGODB_URL nincs beállítva! "
        "Állítsd be Environment Variable-ként a Render Dashboard-on!"
    )

try:
    client = AsyncIOMotorClient(MONGODB_URL)
    database = client.hotmail_checker
    print("✅ MongoDB kapcsolat inicializálva")
except Exception as e:
    print(f"❌ MongoDB hiba: {e}")
    raise

users_collection = database.users
runs_collection = database.runs

async def create_user(email: str, hashed_password: str):
    user = {
        "email": email,
        "password": hashed_password,
        "created_at": datetime.utcnow(),
        "is_active": True
    }
    result = await users_collection.insert_one(user)
    return str(result.inserted_id)

async def get_user_by_email(email: str):
    return await users_collection.find_one({"email": email})

async def create_run(user_id: str, keyword: str, total: int):
    run = {
        "user_id": user_id,
        "keyword": keyword,
        "total": total,
        "checked": 0,
        "hits": 0,
        "custom": 0,
        "bad": 0,
        "retries": 0,
        "status": "running",
        "hit_lines": [],
        "custom_lines": [],
        "hit_details": [],      # ← ÚJ: live preview-hoz (full adatok)
        "custom_details": [],   # ← ÚJ: live preview-hoz (full adatok)
        "started_at": datetime.utcnow(),
        "finished_at": None
    }
    result = await runs_collection.insert_one(run)
    return str(result.inserted_id)

async def get_run(run_id: str):
    from bson import ObjectId
    try:
        return await runs_collection.find_one({"_id": ObjectId(run_id)})
    except:
        return None

async def update_run_stats(run_id: str, stats: dict):
    from bson import ObjectId
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": stats}
        )
    except:
        pass

async def add_result_to_run(run_id: str, result_type: str, line: str):
    """Eredmény hozzáadása (csak text sor)"""
    from bson import ObjectId
    field = "hit_lines" if result_type == "hit" else "custom_lines"
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$push": {field: line}}
        )
    except:
        pass

async def add_result_details_to_run(run_id: str, result_type: str, data: dict):
    """
    ÚJ! Teljes adatok mentése (live preview-hoz).
    Ez MongoDB-be menti az email, password, country, name, stb. adatokat.
    """
    from bson import ObjectId
    field = "hit_details" if result_type == "hit" else "custom_details"
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$push": {field: data}}
        )
    except:
        pass

async def get_user_runs(user_id: str):
    cursor = runs_collection.find({"user_id": user_id}).sort("started_at", -1)
    return await cursor.to_list(length=100)

async def finish_run(run_id: str):
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
    return await runs_collection.find_one({
        "user_id": user_id,
        "status": "running"
    })
