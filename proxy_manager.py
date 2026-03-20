import requests
import threading
import time
import random
import re
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==========================================
# 30+ INGYENES PROXY FORRÁS (naponta frissülnek)
# ==========================================
PROXY_SOURCES = [
    # === ProxyScrape API (valós idejű) ===
    {"url": "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text", "hint": "mixed"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all", "hint": "http"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all", "hint": "socks5"},
    {"url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all", "hint": "socks4"},

    # === TheSpeedX (20 percenként frissül) ===
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "hint": "socks5"},
    {"url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt", "hint": "socks4"},

    # === monosans (óránként frissül) ===
    {"url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt", "hint": "socks4"},
    {"url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt", "hint": "socks5"},

    # === ShiftyTR ===
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt", "hint": "socks4"},
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt", "hint": "socks5"},

    # === prxchk (rendszeresen frissül) ===
    {"url": "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks4.txt", "hint": "socks4"},
    {"url": "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt", "hint": "socks5"},

    # === MuRongPIG (naponta) ===
    {"url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt", "hint": "socks5"},
    {"url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks4.txt", "hint": "socks4"},

    # === mmpx12 ===
    {"url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt", "hint": "socks5"},
    {"url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks4.txt", "hint": "socks4"},

    # === rdavydov ===
    {"url": "https://raw.githubusercontent.com/rdavydov/proxy-list/main/proxies/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/rdavydov/proxy-list/main/proxies/socks4.txt", "hint": "socks4"},
    {"url": "https://raw.githubusercontent.com/rdavydov/proxy-list/main/proxies/socks5.txt", "hint": "socks5"},

    # === hookzof (socks5) ===
    {"url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt", "hint": "socks5"},

    # === clarketm ===
    {"url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt", "hint": "http"},

    # === proxy-list.download API ===
    {"url": "https://www.proxy-list.download/api/v1/get?type=http", "hint": "http"},
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks5", "hint": "socks5"},
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks4", "hint": "socks4"},

    # === sunny9577 ===
    {"url": "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/socks5_proxies.txt", "hint": "socks5"},

    # === Zaeem20 ===
    {"url": "https://raw.githubusercontent.com/Zaeem20/FREE_PROXY_LIST/master/http.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/Zaeem20/FREE_PROXY_LIST/master/socks5.txt", "hint": "socks5"},
    {"url": "https://raw.githubusercontent.com/Zaeem20/FREE_PROXY_LIST/master/socks4.txt", "hint": "socks4"},

    # === roosterkid ===
    {"url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "hint": "http"},
    {"url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt", "hint": "socks5"},
    {"url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt", "hint": "socks4"},

    # === openproxylist API ===
    {"url": "https://api.openproxylist.xyz/http.txt", "hint": "http"},
    {"url": "https://api.openproxylist.xyz/socks5.txt", "hint": "socks5"},
    {"url": "https://api.openproxylist.xyz/socks4.txt", "hint": "socks4"},

    # === spys.me ===
    {"url": "https://spys.me/proxy.txt", "hint": "http"},

    # === GeoNode API ===
    {"url": "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc", "hint": "geonode"},
]


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.proxy_cycle = None
        self.lock = threading.Lock()
        self.last_fetch = 0
        self.tested = False

    # ------------------------------------------
    # FORRÁS LETÖLTÉS
    # ------------------------------------------
    def _fetch_from_source(self, source: dict) -> list:
        """Egy forrásból letölti a proxykat"""
        url = source["url"]
        hint = source.get("hint", "unknown")
        proxies_found = []

        try:
            resp = requests.get(
                url, timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            if resp.status_code != 200:
                return proxies_found

            # GeoNode speciális JSON formátum
            if hint == "geonode":
                try:
                    data = resp.json()
                    for item in data.get("data", []):
                        ip = item.get("ip", "")
                        port = str(item.get("port", ""))
                        if self._is_valid_proxy(ip, port):
                            proto = "socks5" if "socks5" in item.get("protocols", []) else \
                                    "socks4" if "socks4" in item.get("protocols", []) else "http"
                            proxies_found.append({"ip_port": f"{ip}:{port}", "hint": proto})
                except:
                    pass
                return proxies_found

            # Normál szöveges formátum (ip:port)
            for line in resp.text.strip().splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('*'):
                    continue

                # Regex: ip:port vagy ip port (bármilyen formátum)
                match = re.match(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s]+(\d{1,5})', line)
                if match:
                    ip = match.group(1)
                    port = match.group(2)
                    if self._is_valid_proxy(ip, port):
                        actual_hint = hint if hint not in ("mixed", "geonode") else "unknown"
                        proxies_found.append({"ip_port": f"{ip}:{port}", "hint": actual_hint})

        except Exception:
            pass

        return proxies_found

    def fetch_proxies(self) -> int:
        """Összes forrásból letölti a proxykat párhuzamosan"""
        total_sources = len(PROXY_SOURCES)
        print(f"\n🔄 Proxyk letöltése {total_sources} forrásból...")

        all_proxies = {}  # ip_port -> hint (deduplikáció)
        success_count = 0
        fail_count = 0

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {
                executor.submit(self._fetch_from_source, src): src
                for src in PROXY_SOURCES
            }
            for future in as_completed(futures):
                src = futures[future]
                try:
                    result = future.result()
                    if result:
                        for p in result:
                            ip_port = p["ip_port"]
                            if ip_port not in all_proxies:
                                all_proxies[ip_port] = p["hint"]
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception:
                    fail_count += 1

        print(f"  ✅ {success_count}/{total_sources} forrás sikeres | ❌ {fail_count} sikertelen")

        with self.lock:
            self.proxies = [
                {"ip_port": ip_port, "protocol": hint if hint != "unknown" else "http"}
                for ip_port, hint in all_proxies.items()
            ]
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.last_fetch = time.time()
            self.tested = False

        print(f"  📦 {len(self.proxies)} egyedi proxy összegyűjtve (tesztelésre vár)")
        return len(self.proxies)

    # ------------------------------------------
    # PROXY TESZTELÉS
    # ------------------------------------------
    def _test_single_proxy(self, proxy_info: dict, timeout: int = 8) -> dict:
        ip_port = proxy_info["ip_port"]
        hint = proxy_info.get("protocol", "http")

        # Hint szerinti protokollt próbáljuk először
        protocols = ["http", "socks5", "socks4"]
        if hint in protocols:
            protocols.remove(hint)
            protocols.insert(0, hint)

        for proto in protocols:
            proxy_dict = self._build_proxy_dict(ip_port, proto)
            try:
                r = requests.get(
                    "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
                    "client_info=1&haschrome=1&login_hint=test@hotmail.com"
                    "&mkt=en&response_type=code"
                    "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                    "&scope=profile%20openid%20offline_access"
                    "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
                    "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite"
                    "%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D",
                    proxies=proxy_dict,
                    timeout=timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    },
                    allow_redirects=True,
                )
                text = r.text
                if r.status_code == 200 and len(text) > 5000:
                    if "PPFT" in text and "urlPost" in text:
                        return {"ip_port": ip_port, "protocol": proto}
            except:
                continue

        return None

    def _build_proxy_dict(self, ip_port: str, protocol: str) -> dict:
        if protocol == "socks5":
            return {"http": f"socks5://{ip_port}", "https": f"socks5://{ip_port}"}
        elif protocol == "socks4":
            return {"http": f"socks4://{ip_port}", "https": f"socks4://{ip_port}"}
        else:
            return {"http": f"http://{ip_port}", "https": f"http://{ip_port}"}

    def test_and_filter(
        self,
        sample_size: int = 5000,
        max_workers: int = 500,
        timeout: int = 8,
    ) -> int:
        with self.lock:
            all_proxies = list(self.proxies)

        if not all_proxies:
            print("⚠️  Nincs proxy!")
            return 0

        to_test = all_proxies if len(all_proxies) <= sample_size else random.sample(all_proxies, sample_size)
        total_to_test = len(to_test)

        print(f"\n🧪 {total_to_test} proxy tesztelése Microsoft login ellen...")
        print(f"   {max_workers} szál | {timeout}s timeout | Hint-alapú protokoll sorrend")

        working = []
        tested = 0
        failed = 0
        proto_stats = {"http": 0, "socks4": 0, "socks5": 0}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._test_single_proxy, p, timeout): p
                for p in to_test
            }
            for future in as_completed(futures):
                tested += 1
                try:
                    result = future.result()
                    if result:
                        working.append(result)
                        proto_stats[result["protocol"]] += 1
                    else:
                        failed += 1
                except:
                    failed += 1

                if tested % 500 == 0 or tested == total_to_test:
                    print(
                        f"  📊 {tested}/{total_to_test} | "
                        f"✅ {len(working)} (H:{proto_stats['http']} S5:{proto_stats['socks5']} S4:{proto_stats['socks4']}) | "
                        f"❌ {failed}"
                    )

        with self.lock:
            self.proxies = working
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.tested = True

        print(f"\n{'=' * 60}")
        print(f"  🟢 {len(working)} MŰKÖDŐ proxy kész!")
        print(f"     HTTP: {proto_stats['http']} | SOCKS5: {proto_stats['socks5']} | SOCKS4: {proto_stats['socks4']}")
        print(f"  📋 Források: {len(PROXY_SOURCES)} db | Összegyűjtve: {len(all_proxies)} | Tesztelve: {total_to_test}")
        print(f"{'=' * 60}\n")
        return len(working)

    def fetch_and_test(self) -> int:
        self.fetch_proxies()
        return self.test_and_filter()

    # ------------------------------------------
    # SEGÉD METÓDUSOK
    # ------------------------------------------
    def _is_valid_proxy(self, ip: str, port: str) -> bool:
        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return False
            for p in parts:
                num = int(p)
                if num < 0 or num > 255:
                    return False
            port_num = int(port)
            if port_num < 1 or port_num > 65535:
                return False
            return True
        except:
            return False

    def get_proxy(self) -> dict:
        with self.lock:
            if not self.proxies or not self.proxy_cycle:
                return None
            proxy_info = next(self.proxy_cycle)
        return self._build_proxy_dict(proxy_info["ip_port"], proxy_info["protocol"])

    def get_count(self) -> int:
        with self.lock:
            return len(self.proxies)

    def is_tested(self) -> bool:
        with self.lock:
            return self.tested

    def get_stats(self) -> dict:
        with self.lock:
            stats = {"http": 0, "socks4": 0, "socks5": 0, "total": len(self.proxies)}
            for p in self.proxies:
                proto = p.get("protocol", "http")
                if proto in stats:
                    stats[proto] += 1
            return stats


proxy_manager = ProxyManager()
