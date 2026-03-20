from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone
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
invites_collection = database.invites  # Meghívó kódok gyűjteménye


async def create_user(email: str, hashed_password: str, invite_code: str = None):
    user = {
        "email": email,
        "password": hashed_password,
        "created_at": datetime.now(timezone.utc),
        "is_active": True,
        "current_invite_code": invite_code, # Megjegyezzük a kódját
        "needs_new_invite": False           # Alapból nincs zárolva
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
        "hit_details": [],
        "custom_details": [],
        "hits_url": None,
        "custom_url": None,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None
    }
    result = await runs_collection.insert_one(run)
    return str(result.inserted_id)


async def delete_old_runs(user_id: str, keep_run_id: str):
    try:
        await runs_collection.delete_many({
            "user_id": user_id,
            "_id": {"$ne": ObjectId(keep_run_id)}
        })
    except Exception as e:
        pass


async def get_run(run_id: str):
    try:
        return await runs_collection.find_one({"_id": ObjectId(run_id)})
    except Exception as e:
        return None


async def update_run_stats(run_id: str, stats: dict):
    try:
        await runs_collection.update_one({"_id": ObjectId(run_id)}, {"$set": stats})
    except Exception as e:
        pass


async def update_run_status_only(run_id: str, status: str):
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": {
                "status": status,
                "finished_at": datetime.now(timezone.utc) if status == "finished" else None
            }}
        )
    except Exception as e:
        pass


async def add_result_to_run(run_id: str, result_type: str, line: str):
    field = "hit_lines" if result_type == "hit" else "custom_lines"
    try:
        await runs_collection.update_one({"_id": ObjectId(run_id)}, {"$push": {field: line}})
    except Exception as e:
        pass


async def add_result_details_to_run(run_id: str, result_type: str, data: dict):
    field = "hit_details" if result_type == "hit" else "custom_details"
    try:
        await runs_collection.update_one({"_id": ObjectId(run_id)}, {"$push": {field: data}})
    except Exception as e:
        pass


async def get_user_finished_runs(user_id: str):
    cursor = runs_collection.find({"user_id": user_id, "status": "finished"}).sort("started_at", -1)
    return await cursor.to_list(length=1)


async def finish_and_clean_run(run_id: str, hits_url: str, custom_url: str):
    try:
        update_data = {"status": "finished", "finished_at": datetime.now(timezone.utc)}
        if hits_url: update_data["hits_url"] = hits_url
        if custom_url: update_data["custom_url"] = custom_url

        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": update_data, "$unset": { "hit_lines": "", "custom_lines": "", "hit_details": "", "custom_details": "" }}
        )
    except Exception as e:
        pass


async def get_active_run(user_id: str):
    return await runs_collection.find_one({"user_id": user_id, "status": "running"})


# ==========================================
# MEGHÍVÓ KÓD ÉS ZÁROLÁS FUNKCIÓK
# ==========================================

async def create_invite_code(code: str):
    invite = {
        "code": code,
        "created_at": datetime.now(timezone.utc),
        "is_used": False,
        "used_by": None # Ide mentjük el, hogy ki használta
    }
    await invites_collection.insert_one(invite)
    return code


async def get_invite_code(code: str):
    return await invites_collection.find_one({"code": code})


async def mark_invite_used(code: str, email: str):
    """NEM töröljük a kódot, csak megjelöljük felhasználtként!"""
    await invites_collection.update_one(
        {"code": code},
        {"$set": {"is_used": True, "used_by": email}}
    )


async def get_all_invites():
    """Lekéri az összes meghívó kódot az admin számára"""
    cursor = invites_collection.find().sort("created_at", -1)
    return await cursor.to_list(length=100)


async def revoke_invite_and_lock_user(code: str):
    """Zárolja azt a felhasználót, aki ezt a kódot használta, majd törli a kódot"""
    # 1. Megkeressük és zároljuk a usert
    await users_collection.update_many(
        {"current_invite_code": code},
        {"$set": {"needs_new_invite": True}}
    )
    # 2. Töröljük a kódot a rendszerből, hogy ne foglalja a helyet (vagy adminból eltűnjön)
    await invites_collection.delete_one({"code": code})


async def reactivate_user(email: str, new_code: str):
    """Feloldja a felhasználó zárolását az új kóddal"""
    await users_collection.update_one(
        {"email": email},
        {"$set": {"needs_new_invite": False, "current_invite_code": new_code}}
    )
