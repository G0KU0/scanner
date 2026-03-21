from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone
import os
import secrets
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")

if not MONGODB_URL:
    raise ValueError("❌ MONGODB_URL nincs beállítva!")

try:
    client = AsyncIOMotorClient(MONGODB_URL)
    database = client.hotmail_checker
    print("✅ MongoDB kapcsolat inicializálva")
except Exception as e:
    print(f"❌ MongoDB hiba: {e}")
    raise

users_collection = database.users
runs_collection = database.runs
invites_collection = database.invites
results_collection = database.results


# ================================================================
#                     MEGHÍVÓ FUNKCIÓK
# ================================================================

async def create_invite(admin_email: str) -> dict:
    invite_code = secrets.token_urlsafe(16)
    invite = {
        "code": invite_code,
        "created_by": admin_email,
        "created_at": datetime.now(timezone.utc),
        "used_by": None,
        "used_at": None,
        "is_active": True
    }
    await invites_collection.insert_one(invite)
    return invite


async def get_invite_by_code(code: str):
    return await invites_collection.find_one({"code": code})


async def use_invite(code: str, user_email: str):
    await invites_collection.update_one(
        {"code": code},
        {"$set": {
            "used_by": user_email,
            "used_at": datetime.now(timezone.utc)
        }}
    )


async def delete_invite(code: str):
    await invites_collection.delete_one({"code": code})


async def deactivate_invite(code: str):
    await invites_collection.update_one(
        {"code": code},
        {"$set": {"is_active": False}}
    )


async def get_all_invites():
    cursor = invites_collection.find({}).sort("created_at", -1)
    return await cursor.to_list(length=1000)


async def revoke_users_by_invite(invite_code: str):
    await users_collection.update_many(
        {"invite_code": invite_code},
        {"$set": {"invite_active": False}}
    )


# ================================================================
#                     USER FUNKCIÓK
# ================================================================

async def create_user(email: str, hashed_password: str, invite_code: str, is_admin: bool = False):
    user = {
        "email": email,
        "password": hashed_password,
        "invite_code": invite_code,
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc),
        "is_active": True,
        "invite_active": True
    }
    result = await users_collection.insert_one(user)
    return str(result.inserted_id)


async def get_user_by_email(email: str):
    return await users_collection.find_one({"email": email})


async def update_user_invite_status(email: str, status: bool):
    await users_collection.update_one(
        {"email": email},
        {"$set": {"invite_active": status}}
    )


async def get_all_users():
    cursor = users_collection.find({}).sort("created_at", -1)
    return await cursor.to_list(length=1000)


# ================================================================
#                     RUN FUNKCIÓK
# ================================================================

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
        "hits_url": None,
        "custom_url": None,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None
    }
    result = await runs_collection.insert_one(run)
    return str(result.inserted_id)


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
        update_data = {"status": status}
        if status == "finished":
            update_data["finished_at"] = datetime.now(timezone.utc)
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": update_data}
        )
    except Exception as e:
        print(f"❌ update_run_status_only hiba [{run_id}]: {e}")


async def get_active_run(user_id: str):
    return await runs_collection.find_one(
        {"user_id": user_id, "status": "running"}
    )


async def get_user_finished_runs(user_id: str):
    cursor = runs_collection.find(
        {"user_id": user_id, "status": "finished"}
    ).sort("finished_at", -1).limit(1)
    return await cursor.to_list(length=1)


async def get_last_finished_run(user_id: str):
    return await runs_collection.find_one(
        {"user_id": user_id, "status": "finished"},
        sort=[("finished_at", -1)]
    )


# ================================================================
#              EREDMÉNYEK (RESULTS) - KÜLÖN COLLECTION
# ================================================================

async def add_result(run_id: str, user_id: str, result_type: str, line: str, data: dict):
    try:
        result_doc = {
            "run_id": run_id,
            "user_id": user_id,
            "type": result_type,
            "line": line,
            "email": data.get("email", ""),
            "password": data.get("password", ""),
            "country": data.get("country", ""),
            "name": data.get("name", ""),
            "birthdate": data.get("birthdate", "N/A"),
            "mails": data.get("mails", ""),
            "date": data.get("date", ""),
            "created_at": datetime.now(timezone.utc)
        }
        await results_collection.insert_one(result_doc)
    except Exception as e:
        print(f"❌ add_result hiba [{run_id}]: {e}")


async def get_run_results(run_id: str, result_type: str = None):
    try:
        query = {"run_id": run_id}
        if result_type:
            query["type"] = result_type
        cursor = results_collection.find(query).sort("created_at", 1)
        return await cursor.to_list(length=10000)
    except Exception as e:
        print(f"❌ get_run_results hiba [{run_id}]: {e}")
        return []


async def get_run_result_lines(run_id: str, result_type: str):
    try:
        cursor = results_collection.find(
            {"run_id": run_id, "type": result_type},
            {"line": 1, "_id": 0}
        ).sort("created_at", 1)
        results = await cursor.to_list(length=10000)
        return [r["line"] for r in results if r.get("line")]
    except Exception as e:
        print(f"❌ get_run_result_lines hiba [{run_id}]: {e}")
        return []


async def get_run_result_details(run_id: str, result_type: str):
    try:
        cursor = results_collection.find(
            {"run_id": run_id, "type": result_type},
            {"_id": 0, "line": 0, "run_id": 0, "user_id": 0, "created_at": 0}
        ).sort("created_at", 1)
        results = await cursor.to_list(length=10000)
        details = []
        for r in results:
            detail = {
                "email": r.get("email", ""),
                "password": r.get("password", ""),
                "country": r.get("country", ""),
                "name": r.get("name", ""),
                "birthdate": r.get("birthdate", "N/A"),
            }
            if r.get("type") == "hit":
                detail["mails"] = r.get("mails", "")
                detail["date"] = r.get("date", "")
            details.append(detail)
        return details
    except Exception as e:
        print(f"❌ get_run_result_details hiba [{run_id}]: {e}")
        return []


async def get_result_count(run_id: str, result_type: str):
    try:
        return await results_collection.count_documents(
            {"run_id": run_id, "type": result_type}
        )
    except Exception as e:
        print(f"❌ get_result_count hiba [{run_id}]: {e}")
        return 0


async def delete_run_results(run_id: str):
    try:
        result = await results_collection.delete_many({"run_id": run_id})
        print(f"🗑️ {result.deleted_count} eredmény törölve (run: {run_id})")
    except Exception as e:
        print(f"❌ delete_run_results hiba [{run_id}]: {e}")


# ================================================================
#                    FINISH + CLEANUP
# ================================================================

async def finish_run(run_id: str, hits_url: str, custom_url: str):
    try:
        update_data = {
            "status": "finished",
            "finished_at": datetime.now(timezone.utc)
        }
        if hits_url:
            update_data["hits_url"] = hits_url
        if custom_url:
            update_data["custom_url"] = custom_url

        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": update_data}
        )

        hits_count = await get_result_count(run_id, "hit")
        custom_count = await get_result_count(run_id, "custom")

        should_delete = True
        if hits_count > 0 and not hits_url:
            should_delete = False
        if custom_count > 0 and not custom_url:
            should_delete = False

        if should_delete and (hits_url or custom_url):
            await delete_run_results(run_id)
            print(f"  ✅ Eredmények törölve DB-ből")

        print(f"  📊 Run befejezve: hits={hits_count}, custom={custom_count}")
    except Exception as e:
        print(f"❌ finish_run hiba [{run_id}]: {e}")


async def cleanup_user_data(user_id: str, keep_run_id: str):
    try:
        last_finished = await runs_collection.find_one(
            {
                "user_id": user_id,
                "status": "finished",
                "_id": {"$ne": ObjectId(keep_run_id)}
            },
            sort=[("finished_at", -1)]
        )

        keep_ids = [ObjectId(keep_run_id)]
        if last_finished:
            keep_ids.append(last_finished["_id"])

        old_runs_cursor = runs_collection.find(
            {
                "user_id": user_id,
                "_id": {"$nin": keep_ids}
            }
        )
        old_runs = await old_runs_cursor.to_list(length=100)

        for old_run in old_runs:
            old_id = str(old_run["_id"])
            await results_collection.delete_many({"run_id": old_id})
            await runs_collection.delete_one({"_id": old_run["_id"]})

        if len(old_runs) > 0:
            print(f"🧹 {len(old_runs)} régi futtatás kitakarítva (User: {user_id})")
    except Exception as e:
        print(f"❌ cleanup_user_data hiba: {e}")


async def ensure_indexes():
    try:
        await users_collection.create_index("email", unique=True)
        await invites_collection.create_index("code", unique=True)
        await runs_collection.create_index("user_id")
        await runs_collection.create_index([("user_id", 1), ("status", 1)])
        await runs_collection.create_index([("user_id", 1), ("finished_at", -1)])
        await results_collection.create_index("run_id")
        await results_collection.create_index([("run_id", 1), ("type", 1)])
        await results_collection.create_index([("run_id", 1), ("created_at", 1)])
        print("✅ MongoDB indexek létrehozva")
    except Exception as e:
        print(f"⚠️ Index hiba (nem kritikus): {e}")
