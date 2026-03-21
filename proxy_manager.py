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

        self.PROXY_URL = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text"

    def fetch_proxies(self) -> int:
        print("\n🔄 Proxyk letöltése (ProxyScrape)...")
        raw_proxies = set()

        try:
            resp = requests.get(self.PROXY_URL, timeout=15)
            if resp.status_code == 200:
                for line in resp.text.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(":")
                    if len(parts) == 2:
                        ip = parts[0].strip()
                        port = parts[1].strip()
                        if self._is_valid_proxy(ip, port):
                            raw_proxies.add(f"{ip}:{port}")

                print(f"  ✅ {len(raw_proxies)} proxy letöltve")
            else:
                print(f"  ❌ HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ❌ Hiba: {str(e)[:60]}")

        with self.lock:
            self.proxies = [
                {"ip_port": p, "protocol": "unknown"}
                for p in raw_proxies
            ]
            random.shuffle(self.proxies)
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            self.last_fetch = time.time()
            self.tested = False

        print(f"  📦 {len(self.proxies)} proxy kész (tesztelésre vár)")
        return len(self.proxies)

    def _test_single_proxy(self, proxy_info: dict, timeout: int = 8) -> dict:
        ip_port = proxy_info["ip_port"]

        for proto in ["http", "socks5", "socks4"]:
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
        sample_size: int = 3000,
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
        print(f"   {max_workers} szál | {timeout}s timeout | Auto: HTTP→SOCKS5→SOCKS4")

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
        print(f"{'=' * 60}\n")
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
