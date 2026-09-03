import re
import time
import requests
import threading
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Rectangle

ACCOUNTS = [
    {
        "id": "m1",
        "name": "SuperAnt",
        "type": "Legacy (15YY...)",
        "address": "15YYrkm1io6uYxdPd4xr5nMKcrYutYV3wE",
        "url": "https://btcpowlab-pool.com/miner/15YYrkm1io6uYxdPd4xr5nMKcrYutYV3wE"
    },
    {
        "id": "m2",
        "name": "BtcMiningBrothers",
        "type": "SegWit (bc1q...)",
        "address": "bc1q7vrvymp6n4fqe22verd8de9k64nd724pw553vg",
        "url": "https://btcpowlab-pool.com/miner/bc1q7vrvymp6n4fqe22verd8de9k64nd724pw553vg"
    }
]

DASHBOARD_URL = "https://btcpowlab-pool.com/dashboard"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
})

cached_wallet = {}

miner_session_state = {
    acc["id"]: {
        "start_shares": None,
        "start_work": None,
        "last_shares": None,
        "last_work": None,
        "session_max_diff": 0.0
    }
    for acc in ACCOUNTS
}

global_session_state = {
    "start_pool_work": None,
    "last_pool_work": None,
    "pool_session_work": 0.0,
    "pool_session_best_diff": 0.0
}

def parse_num(val_str):
    if not val_str:
        return 0.0
    clean = re.sub(r"[^\d\.,KMGTBkmgtb]", "", str(val_str)).replace(",", "")
    match = re.match(r"^([\d\.]+)\s*([KMGTBkmgtb])?$", clean)
    if not match:
        return 0.0
    try:
        val = float(match.group(1))
        unit = (match.group(2) or "").upper()
        factors = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "B": 1e9}
        return val * factors.get(unit, 1.0)
    except ValueError:
        return 0.0

def fmt_diff(num):
    if num <= 0:
        return "Zoeken..."
    if num >= 1e12:
        return f"{num / 1e12:.2f} T"
    if num >= 1e9:
        return f"{num / 1e9:.2f} G"
    if num >= 1e6:
        return f"{num / 1e6:.2f} M"
    if num >= 1e3:
        return f"{num / 1e3:.2f} K"
    return f"{num:.2f}"

def get_wallet_balance(addr):
    try:
        cr = session.get(f"https://mempool.space/api/address/{addr}", timeout=4)
        if cr.status_code == 200:
            cdata = cr.json()
            funded = cdata.get("chain_stats", {}).get("funded_txo_sum", 0)
            spent = cdata.get("chain_stats", {}).get("spent_txo_sum", 0)
            return f"{(funded - spent) / 100000000:.8f} BTC"
    except Exception:
        pass
    return cached_wallet.get(addr, "0.00000000 BTC")

def fetch_fast_pool(account):
    addr = account["address"]
    pool_url = account["url"]
    aid = account["id"]
    st = miner_session_state[aid]

    hashrate = "0 H/s"
    shares_str = "0"
    alltime_best_diff = "Geen share"
    current_diff = "Auto (VarDiff)"
    pool_balance = "0 sats"

    try:
        r = session.get(pool_url, timeout=4)
        soup = BeautifulSoup(r.text, "html.parser")
        raw_text = soup.get_text(" | ", strip=True)

        hr_m = re.search(r"(\d+(?:\.\d+)?\s*[TGMK]?H/s)\s*\|\s*estimated effective", raw_text, re.I)
        if not hr_m:
            hr_m = re.search(r"(\d+(?:\.\d+)?\s*[TGMK]?H/s)", raw_text, re.I)
        if hr_m:
            hashrate = hr_m.group(1)

        sh_m = re.search(r"(\d+(?:[,\.]\d+)?)\s*credited shares", raw_text, re.I)
        if sh_m:
            shares_str = sh_m.group(1)

        diff_all = re.search(r"del bloque\.?\s*\|\s*([\d\.]+\s*[KMGTkmgt]?)", raw_text, re.I)
        if not diff_all:
            diff_all = re.search(r"Best share difficulty.*?\|\s*([\d\.]+\s*[KMGTkmgt]?)", raw_text, re.I)
        if diff_all:
            alltime_best_diff = diff_all.group(1).strip()

        cur_diff_m = re.search(r"(?:current|target|assigned)\s*diff(?:iculty)?.*?\|\s*([\d\.]+\s*[KMGTkmgt]?)", raw_text, re.I)
        if cur_diff_m:
            current_diff = cur_diff_m.group(1).strip()

        work_m = re.search(r"Demonstrated work.*?\|\s*([\d\.]+\s*[KMGTkmgt]?)\s*\|\s*normalized", raw_text, re.I)
        cur_work_num = 0.0
        if work_m:
            pool_balance = f"{work_m.group(1)} units"
            cur_work_num = parse_num(work_m.group(1))
        else:
            sats_m = re.search(r"(\d+(?:,\d+)?)\s*sats", raw_text, re.I)
            if sats_m:
                pool_balance = f"{sats_m.group(1)} sats"
                cur_work_num = parse_num(sats_m.group(1))

        cur_shares_num = int(re.sub(r"[^\d]", "", shares_str) or 0)

        if st["start_shares"] is None and cur_shares_num > 0:
            st["start_shares"] = cur_shares_num
            st["last_shares"] = cur_shares_num
            st["start_work"] = cur_work_num
            st["last_work"] = cur_work_num

        if st["last_shares"] is not None and cur_shares_num > st["last_shares"]:
            delta_shares = cur_shares_num - st["last_shares"]
            delta_work = cur_work_num - st["last_work"]
            if delta_shares > 0 and delta_work > 0:
                effective_share_diff = delta_work / delta_shares
                if effective_share_diff > st["session_max_diff"]:
                    st["session_max_diff"] = effective_share_diff

            st["last_shares"] = cur_shares_num
            st["last_work"] = cur_work_num

    except Exception:
        alltime_best_diff = "Update..."

    sess_diff_display = fmt_diff(st["session_max_diff"]) if st["session_max_diff"] > 0 else "Wachten..."

    return {
        "id": aid,
        "addr": addr,
        "hashrate": hashrate,
        "shares": shares_str,
        "current_diff": current_diff,
        "sess_best_diff": sess_diff_display,
        "alltime_best_diff": alltime_best_diff,
        "pool_balance": pool_balance,
        "wallet_balance": cached_wallet.get(addr, "Laden...")
    }

def fetch_personal_dashboard():
    data = {
        "hr_5m": "...",
        "hr_15m": "...",
        "hr_1h": "...",
        "workers": "...",
        "quality": "...",
        "work": "...",
        "height": "...",
        "difficulty": "...",
        "candidates": "0",
        "best_hash": "...",
        "pool_session_work": "0.00"
    }

    try:
        r = session.get(DASHBOARD_URL, timeout=4)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" | ", strip=True)

        all_hrs = re.findall(r"(\d+(?:\.\d+)?\s*[TGMK]?H/s)", text, re.I)
        if len(all_hrs) >= 3:
            data["hr_5m"] = all_hrs[0]
            data["hr_15m"] = all_hrs[1]
            data["hr_1h"] = all_hrs[2]

        m_work = re.search(r"MINERS\s*/\s*WORKERS.*?\|?\s*(\d+\s*/\s*\d+)", text, re.I)
        if not m_work:
            m_work = re.search(r"(\d+\s*/\s*\d+)", text)
        if m_work:
            data["workers"] = m_work.group(1).replace(" ", "")

        m_qual = re.search(r"CREDITABLE\s+SHARE\s+QUALITY.*?\|?\s*([\d\.]+\s*%)", text, re.I)
        if not m_qual:
            m_qual = re.search(r"([\d\.]+\s*%)\s*\|\s*[\d,]+\s*credited shares", text, re.I)
        if m_qual:
            data["quality"] = m_qual.group(1)

        m_norm = re.search(r"NORMALIZED\s+WORK.*?\|?\s*([\d\.]+\s*[BKMGTkmgt])", text, re.I)
        if not m_norm:
            m_norm = re.search(r"([\d\.]+\s*[BKMGTkmgt])\s*\|\s*vardiff-independent", text, re.I)
        
        pool_work_num = 0.0
        if m_norm:
            data["work"] = m_norm.group(1)
            pool_work_num = parse_num(m_norm.group(1))

        if pool_work_num > 0:
            if global_session_state["start_pool_work"] is None:
                global_session_state["start_pool_work"] = pool_work_num
                global_session_state["last_pool_work"] = pool_work_num
            work_delta = max(0.0, pool_work_num - global_session_state["start_pool_work"])
            data["pool_session_work"] = fmt_diff(work_delta)

        m_h = re.search(r"BITCOIN\s+HEIGHT.*?\|?\s*(\d{3}[\.,]\d{3}|\d{6})", text, re.I)
        if not m_h:
            m_h = re.search(r"Height\s*#?(\d{3}[\.,]\d{3}|\d{6})", text, re.I)
        if m_h:
            data["height"] = m_h.group(1)

        m_diff = re.search(r"NETWORK\s+DIFFICULTY.*?\|?\s*([\d\.]+\s*[TGMK])", text, re.I)
        if not m_diff:
            m_diff = re.search(r"(\d{2,3}\.\d{1,2}\s*T)", text)
        if m_diff:
            data["difficulty"] = m_diff.group(1)

        m_cand = re.search(r"NETWORK\s+CANDIDATES.*?\|?\s*(\d+)", text, re.I)
        if m_cand:
            data["candidates"] = m_cand.group(1)

        m_hash = re.search(r"\b(00000000000000[0-9a-fA-F]{50})\b", text)
        if not m_hash:
            m_hash = re.search(r"(00000000000000[0-9a-fA-F]{30,})", text)
        if m_hash:
            data["best_hash"] = m_hash.group(1).lower()

    except Exception:
        pass

    return data

class ColorCard(BoxLayout):
    def __init__(self, bg_color=(0.067, 0.102, 0.18, 1), **kwargs):
        super().__init__(**kwargs)
        self.bg_color = bg_color
        with self.canvas.before:
            Color(*self.bg_color)
            self.rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self._update_rect, pos=self._update_rect)

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

class PoWLabApp(App):
    def build(self):
        self.title = "PoW Lab Pro Monitor"
        self.ui = {}
        self.start_timestamp = time.strftime("%H:%M:%S")

        root = BoxLayout(orientation="vertical", spacing=8, padding=[8, 8, 8, 8])
        with root.canvas.before:
            Color(0.035, 0.055, 0.09, 1)
            self.bg = Rectangle(size=(2000, 4000), pos=(0, 0))

        header = ColorCard(bg_color=(0.067, 0.102, 0.18, 1), orientation="vertical", size_hint_y=None, height=65, padding=4)
        header.add_widget(Label(text="⚡ REALTIME MINER STATUS", font_size="16sp", bold=True, color=(0.22, 0.74, 0.97, 1)))
        self.ping_lbl = Label(text=f"Sessie actief sinds {self.start_timestamp}", font_size="11sp", color=(0.06, 0.73, 0.51, 1))
        header.add_widget(self.ping_lbl)
        root.add_widget(header)

        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
        content = BoxLayout(orientation="vertical", size_hint_y=None, spacing=8)
        content.bind(minimum_height=content.setter("height"))

        for acc in ACCOUNTS:
            card = ColorCard(bg_color=(0.067, 0.102, 0.18, 1), orientation="vertical", size_hint_y=None, height=275, padding=6, spacing=4)
            
            top = BoxLayout(size_hint_y=None, height=26)
            top.add_widget(Label(text=acc["name"], font_size="14sp", bold=True, halign="left", color=(0.97, 0.98, 0.99, 1)))
            top.add_widget(Label(text=acc["type"], font_size="10sp", color=(0.22, 0.74, 0.97, 1), halign="right"))
            card.add_widget(top)

            grid = GridLayout(cols=2, size_hint_y=None, height=170, spacing=4)
            self.add_stat(grid, "LIVE HASHRATE", (0.22, 0.74, 0.97, 1), f"hr_{acc['id']}")
            self.add_stat(grid, "CREDITED SHARES", (0.06, 0.73, 0.51, 1), f"sh_{acc['id']}")
            self.add_stat(grid, "HUIDIGE DIFF", (0.22, 0.74, 0.97, 1), f"curdiff_{acc['id']}")
            self.add_stat(grid, "SESSIE BEST DIFF", (0.13, 0.83, 0.93, 1), f"sess_diff_{acc['id']}")
            self.add_stat(grid, "ALL-TIME BEST DIFF", (0.96, 0.62, 0.04, 1), f"alltime_diff_{acc['id']}")
            self.add_stat(grid, "TOTAAL WORK (POOL)", (0.75, 0.52, 0.99, 1), f"work_{acc['id']}")
            card.add_widget(grid)

            w_box = ColorCard(bg_color=(0.035, 0.055, 0.09, 1), orientation="vertical", size_hint_y=None, height=45, padding=4)
            w_box.add_widget(Label(text="WALLET SALDO", font_size="9sp", color=(0.51, 0.57, 0.65, 1)))
            w_lbl = Label(text="Laden...", font_size="12sp", bold=True, color=(0.20, 0.83, 0.60, 1))
            w_box.add_widget(w_lbl)
            self.ui[f"wallet_{acc['id']}"] = w_lbl
            card.add_widget(w_box)

            content.add_widget(card)

        d_card = ColorCard(bg_color=(0.067, 0.102, 0.18, 1), orientation="vertical", size_hint_y=None, height=350, padding=6, spacing=4)
        d_top = BoxLayout(size_hint_y=None, height=26)
        d_top.add_widget(Label(text="GLOBAL DASHBOARD", font_size="14sp", bold=True, color=(0.97, 0.98, 0.99, 1)))
        d_top.add_widget(Label(text="LIVE STATS", font_size="10sp", color=(0.49, 0.23, 0.93, 1)))
        d_card.add_widget(d_top)

        d_grid = GridLayout(cols=3, size_hint_y=None, height=220, spacing=4)
        self.add_stat(d_grid, "HASHRATE 5M", (0.22, 0.74, 0.97, 1), "d_hr5")
        self.add_stat(d_grid, "HASHRATE 15M", (0.22, 0.74, 0.97, 1), "d_hr15")
        self.add_stat(d_grid, "HASHRATE 1H", (0.22, 0.74, 0.97, 1), "d_hr1h")
        self.add_stat(d_grid, "WORKERS", (0.06, 0.73, 0.51, 1), "d_workers")
        self.add_stat(d_grid, "QUALITY", (0.06, 0.73, 0.51, 1), "d_qual")
        self.add_stat(d_grid, "POOL NORM.", (0.75, 0.52, 0.99, 1), "d_work")
        self.add_stat(d_grid, "BTC HEIGHT", (0.96, 0.62, 0.04, 1), "d_height")
        self.add_stat(d_grid, "DIFFICULTY", (0.96, 0.62, 0.04, 1), "d_diff")
        self.add_stat(d_grid, "CANDIDATES", (0.97, 0.44, 0.44, 1), "d_cand")
        self.add_stat(d_grid, "POOL SESSIE", (0.13, 0.83, 0.93, 1), "d_p_sess_work")
        self.add_stat(d_grid, "SESSIE TIJD", (0.65, 0.55, 0.98, 1), "d_sess_time")
        self.add_stat(d_grid, "STATUS", (0.20, 0.83, 0.60, 1), "d_p_status")
        d_card.add_widget(d_grid)

        h_box = ColorCard(bg_color=(0.035, 0.055, 0.09, 1), orientation="vertical", size_hint_y=None, height=60, padding=4)
        h_box.add_widget(Label(text="POOL RECORD HASH (KOPIEERBAAR)", font_size="9sp", color=(0.51, 0.57, 0.65, 1)))
        self.hash_input = TextInput(text="Laden...", readonly=True, multiline=False, background_color=(0.035, 0.055, 0.09, 1), foreground_color=(0.22, 0.74, 0.97, 1), font_size="10sp")
        h_box.add_widget(self.hash_input)
        d_card.add_widget(h_box)

        content.add_widget(d_card)
        scroll.add_widget(content)
        root.add_widget(scroll)

        self.status = Label(text="Starten...", size_hint_y=None, height=22, font_size="10sp", color=(0.39, 0.45, 0.55, 1))
        root.add_widget(self.status)

        threading.Thread(target=self.wallet_refresh_loop, daemon=True).start()
        threading.Thread(target=self.fast_pool_loop, daemon=True).start()

        return root

    def add_stat(self, parent, title, color, key):
        box = ColorCard(bg_color=(0.035, 0.055, 0.09, 1), orientation="vertical", padding=2)
        box.add_widget(Label(text=title, font_size="8sp", color=(0.51, 0.57, 0.65, 1)))
        lbl = Label(text="...", font_size="11sp", bold=True, color=color)
        box.add_widget(lbl)
        self.ui[key] = lbl
        parent.add_widget(box)

    def wallet_refresh_loop(self):
        while True:
            for acc in ACCOUNTS:
                bal = get_wallet_balance(acc["address"])
                cached_wallet[acc["address"]] = bal
                Clock.schedule_once(lambda dt, a=acc["id"], b=bal: self.update_wallet_ui(a, b))
            time.sleep(45)

    def fast_pool_loop(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            while True:
                t0 = time.time()
                miner_future = executor.map(fetch_fast_pool, ACCOUNTS)
                dash_future = executor.submit(fetch_personal_dashboard)

                results = list(miner_future)
                dash_data = dash_future.result()

                elapsed = int((time.time() - t0) * 1000)
                Clock.schedule_once(lambda dt, r=results, d=dash_data, e=elapsed: self.apply_updates(r, d, e))
                time.sleep(2.0)

    def apply_updates(self, results, dash_data, elapsed):
        for data in results:
            aid = data["id"]
            if f"hr_{aid}" in self.ui:
                self.ui[f"hr_{aid}"].text = data["hashrate"]
                self.ui[f"sh_{aid}"].text = data["shares"]
                self.ui[f"curdiff_{aid}"].text = data["current_diff"]
                self.ui[f"sess_diff_{aid}"].text = data["sess_best_diff"]
                self.ui[f"alltime_diff_{aid}"].text = data["alltime_best_diff"]
                self.ui[f"work_{aid}"].text = data["pool_balance"]

        if dash_data:
            self.ui["d_hr5"].text = dash_data.get("hr_5m", "...")
            self.ui["d_hr15"].text = dash_data.get("hr_15m", "...")
            self.ui["d_hr1h"].text = dash_data.get("hr_1h", "...")
            self.ui["d_workers"].text = dash_data.get("workers", "...")
            self.ui["d_qual"].text = dash_data.get("quality", "...")
            self.ui["d_work"].text = dash_data.get("work", "...")
            self.ui["d_height"].text = dash_data.get("height", "...")
            self.ui["d_diff"].text = dash_data.get("difficulty", "...")
            self.ui["d_cand"].text = dash_data.get("candidates", "0")
            self.ui["d_p_sess_work"].text = dash_data.get("pool_session_work", "0.00")
            self.ui["d_sess_time"].text = self.start_timestamp
            self.ui["d_p_status"].text = "Live"

            best_h = dash_data.get("best_hash", "")
            if best_h and best_h != "...":
                self.hash_input.text = best_h

        t = time.strftime("%H:%M:%S")
        self.status.text = f"Live feed: {t} | Latency: {elapsed}ms"

    def update_wallet_ui(self, aid, val):
        if f"wallet_{aid}" in self.ui:
            self.ui[f"wallet_{aid}"].text = val

if __name__ == "__main__":
    PoWLabApp().run()
