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


async def create_user(email: str, hashed_password: str):
    user = {
        "email": email,
        "password": hashed_password,
        "created_at": datetime.now(timezone.utc),
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
    """
    Törli a felhasználó ÖSSZES korábbi futtatását a DB-ből,
    KIVÉVE azt az egyet (keep_run_id), amit most hoztunk létre!
    """
    try:
        await runs_collection.delete_many({
            "user_id": user_id,
            "_id": {"$ne": ObjectId(keep_run_id)}
        })
        print(f"🗑️ Előző futtatások törölve. (User: {user_id})")
    except Exception as e:
        print(f"❌ Hiba a régi futtatások törlésekor: {e}")


async def get_run(run_id: str):
    try:
        return await runs_collection.find_one({"_id": ObjectId(run_id)})
    except Exception as e:
        print(f"❌ get_run hiba [{run_id}]: {e}")
        return None


async def update_run_stats(run_id: str, stats: dict):
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": stats}
        )
    except Exception as e:
        print(f"❌ update_run_stats hiba [{run_id}]: {e}")


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
        print(f"❌ update_run_status_only hiba [{run_id}]: {e}")


async def add_result_to_run(run_id: str, result_type: str, line: str):
    field = "hit_lines" if result_type == "hit" else "custom_lines"
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$push": {field: line}}
        )
    except Exception as e:
        print(f"❌ add_result_to_run hiba [{run_id}]: {e}")


async def add_result_details_to_run(run_id: str, result_type: str, data: dict):
    field = "hit_details" if result_type == "hit" else "custom_details"
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$push": {field: data}}
        )
    except Exception as e:
        print(f"❌ add_result_details hiba [{run_id}]: {e}")


async def get_user_finished_runs(user_id: str):
    cursor = runs_collection.find(
        {"user_id": user_id, "status": "finished"}
    ).sort("started_at", -1)
    return await cursor.to_list(length=1)


async def finish_and_clean_run(run_id: str, hits_url: str, custom_url: str):
    try:
        update_data = {
            "status": "finished",
            "finished_at": datetime.now(timezone.utc)
        }

        unset_data = {}

        if hits_url:
            update_data["hits_url"] = hits_url
            # ✅ Feltöltve → töröljük a DB-ből
            unset_data["hit_lines"] = ""
            unset_data["hit_details"] = ""
            print(f"  🗑️ hit_lines törölve DB-ből (URL: {hits_url})")
        else:
            print(f"  ⚠️ hit_lines MARAD DB-ben (nincs külső URL)")

        if custom_url:
            update_data["custom_url"] = custom_url
            # ✅ Feltöltve → töröljük a DB-ből
            unset_data["custom_lines"] = ""
            unset_data["custom_details"] = ""
            print(f"  🗑️ custom_lines törölve DB-ből (URL: {custom_url})")
        else:
            print(f"  ⚠️ custom_lines MARAD DB-ben (nincs külső URL)")

        update_query = {"$set": update_data}
        if unset_data:
            update_query["$unset"] = unset_data

        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            update_query
        )
    except Exception as e:
        print(f"❌ finish_and_clean_run hiba [{run_id}]: {e}")
