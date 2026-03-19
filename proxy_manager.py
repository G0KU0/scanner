import requests
import threading
import time
import random
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed


class ProxyManager:
    """Automatikus proxy szerző, tesztelő és rotáló manager"""

    def __init__(self):
        self.proxies = []
        self.proxy_cycle = None
        self.lock = threading.Lock()
        self.last_fetch = 0
        self.tested = False

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

    # ================================================
    # PROXY LETÖLTÉS
    # ================================================
    def fetch_proxies(self) -> int:
        """Összes forrásból proxykat gyűjt"""
        print("\n🔄 Ingyenes proxyk letöltése...")
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
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.last_fetch = time.time()
            self.tested = False

        count = len(self.proxies)
        print(f"\n  📦 {count} proxy letöltve (még NINCS tesztelve)")
        return count

    # ================================================
    # PROXY TESZTELÉS
    # ================================================
    def _test_single_proxy(self, proxy_str: str, timeout: int = 4) -> bool:
        """Egy proxy tesztelése - működik-e?"""
        proxies = {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}",
        }

        # 1. próba: Google (leggyorsabb)
        try:
            r = requests.head(
                "http://www.google.com/",
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
            )
            if r.status_code < 500:
                return True
        except:
            pass

        # 2. próba: httpbin
        try:
            r = requests.get(
                "http://httpbin.org/ip",
                proxies=proxies,
                timeout=timeout,
            )
            if r.status_code == 200:
                return True
        except:
            pass

        return False

    def test_and_filter(
        self,
        sample_size: int = 1500,
        max_workers: int = 300,
        timeout: int = 4,
    ) -> int:
        """
        Random mintát vesz a proxykból, teszteli párhuzamosan,
        és CSAK a működőket tartja meg.
        """
        with self.lock:
            all_proxies = list(self.proxies)

        if not all_proxies:
            print("⚠️  Nincs proxy a teszteléshez!")
            return 0

        # Mintavétel
        if len(all_proxies) <= sample_size:
            to_test = all_proxies
        else:
            to_test = random.sample(all_proxies, sample_size)

        total_to_test = len(to_test)
        print(f"\n🧪 {total_to_test} proxy tesztelése ({max_workers} szálon, {timeout}s timeout)...")

        working = []
        tested = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._test_single_proxy, p, timeout): p
                for p in to_test
            }

            for future in as_completed(futures):
                proxy = futures[future]
                tested += 1

                try:
                    if future.result():
                        working.append(proxy)
                    else:
                        failed += 1
                except:
                    failed += 1

                # Haladás kiírása
                if tested % 300 == 0 or tested == total_to_test:
                    print(
                        f"  📊 Tesztelve: {tested}/{total_to_test} | "
                        f"✅ Működik: {len(working)} | "
                        f"❌ Halott: {failed}"
                    )

        # Csak a működőket tartjuk meg
        with self.lock:
            self.proxies = working
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.tested = True

        print(f"\n{'=' * 55}")
        print(f"  🟢 {len(working)} TESZTELT, MŰKÖDŐ proxy készen áll!")
        print(f"  ❌ {failed} halott proxy eltávolítva")
        print(f"{'=' * 55}\n")

        return len(working)

    def fetch_and_test(self) -> int:
        """Letölt ÉS tesztel egyben"""
        self.fetch_proxies()
        return self.test_and_filter()

    # ================================================
    # PROXY HASZNÁLAT
    # ================================================
    def _is_valid_proxy(self, ip: str, port: str) -> bool:
        """Érvényes ip:port ellenőrzés"""
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
        """Proxy string formátumban (ip:port)"""
        with self.lock:
            if not self.proxies or not self.proxy_cycle:
                return None
            return next(self.proxy_cycle)

    def get_count(self) -> int:
        """Működő proxyk száma"""
        with self.lock:
            return len(self.proxies)

    def is_tested(self) -> bool:
        """Voltak-e már tesztelve a proxyk?"""
        with self.lock:
            return self.tested

    def mark_bad(self, proxy_str: str):
        """Futás közben halottnak jelölt proxy eltávolítása"""
        with self.lock:
            if proxy_str in self.proxies:
                self.proxies.remove(proxy_str)
                # Új cycle a maradékból
                if self.proxies:
                    self.proxy_cycle = cycle(self.proxies)
                else:
                    self.proxy_cycle = None


# Globális proxy manager
proxy_manager = ProxyManager()
