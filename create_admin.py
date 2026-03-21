import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_admin():
    MONGODB_URL = os.getenv("MONGODB_URL")
    if not MONGODB_URL:
        print("❌ MONGODB_URL nincs beállítva!")
        return

    client = AsyncIOMotorClient(MONGODB_URL)
    db = client.hotmail_checker

    # ⚠️ VÁLTOZTASD MEG EZEKET!
    admin_email = "xat.king6969@gmail.com"
    admin_password = "Erika.2021"

    existing = await db.users.find_one({"email": admin_email})
    if existing:
        # Ha létezik, frissítjük admin-ra
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"is_admin": True, "invite_active": True}}
        )
        print(f"✅ Meglévő user admin-ra frissítve: {admin_email}")
    else:
        await db.users.insert_one({
            "email": admin_email,
            "password": pwd_context.hash(admin_password),
            "is_admin": True,
            "invite_code": "SYSTEM_ADMIN",
            "invite_active": True,
            "is_active": True,
            "created_at": datetime.now(timezone.utc)
        })
        print(f"✅ Admin user létrehozva: {admin_email}")

    print(f"   Jelszó: {admin_password}")
    print(f"   ⚠️ Változtasd meg éles környezetben!")

    client.close()


if __name__ == "__main__":
    asyncio.run(create_admin())
