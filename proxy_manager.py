import requests
import threading
import time
import random
from itertools import cycle


class ProxyManager:
    """Automatikus ingyenes proxy szerző és rotáló manager"""

    def __init__(self):
        self.proxies = []
        self.proxy_cycle = None
        self.lock = threading.Lock()
        self.last_fetch = 0

        self.SOURCES = [
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
            "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
            "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
            "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
            "https://raw.githubusercontent.com/UserR3X/proxy-list/main/online/http.txt",
            "https://raw.githubusercontent.com/ErcinDedeworken/proxies/main/proxies",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://www.proxy-list.download/api/v1?type=http",
            "https://www.proxy-list.download/api/v1?type=https",
            "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc",
            "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
            "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
            "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
        ]

    def fetch_proxies(self) -> int:
        """Összes forrásból proxykat gyűjt"""
        print("\n🔄 Ingyenes proxyk betöltése...")
        all_proxies = set()

        for source_url in self.SOURCES:
            try:
                resp = requests.get(source_url, timeout=10)
                if resp.status_code == 200:
                    content = resp.text

                    if "geonode" in source_url:
                        try:
                            data = resp.json()
                            for p in data.get("data", []):
                                ip = p.get("ip", "")
                                port = p.get("port", "")
                                if ip and port:
                                    all_proxies.add(f"{ip}:{port}")
                            src_name = source_url.split("/")[-1][:40]
                            print(f"  ✅ {src_name}... ({len(all_proxies)} összesen)")
                            continue
                        except:
                            pass

                    for line in content.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(":")
                        if len(parts) >= 2:
                            ip_part = parts[0].strip()
                            port_part = parts[1].strip().split()[0]
                            if self._is_valid_proxy(ip_part, port_part):
                                all_proxies.add(f"{ip_part}:{port_part}")

                    src_name = source_url.split("/")[-1][:40]
                    print(f"  ✅ {src_name}... ({len(all_proxies)} összesen)")
            except Exception:
                src_name = source_url.split("/")[-1][:40]
                print(f"  ❌ {src_name}... (hiba)")
                continue

        with self.lock:
            self.proxies = list(all_proxies)
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies)
            self.last_fetch = time.time()

        count = len(self.proxies)
        print(f"\n{'=' * 50}")
        print(f"  🟢 {count} proxy betöltve és kész!")
        print(f"{'=' * 50}\n")
        return count

    def _is_valid_proxy(self, ip: str, port: str) -> bool:
        """Ellenőrzi, hogy érvényes ip:port-e"""
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
        """Következő proxy lekérése (round-robin)"""
        with self.lock:
            if not self.proxies or not self.proxy_cycle:
                return None
            proxy_str = next(self.proxy_cycle)
        return {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}",
        }

    def get_proxy_str(self) -> str:
        """Proxy string formátumban"""
        with self.lock:
            if not self.proxies or not self.proxy_cycle:
                return None
            return next(self.proxy_cycle)

    def get_count(self) -> int:
        """Betöltött proxyk száma"""
        with self.lock:
            return len(self.proxies)

    def remove_proxy(self, proxy_str: str):
        """Rossz proxy eltávolítása"""
        with self.lock:
            if proxy_str in self.proxies:
                self.proxies.remove(proxy_str)
                if self.proxies:
                    self.proxy_cycle = cycle(self.proxies)
                else:
                    self.proxy_cycle = None


# Globális proxy manager példány
proxy_manager = ProxyManager()
