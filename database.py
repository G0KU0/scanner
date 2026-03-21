from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone
import os
import secrets
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
invites_collection = database.invites
results_collection = database.results  # ÚJ: Eredmények külön collection


# ================================================================
#                     MEGHÍVÓ FUNKCIÓK
# ================================================================

async def create_invite(admin_email: str) -> dict:
    """Admin által létrehozott meghívó kód"""
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
    """Meghívó kód lekérése"""
    return await invites_collection.find_one({"code": code})


async def use_invite(code: str, user_email: str):
    """Meghívó felhasználása regisztrációkor"""
    await invites_collection.update_one(
        {"code": code},
        {"$set": {
            "used_by": user_email,
            "used_at": datetime.now(timezone.utc)
        }}
    )


async def delete_invite(code: str):
    """Meghívó törlése (admin által)"""
    await invites_collection.delete_one({"code": code})


async def deactivate_invite(code: str):
    """Meghívó deaktiválása"""
    await invites_collection.update_one(
        {"code": code},
        {"$set": {"is_active": False}}
    )


async def get_all_invites():
    """Összes meghívó listázása (admin panel)"""
    cursor = invites_collection.find({}).sort("created_at", -1)
    return await cursor.to_list(length=1000)


async def revoke_users_by_invite(invite_code: str):
    """Adott meghívóval regisztrált userek letiltása"""
    await users_collection.update_many(
        {"invite_code": invite_code},
        {"$set": {"invite_active": False}}
    )


# ================================================================
#                     USER FUNKCIÓK
# ================================================================

async def create_user(email: str, hashed_password: str, invite_code: str, is_admin: bool = False):
    """User létrehozása meghívó kóddal"""
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
    """User lekérése email alapján"""
    return await users_collection.find_one({"email": email})


async def update_user_invite_status(email: str, status: bool):
    """User meghívó státuszának frissítése"""
    await users_collection.update_one(
        {"email": email},
        {"$set": {"invite_active": status}}
    )


async def get_all_users():
    """Összes user (admin panel)"""
    cursor = users_collection.find({}).sort("created_at", -1)
    return await cursor.to_list(length=1000)


# ================================================================
#                     RUN (FUTTATÁS) FUNKCIÓK
# ================================================================

async def create_run(user_id: str, keyword: str, total: int):
    """
    Új futtatás létrehozása.
    NEM tárol eredményeket a run dokumentumban - azok külön collection-ben vannak!
    """
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


async def delete_old_runs(user_id: str, keep_run_id: str):
    """
    Törli a felhasználó ÖSSZES korábbi futtatását és eredményeit,
    KIVÉVE az utolsó BEFEJEZETT futtatást (annak eredményei maradnak)
    és a most indított futtatást.
    """
    try:
        # Keressük meg az utolsó befejezett futtatást (nem a mostani)
        last_finished = await runs_collection.find_one(
            {
                "user_id": user_id,
                "status": "finished",
                "_id": {"$ne": ObjectId(keep_run_id)}
            },
            sort=[("finished_at", -1)]
        )

        last_finished_id = str(last_finished["_id"]) if last_finished else None

        # Töröljük az összes RÉGI futtatást KIVÉVE:
        # 1. A most indított (keep_run_id)
        # 2. Az utolsó befejezett (last_finished_id) - ha van
        exclude_ids = [ObjectId(keep_run_id)]
        if last_finished_id:
            exclude_ids.append(ObjectId(last_finished_id))

        # Régi futtatások ID-i amiket törölünk
        old_runs_cursor = runs_collection.find(
            {
                "user_id": user_id,
                "_id": {"$nin": exclude_ids}
            }
        )
        old_runs = await old_runs_cursor.to_list(length=100)

        for old_run in old_runs:
            old_id = str(old_run["_id"])
            # Eredmények törlése
            await results_collection.delete_many({"run_id": old_id})
            # Futtatás törlése
            await runs_collection.delete_one({"_id": old_run["_id"]})

        deleted_count = len(old_runs)
        if deleted_count > 0:
            print(f"🗑️ {deleted_count} régi futtatás törölve. (User: {user_id})")
        if last_finished_id:
            print(f"📦 Utolsó befejezett futtatás megtartva: {last_finished_id}")

    except Exception as e:
        print(f"❌ Hiba a régi futtatások törlésekor: {e}")


async def get_run(run_id: str):
    """Futtatás lekérése"""
    try:
        return await runs_collection.find_one({"_id": ObjectId(run_id)})
    except Exception as e:
        print(f"❌ get_run hiba [{run_id}]: {e}")
        return None


async def update_run_stats(run_id: str, stats: dict):
    """Futtatás statisztikáinak frissítése"""
    try:
        await runs_collection.update_one(
            {"_id": ObjectId(run_id)},
            {"$set": stats}
        )
    except Exception as e:
        print(f"❌ update_run_stats hiba [{run_id}]: {e}")


async def update_run_status_only(run_id: str, status: str):
    """Futtatás státuszának frissítése"""
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
    """Aktív (futó) futtatás lekérése"""
    return await runs_collection.find_one(
        {"user_id": user_id, "status": "running"}
    )


async def get_user_finished_runs(user_id: str):
    """Utolsó 1 befejezett futtatás lekérése"""
    cursor = runs_collection.find(
        {"user_id": user_id, "status": "finished"}
    ).sort("finished_at", -1).limit(1)
    return await cursor.to_list(length=1)


async def get_last_finished_run(user_id: str):
    """Utolsó befejezett futtatás lekérése (egyetlen)"""
    return await runs_collection.find_one(
        {"user_id": user_id, "status": "finished"},
        sort=[("finished_at", -1)]
    )


# ================================================================
#                 EREDMÉNYEK (RESULTS) - KÜLÖN COLLECTION
#          Minden eredmény egyedi dokumentum → kis memória
# ================================================================

async def add_result(run_id: str, user_id: str, result_type: str, line: str, data: dict):
    """
    Egyetlen eredmény mentése a results collection-be.
    Minden eredmény (hit/custom) saját dokumentum lesz.
    NEM terheli a memóriát, mert egyenként menti!
    """
    try:
        result_doc = {
            "run_id": run_id,
            "user_id": user_id,
            "type": result_type,  # "hit" vagy "custom"
            "line": line,          # Formázott sor (email:pass | Country=... stb)
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
    """
    Futtatás eredményeinek lekérése.
    result_type: "hit", "custom", vagy None (mind)
    """
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
    """
    Csak a formázott sorok lekérése (feltöltéshez).
    Visszaadja a line mezőt string listaként.
    """
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
    """
    Részletes eredmények lekérése (WebSocket live feed-hez).
    """
    try:
        cursor = results_collection.find(
            {"run_id": run_id, "type": result_type},
            {
                "_id": 0, "line": 0, "run_id": 0,
                "user_id": 0, "created_at": 0
            }
        ).sort("created_at", 1)

        results = await cursor.to_list(length=10000)

        # Visszaadjuk a data formátumban
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
    """Eredmények számának lekérése"""
    try:
        return await results_collection.count_documents(
            {"run_id": run_id, "type": result_type}
        )
    except Exception as e:
        print(f"❌ get_result_count hiba [{run_id}]: {e}")
        return 0


async def delete_run_results(run_id: str):
    """Futtatás ÖSSZES eredményének törlése"""
    try:
        result = await results_collection.delete_many({"run_id": run_id})
        print(f"🗑️ {result.deleted_count} eredmény törölve (run: {run_id})")
    except Exception as e:
        print(f"❌ delete_run_results hiba [{run_id}]: {e}")


# ================================================================
#              FUTTATÁS BEFEJEZÉSE + CLEANUP
# ================================================================

async def finish_run(run_id: str, hits_url: str, custom_url: str):
    """
    Futtatás befejezése.
    
    - Elmenti a külső URL-eket (pastebin.fi / transfer.sh)
    - Ha SIKERÜLT feltölteni → törli az eredményeket a DB-ből
    - Ha NEM sikerült → eredmények MARADNAK a DB-ben (fallback letöltés)
    """
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

        # Ha mindkét URL megvan → törölhetjük az eredményeket a DB-ből
        # hogy ne foglalja a helyet
        hits_count = await get_result_count(run_id, "hit")
        custom_count = await get_result_count(run_id, "custom")

        should_delete = True

        if hits_count > 0 and not hits_url:
            should_delete = False
            print(f"  ⚠️ Hits eredmények MARADNAK DB-ben (nincs külső URL)")

        if custom_count > 0 and not custom_url:
            should_delete = False
            print(f"  ⚠️ Custom eredmények MARADNAK DB-ben (nincs külső URL)")

        if should_delete and (hits_url or custom_url):
            # Minden feltöltve → töröljük az eredményeket
            await delete_run_results(run_id)
            print(f"  ✅ Eredmények törölve DB-ből (külső URL-ek mentve)")

        print(f"  📊 Run befejezve: hits={hits_count}, custom={custom_count}")

    except Exception as e:
        print(f"❌ finish_run hiba [{run_id}]: {e}")


async def cleanup_user_data(user_id: str, keep_run_id: str):
    """
    User adatainak takarítása indításkor.
    
    LOGIKA:
    - Megtartja az UTOLSÓ BEFEJEZETT futtatást (és annak URL-jeit)
    - Megtartja a MOST INDÍTOTT futtatást
    - Töröl MINDENT ami régebbi
    """
    try:
        # Utolsó befejezett keresése
        last_finished = await runs_collection.find_one(
            {
                "user_id": user_id,
                "status": "finished",
                "_id": {"$ne": ObjectId(keep_run_id)}
            },
            sort=[("finished_at", -1)]
        )

        # Megtartandó ID-k
        keep_ids = [ObjectId(keep_run_id)]
        if last_finished:
            keep_ids.append(last_finished["_id"])

        # Régi futtatások keresése
        old_runs_cursor = runs_collection.find(
            {
                "user_id": user_id,
                "_id": {"$nin": keep_ids}
            }
        )
        old_runs = await old_runs_cursor.to_list(length=100)

        # Régi futtatások és eredményeik törlése
        for old_run in old_runs:
            old_id = str(old_run["_id"])
            await results_collection.delete_many({"run_id": old_id})
            await runs_collection.delete_one({"_id": old_run["_id"]})

        if len(old_runs) > 0:
            print(f"🧹 {len(old_runs)} régi futtatás kitakarítva (User: {user_id})")

    except Exception as e:
        print(f"❌ cleanup_user_data hiba: {e}")


# ================================================================
#                     INDEX-EK LÉTREHOZÁSA
# ================================================================

async def ensure_indexes():
    """MongoDB indexek létrehozása a gyorsabb lekérdezésekhez"""
    try:
        # Users
        await users_collection.create_index("email", unique=True)

        # Invites
        await invites_collection.create_index("code", unique=True)

        # Runs
        await runs_collection.create_index("user_id")
        await runs_collection.create_index([("user_id", 1), ("status", 1)])
        await runs_collection.create_index([("user_id", 1), ("finished_at", -1)])

        # Results - FONTOS az eredmények gyors lekérdezéséhez
        await results_collection.create_index("run_id")
        await results_collection.create_index([("run_id", 1), ("type", 1)])
        await results_collection.create_index([("run_id", 1), ("created_at", 1)])

        print("✅ MongoDB indexek létrehozva")
    except Exception as e:
        print(f"⚠️ Index hiba (nem kritikus): {e}")
