import requests
import threading
import time
import random
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed


class ProxyManager:
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
            "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
            "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc",
            "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
            "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
            "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
        ]

    def fetch_proxies(self) -> int:
        print("\n🔄 Proxyk letöltése...")
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
                continue

        with self.lock:
            self.proxies = list(all_proxies)
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.last_fetch = time.time()
            self.tested = False

        print(f"  📦 {len(self.proxies)} proxy letöltve")
        return len(self.proxies)

    def _test_single_proxy(self, proxy_str: str, timeout: int = 6) -> bool:
        """
        Proxy tesztelése MICROSOFT LOGIN OLDALON!
        Nem google - hanem az igazi célpont ellen teszteljük.
        """
        proxies = {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}",
        }

        try:
            # Microsoft login oldalt teszteljük - ez az igazi teszt!
            r = requests.get(
                "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?"
                "client_info=1&haschrome=1&login_hint=test@hotmail.com"
                "&mkt=en&response_type=code"
                "&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                "&scope=profile%20openid%20offline_access"
                "%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
                "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite"
                "%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D",
                proxies=proxies,
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                allow_redirects=True,
            )

            # Ellenőrizzük hogy VALÓBAN Microsoft oldalt kaptunk
            text = r.text
            if r.status_code == 200 and len(text) > 5000:
                if "PPFT" in text or "urlPost" in text or "login.live.com" in text:
                    return True

            return False

        except:
            return False

    def test_and_filter(
        self,
        sample_size: int = 2000,
        max_workers: int = 400,
        timeout: int = 6,
    ) -> int:
        with self.lock:
            all_proxies = list(self.proxies)

        if not all_proxies:
            print("⚠️  Nincs proxy a teszteléshez!")
            return 0

        if len(all_proxies) <= sample_size:
            to_test = all_proxies
        else:
            to_test = random.sample(all_proxies, sample_size)

        total_to_test = len(to_test)
        print(f"\n🧪 {total_to_test} proxy tesztelése MICROSOFT LOGIN ellen...")
        print(f"   ({max_workers} szál, {timeout}s timeout)")

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

                if tested % 500 == 0 or tested == total_to_test:
                    print(
                        f"  📊 {tested}/{total_to_test} | "
                        f"✅ Működik: {len(working)} | "
                        f"❌ Halott: {failed}"
                    )

        with self.lock:
            self.proxies = working
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.tested = True

        print(f"\n{'=' * 55}")
        print(f"  🟢 {len(working)} MICROSOFT-TESZTELT proxy kész!")
        print(f"  ❌ {failed} nem működő proxy törölve")
        print(f"{'=' * 55}\n")

        return len(working)

    def fetch_and_test(self) -> int:
        self.fetch_proxies()
        return self.test_and_filter()

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
            proxy_str = next(self.proxy_cycle)
        return {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}",
        }

    def get_count(self) -> int:
        with self.lock:
            return len(self.proxies)

    def is_tested(self) -> bool:
        with self.lock:
            return self.tested

    def mark_bad(self, proxy_str: str):
        with self.lock:
            if proxy_str in self.proxies:
                self.proxies.remove(proxy_str)
                if self.proxies:
                    self.proxy_cycle = cycle(self.proxies)
                else:
                    self.proxy_cycle = None


proxy_manager = ProxyManager()
