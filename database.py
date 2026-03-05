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

async def delete_old_runs(user_id: str):
    """
    KÜLSŐ API TÁRHELY TISZTÍTÁSA: 
    Törli a felhasználó összes korábbi futtatását a DB-ből!
    Így sosem telik be a MongoDB tárhely.
    """
    try:
        await runs_collection.delete_many({"user_id": user_id})
        print(f"🗑️ Előző futtatások törölve a külső adatbázisból (User: {user_id})")
    except Exception as e:
        print(f"❌ Hiba a régi futtatások törlésekor: {e}")

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
        "hit_details": [],
        "custom_details": [],
        "hits_url": None,
        "custom_url": None,
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

# 🔴 ÚJ FUNKCIÓ (F5 + STOP bugfix) 🔴
async def update_run_status_only(run_id: str, status: str):
    """Csak a státuszt frissíti azonnal, API feltöltés előtt"""
    from bson import ObjectId
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": {
                "status": status,
                "finished_at": datetime.utcnow() if status == "finished" else None
            }}
        )
    except:
        pass

async def add_result_to_run(run_id: str, result_type: str, line: str):
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

async def finish_and_clean_run(run_id: str, hits_url: str, custom_url: str):
    """
    Ez az a funkció, ami MENTI A KÜLSŐ API LINKET, 
    majd TÖRLI a Mongo-ból a hatalmas szövegeket, hogy 0MB-ot foglaljanak el.
    """
    from bson import ObjectId
    try:
        update_data = {
            "status": "finished",
            "finished_at": datetime.utcnow()
        }
        if hits_url: update_data["hits_url"] = hits_url
        if custom_url: update_data["custom_url"] = custom_url

        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {
                "$set": update_data,
                "$unset": {
                    "hit_lines": "",       
                    "custom_lines": "",    
                    "hit_details": "",     
                    "custom_details": ""   
                }
            }
        )
    except Exception as e:
        print(f"Hiba a futtatás lezárásakor: {e}")

async def get_active_run(user_id: str):
    return await runs_collection.find_one({"user_id": user_id, "status": "running"})
