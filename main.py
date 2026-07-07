#!/usr/bin/env python3
"""AnberReMach — interaktywny frontend SDL2 dla biblioteki ISA + remach_lib (Re, Ma, q, EAS) na Anbernic.

Atmosfera wzorcowa ISO 2533:1975 / ICAO Doc 7488/3 / US Std Atmosphere 1976.

Ekrany: MENU → PUNKT / TABELA / WYKRES / INWERSJA / NORMY.

Sterowanie (wewnątrz ekranu):
  D-pad ↑/↓       — nawigacja między polami (zmiennymi)
  D-pad ←/→       — zmiana wartości aktywnego pola (krok bazowy × mnożnik Y)
                    w TABELI po obliczeniu: przewijanie wyników
  L1 / R1         — zmiana wartości aktywnego pola z mnożnikiem ×10
  L2 / R2         — zmiana wartości aktywnego pola z mnożnikiem ×100
  A (BTN_SOUTH)   — wykonaj akcję ekranu (oblicz / generuj)
  B (BTN_EAST)    — wstecz (w menu = wyjście)
  X (BTN_NORTH)   — cykl ΔT
  Y (BTN_WEST)    — PUNKT: cykl mnożnika kroku ×0.1/×1/×10/×100/×1000/
                    ×10000/×100000; TABELA: zapis CSV
  SELECT          — PUNKT: cykl jednostki wysokości (m/ft/km); inne: czyść
  START           — PUNKT: cykl jednostki prędkości (m/s, km/h, kt, mph, ft/s)
  MENU            — wyjście do menu Anbernica

Mapowanie kodów evdev — referencja: rg40xx-buttons (event1):
  A=304, B=305, X=307, Y=308, L1=310, R1=311, L2=312, R2=313,
  SELECT=314, START=315, MENU=354, BTN_MODE=316 (oba honorowane jako exit).
  D-pad: ABS_HAT0X=16, ABS_HAT0Y=17 (dyskretne ±1).

Diagnostyka: w prawym górnym rogu wyświetla się ostatni kod EV_KEY,
co pozwala dopasować fizyczne przyciski tej konsoli do ich kodów.
"""
import os, sys, ctypes, struct, time
from pathlib import Path

os.environ.pop('SDL_VIDEODRIVER', None)
os.environ['PYSDL2_DLL_PATH'] = '/usr/lib'

import sdl2
from power_screen import ScreenPowerToggle
from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# Fallback dla kopii źródłowej (bez zbundlowanego isa_lib): siostrzane apps/atmosfera
if not (_HERE / 'isa_lib').is_dir():
    _ATMO = _HERE.parent / 'atmosfera'
    if _ATMO.is_dir() and str(_ATMO) not in sys.path:
        sys.path.insert(0, str(_ATMO))

from isa_lib import isa, pressure_altitude, density_altitude
from isa_lib import constants as C
# AnberReMach — biblioteka liczb podobieństwa (Re, Ma, q, EAS).
from remach_lib import point as remach_point

# ── stałe wyświetlania ───────────────────────────────────────────────────────
W, H = 640, 480
FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
FONT_SM, FONT_MD, FONT_LG = 12, 15, 20

BG  = (10, 14, 22, 255)
FG  = (210, 220, 230, 255)
ACC = (80, 180, 255, 255)
GRN = (80, 220, 120, 255)
YEL = (255, 210, 60, 255)
RED = (255, 90, 80, 255)
DIM = (120, 130, 145, 255)
SEL = (255, 230, 90, 255)
SEP = (40, 55, 75, 255)

LOG = Path('/mnt/data/anberremach.log')
WYK_DIR = Path('/mnt/data/anberremach_wykresy')
WYK_DIR.mkdir(parents=True, exist_ok=True)
TAB_DIR = Path('/mnt/data/anberremach_tabele')
TAB_DIR.mkdir(parents=True, exist_ok=True)

# ── evdev — gamepad (event1) ────────────────────────────────────────────────
EV_KEY, EV_ABS = 1, 3
_EV = struct.Struct('llHHi')
EV_SIZE = _EV.size

# Kody przycisków RG40XX V (event1) — referencja: skill rg40xx-buttons.
# Kanoniczny standard Linux input: BTN_SOUTH/EAST/NORTH/WEST + BTN_TL/TR/TL2/TR2.
BTN_A, BTN_B, BTN_X, BTN_Y = 304, 305, 307, 308
BTN_L1, BTN_R1, BTN_L2, BTN_R2 = 310, 311, 312, 313
BTN_SELECT, BTN_START = 314, 315
BTN_MODE = 316
KEY_MENU = 354
EXIT_KEYS = {BTN_MODE, KEY_MENU}
SHOULDER_KEYS = {BTN_L1, BTN_R1, BTN_L2, BTN_R2}

# Lewy analog (RG40XX V, event1) — empiryczne kody z anbercc/main.py:
# ABS code 3 = oś Y lewej gałki, zakres ~±4096.
ABS_LY_CODE = 3
ANALOG_DEADZONE = 400      # ~10% z 4096 (RG40XX V zakres ±4096)
ANALOG_MAX      = 4096
ANALOG_SLOW_MS  = 120
ANALOG_FAST_MS  = 20

# ── modele danych ekranów ───────────────────────────────────────────────────
# AnberReMach: jednostki prędkości — oddzielne od jednostki wysokości
V_UNITS = ['m/s', 'km/h', 'kt', 'mph', 'ft/s']
V_UNIT_TO_MS = {  # mnożnik: wartość_w_jednostce × współczynnik → m/s
    'm/s':  1.0,
    'km/h': 1.0/3.6,
    'kt':   0.5144444444,
    'mph':  0.44704,
    'ft/s': 0.3048,
}
# AnberReMach: tryby kroku — D-pad ←→ używa wybranego mnożnika; Y cykluje.
STEP_MODES = [0.1, 1, 10, 100, 1000, 10000, 100000]  # mnożniki krokowe (cykl Y)

UNITS = ['m', 'ft', 'km']           # jednostki wysokości
UNIT_TO_M = {'m': 1.0, 'ft': 0.3048, 'km': 1000.0}  # przelicznik wyświetlania h
DT_CYCLE = [0.0, 15.0, -15.0, 30.0] # ISA, ISA+15, ISA-15, ISA+30


# ── główna klasa aplikacji ──────────────────────────────────────────────────
class AnberReMach:
    def __init__(self):
        # Rotacja logu: >1 MB → zostaw ostatnie ~200 kB
        try:
            if LOG.exists() and LOG.stat().st_size > 1_000_000:
                tail = LOG.read_text(encoding='utf-8', errors='replace')[-200_000:]
                LOG.write_text(tail, encoding='utf-8')
        except Exception:
            pass
        self.log = LOG.open('a', encoding='utf-8')
        self.log.write(f'\n[{time.strftime("%H:%M:%S")}] start\n'); self.log.flush()

        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_EVENTS) != 0:
            err = sdl2.SDL_GetError()
            self.log.write(f'SDL_Init FAIL: {err}\n'); self.log.flush()
            self.log.close()
            sys.exit(f'SDL_Init FAIL: {err}')

        self.win = sdl2.SDL_CreateWindow(
            b'AnberReMach',
            sdl2.SDL_WINDOWPOS_UNDEFINED, sdl2.SDL_WINDOWPOS_UNDEFINED,
            0, 0,
            sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP | sdl2.SDL_WINDOW_SHOWN
        )
        self.ren = sdl2.SDL_CreateRenderer(self.win, -1, sdl2.SDL_RENDERER_SOFTWARE)
        if not self.ren:
            self.ren = sdl2.SDL_CreateRenderer(self.win, -1, 0)

        self.img = Image.new('RGBA', (W, H), BG)
        self.draw = ImageDraw.Draw(self.img)
        self.fsm = ImageFont.truetype(FONT_PATH, FONT_SM)
        self.fmd = ImageFont.truetype(FONT_PATH, FONT_MD)
        self.flg = ImageFont.truetype(FONT_PATH, FONT_LG)

        # gamepad
        self.gp_fd = None
        try:
            self.gp_fd = os.open('/dev/input/event1', os.O_RDONLY | os.O_NONBLOCK)
            self.log.write('gamepad event1 OK\n'); self.log.flush()
        except Exception as e:
            self.log.write(f'gamepad FAIL: {e}\n'); self.log.flush()

        self._tex = None

        # stan aplikacji
        self.screen = 'menu'    # menu / point / table / plot / invert / norms
        self.menu_idx = 0
        self.norms_scroll = 0

        # diagnostyka — kod ostatnio naciśniętego przycisku
        self.last_btn = '—'

        # PUNKT
        self.point_h = 11000.0  # ZAWSZE [m] SI; jednostka tylko do wyświetlania i kroku (jak point_V)
        self.point_dT_idx = 0
        self.point_unit_idx = 0
        self.point_V = 100.0    # AnberReMach: prędkość TAS [m/s] (zawsze SI w pamięci)
        self.point_V_unit_idx = 0   # 0=m/s, 1=km/h, 2=kt, 3=mph, 4=ft/s
        self.point_L = 1.0      # AnberReMach: długość char. [m]
        self.point_field = 0    # 0=h, 1=V, 2=L (jednostki: SELECT=h, START=V; ΔT: X)
        self.step_mode_idx = 1  # 0=×0.1, 1=×1 (domyślny), 2=×10, ... (cykl Y)
        self.point_scroll = 0   # offset wierszy wyjścia (gdy nie mieści się na ekranie)

        # TABELA
        self.tab_h0 = 0.0
        self.tab_h1 = 20000.0
        self.tab_step = 1000.0
        self.tab_dT = 0.0
        self.tab_field = 0      # 0=h0, 1=h1, 2=step, 3=dT
        self.tab_scroll = 0
        self.tab_rows = []
        self.tab_status = ''
        self.tab_save_path = None

        # WYKRES
        self.plot_h0 = 0.0
        self.plot_h1 = 30000.0
        self.plot_n = 150
        self.plot_field = 0
        self.plot_path = None
        self.plot_status = ''

        # INWERSJA
        self.inv_mode = 0       # 0=p->h, 1=rho->h
        self.inv_p = 50000.0
        self.inv_rho = 0.5
        self.inv_dT = 0.0
        self.inv_field = 0      # 0=mode, 1=value, 2=dT
        self.inv_result = None
        self.inv_status = ''

        # Lewy analog — stan auto-repeat (wzorzec z anbercc/main.py)
        self.analog_y = 0
        self.analog_next = 0.0

    # ── input ───────────────────────────────────────────────────────────────
    def _poll_gamepad(self):
        if self.gp_fd is None:
            return []
        out = []
        try:
            while True:
                buf = os.read(self.gp_fd, EV_SIZE)
                if not buf or len(buf) < EV_SIZE:
                    break
                _s, _us, etype, code, value = _EV.unpack(buf)
                if value > 0x7FFFFFFF:
                    value -= 0x100000000
                out.append((etype, code, value))
        except BlockingIOError:
            pass
        except Exception as e:
            self.log.write(f'gamepad read ERR: {e}\n'); self.log.flush()
        return out

    # ── pomocnicze tekst/grafika ───────────────────────────────────────────
    def _text(self, x, y, txt, font=None, color=FG):
        self.draw.text((x, y), txt, font=font or self.fsm, fill=color)

    def _hline(self, y, color=SEP):
        self.draw.line([(0, y), (W, y)], fill=color, width=1)

    def _box(self, x, y, w, h, color=SEP):
        self.draw.rectangle([(x, y), (x+w, y+h)], outline=color)

    # ── render: nagłówek + stopka wspólne ──────────────────────────────────
    def _header(self, title):
        self._text(8, 6, '⬡ AnberReMach', self.fmd, ACC)
        tw = self.fmd.getlength(title)
        self._text(W//2 - tw//2, 6, title, self.fmd, FG)
        ts = time.strftime('%H:%M')
        self._text(W - 50, 6, ts, self.fmd, DIM)
        # pasek diagnostyki — zawsze pokazuje ostatni kod EV_KEY
        self._text(W - 160, 22, f'btn:{self.last_btn}', self.fsm, DIM)
        self._hline(28)

    def _footer(self, hints):
        self._hline(H - 22)
        self._text(8, H - 16, hints, self.fsm, DIM)

    # ── EKRAN: MENU ─────────────────────────────────────────────────────────
    def render_menu(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('MENU')

        items = [
            ('PUNKT',    'wartości na zadanej wysokości'),
            ('TABELA',   'profil h0..h1 z krokiem'),
            ('WYKRES',   'profil T, p, rho, a -> PNG'),
            ('INWERSJA', 'wysokość z p lub rho'),
            ('NORMY',    'opis zmiennych + odniesienia'),
        ]
        y = 56
        for i, (name, desc) in enumerate(items):
            sel = (i == self.menu_idx)
            col = SEL if sel else FG
            mark = '►' if sel else ' '
            self._text(60, y, f'{mark}  {name}', self.flg, col)
            self._text(220, y + 6, desc, self.fsm, DIM if not sel else FG)
            y += 42

        sl = isa(0.0)
        info = (f'ISA SL: T0={sl["T_K"]:.2f} K  p0={sl["p_Pa"]:.0f} Pa  '
                f'rho0={sl["rho_kg_m3"]:.4f} kg/m³  a0={sl["a_m_s"]:.2f} m/s')
        self._text(8, H - 50, info, self.fsm, DIM)

        self._footer('A=wybierz   D-pad ↑↓ / L-stick=nawigacja   B/MENU=wyjście')

    # ── EKRAN: PUNKT ───────────────────────────────────────────────────────
    def render_point(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('PUNKT — Re/Ma/q (ISA + remach_lib)')

        unit = UNITS[self.point_unit_idx]
        dT = DT_CYCLE[self.point_dT_idx]

        v_unit = V_UNITS[self.point_V_unit_idx]
        v_in_unit = self.point_V / V_UNIT_TO_MS[v_unit]
        # Info nad polami: jednostki + ΔT (sterowane przez SELECT/START/X — nie polami)
        step_factor = STEP_MODES[self.step_mode_idx]
        self._text(20, 36, f'h:{unit}   V:{v_unit}   ΔT:{dT:+g}   krok:×{step_factor}', self.fsm, DIM)
        # W1: point_h trzymane ZAWSZE w metrach — jednostka tylko do wyświetlania
        h_in_unit = self.point_h / UNIT_TO_M[unit]
        if unit == 'km':
            h_disp = f'{h_in_unit:.3f}   (= {self.point_h:.1f} m)'
        elif unit == 'ft':
            h_disp = f'{h_in_unit:.1f}   (= {self.point_h:.1f} m)'
        else:
            h_disp = f'{self.point_h:.1f}'
        rows = [
            (f'Wysokość [{unit}]:', h_disp),
            (f'V [{v_unit}]:',      f'{v_in_unit:.2f}   (= {self.point_V:.2f} m/s)'),
            ('L [m]:',               f'{self.point_L:.3f}   (długość charakterystyczna)'),
        ]
        # Krótkie wyjaśnienie ΔT — zawsze widoczne pod nagłówkiem
        self._text(420, 36, 'ΔT = odchyłka od ISA', self.fsm, DIM)
        self._text(420, 50, 'T(h) = T_ISA(h) + ΔT', self.fsm, DIM)
        self._text(420, 64, 'np. ISA+15 = gorący dzień', self.fsm, DIM)
        self._text(420, 78, '    ISA-15 = zimny dzień', self.fsm, DIM)
        y = 50
        for i, (lab, val) in enumerate(rows):
            sel = (i == self.point_field)
            col = SEL if sel else FG
            mark = '►' if sel else ' '
            self._text(20, y, f'{mark} {lab}', self.fmd, col)
            self._text(200, y, val, self.fmd, col)
            y += 26

        self._hline(y + 4)
        y += 12

        try:
            r = isa(self.point_h, dT=dT)   # point_h zawsze w metrach (W1)
        except ValueError as e:
            self._text(20, y, f'BŁĄD: {e}', self.fmd, RED)
            self._footer('↑↓=pole  ←→=±krok  L1/R1=×10  L2/R2=×100  X=ΔT  Y=mnożnik  B=menu')
            return

        labels = [
            ('h geopot.',     f'{r["h_geo_m"]:.2f} m', f'{r["h_geo_m"]/0.3048:.1f} ft'),
            ('h geom.',       f'{r["h_geom_m"]:.2f} m', f'{r["h_geom_m"]/0.3048:.1f} ft'),
            ('warstwa',       f'{r["layer"]}', ''),
            ('T',             f'{r["T_K"]:.3f} K', f'{r["T_K"]-273.15:+.2f} °C'),
            ('p',             f'{r["p_Pa"]:.2f} Pa', f'{r["p_Pa"]/100:.3f} hPa'),
            ('rho',           f'{r["rho_kg_m3"]:.5f} kg/m³', ''),
            ('a',             f'{r["a_m_s"]:.3f} m/s', f'{r["a_m_s"]*3.6:.2f} km/h'),
            ('mu',            f'{r["mu_Pa_s"]:.3e} Pa·s', ''),
            ('sigma=rho/rho0',f'{r["sigma"]:.5f}', ''),
            ('delta=p/p0',    f'{r["delta"]:.5f}', ''),
            ('theta=T/T0',    f'{r["theta"]:.5f}', ''),
        ]
        # AnberReMach: dorzucamy Re/Ma/q/EAS jako kolejne wiersze do labels (jednorodne scroll)
        try:
            rm = remach_point(V=self.point_V, L=self.point_L, h_geo=r['h_geo_m'], dT=dT)
            labels.append(('— AnberReMach —', '', ''))
            labels.append(('Re',  f'{rm["Re"]:.3e}', ''))
            labels.append(('Ma',  f'{rm["Ma"]:.4f}', ''))
            labels.append(('q',   f'{rm["q_Pa"]:.2f} Pa', f'{rm["q_Pa"]/100:.2f} hPa'))
            labels.append(('EAS', f'{rm["EAS"]:.2f} m/s', f'{rm["EAS"]*3.6:.1f} km/h'))
            labels.append(('nu',  f'{rm["nu"]:.3e} m²/s', ''))
        except Exception as _e:
            labels.append(('remach ERR', str(_e)[:40], ''))

        # Scroll: pokaż tylko wiersze które mieszczą się przed stopką
        avail_px = (H - 50) - y
        max_rows = max(4, avail_px // 16)
        total = len(labels)
        self.point_scroll = max(0, min(max(0, total - max_rows), self.point_scroll))
        shown = labels[self.point_scroll : self.point_scroll + max_rows]
        for lab, v1, v2 in shown:
            self._text(20, y, lab, self.fsm, ACC)
            self._text(170, y, v1, self.fsm, FG)
            if v2:
                self._text(360, y, v2, self.fsm, DIM)
            y += 16
        # wskaźnik scroll (gdy są ukryte wiersze)
        if total > max_rows:
            scroll_info = f'  ▲{self.point_scroll}  ▼{total - self.point_scroll - max_rows}'
            self._text(W - 100, H - 50, scroll_info, self.fsm, DIM)

        # legenda zmiennych — TYLKO jeśli zostało miejsce
        if y < H - 38:
            self._text(20, y + 2, 'σ=ρ/ρ0 δ=p/p0 θ=T/T0   ISO 2533 / ICAO 7488/3', self.fsm, DIM)
        # AnberReMach: dodatkowo Re/Ma/q/EAS (V=100 m/s, L=1.0 m — domyślne)
        self._footer('↑↓=pole ←→=±krok L1R1=×10 L2R2=×100 Y=mnożnik X=ΔT SEL=jedn.h START=jedn.V B=menu')

    # ── EKRAN: TABELA ──────────────────────────────────────────────────────
    def _recompute_table(self):
        h0, h1, step, dT = self.tab_h0, self.tab_h1, self.tab_step, self.tab_dT
        self.tab_status = ''
        if step <= 0 or h1 <= h0:
            self.tab_rows = []
            self.tab_status = 'BŁĄD: wymagane krok>0 i h1>h0'
            return
        LIMIT = 200
        rows = []
        h = h0
        while h <= h1 + 1e-6:
            if len(rows) >= LIMIT:
                self.tab_status = f'UWAGA: obcięto do {LIMIT} wierszy (koniec na h={h-step:.0f} m)'
                break
            try:
                r = isa(h, dT=dT)
            except ValueError as e:
                self.tab_status = f'UWAGA: przerwano na h={h:.0f} m ({e})'
                break
            rows.append(r)
            h += step
        self.tab_rows = rows
        if rows and not self.tab_status:
            self.tab_status = f'OK: {len(rows)} wierszy'

    def render_table(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('TABELA — profil ISA')

        rows = [
            ('h0:  ', f'{self.tab_h0:.0f} m'),
            ('h1:  ', f'{self.tab_h1:.0f} m'),
            ('krok:', f'{self.tab_step:.0f} m'),
            ('ΔT:  ', f'{self.tab_dT:+.0f} K'),
        ]
        for i, (lab, val) in enumerate(rows):
            sel = (i == self.tab_field)
            col = SEL if sel else FG
            mark = '►' if sel else ' '
            x = 10 + i*155
            self._text(x, 36, f'{mark}{lab}', self.fsm, col)
            self._text(x + 50, 36, val, self.fsm, col)
        self._hline(58)

        cols = ('h[m]', 'T[K]', 'T[°C]', 'p[Pa]', 'rho[kg/m³]', 'a[m/s]', 'sigma')
        xs = (10, 95, 170, 240, 340, 460, 540)
        y = 64
        for i, c in enumerate(cols):
            self._text(xs[i], y, c, self.fsm, ACC)
        self._hline(y + 14)
        y += 18

        if not self.tab_rows:
            self._text(20, y + 10, 'Brak danych. Naciśnij A by obliczyć.', self.fsm, DIM)
            if self.tab_status:
                col = GRN if 'OK' in self.tab_status else (RED if 'BŁĄD' in self.tab_status else YEL)
                self._text(20, H - 40, self.tab_status, self.fsm, col)
            self._footer('↑↓=pole  ←→=±krok  L1R1=×10 L2R2=×100  A=oblicz  Y=zapis  SEL=czyść  B=menu')
            return

        max_rows = (H - y - 40) // 14
        rows_v = self.tab_rows[self.tab_scroll:self.tab_scroll + max_rows]
        for r in rows_v:
            vals = (
                f'{r["h_geo_m"]:.0f}',
                f'{r["T_K"]:.2f}',
                f'{r["T_K"]-273.15:+.2f}',
                f'{r["p_Pa"]:.0f}',
                f'{r["rho_kg_m3"]:.5f}',
                f'{r["a_m_s"]:.2f}',
                f'{r["sigma"]:.4f}',
            )
            for i, v in enumerate(vals):
                self._text(xs[i], y, v, self.fsm, FG)
            y += 14

        n = len(self.tab_rows)
        self._text(W - 130, H - 38,
                   f'pozycja {self.tab_scroll+1}-{min(self.tab_scroll+max_rows, n)}/{n}',
                   self.fsm, DIM)

        if self.tab_status:
            col = GRN if 'OK' in self.tab_status else (RED if 'BŁĄD' in self.tab_status else YEL)
            self._text(8, H - 38, self.tab_status, self.fsm, col)

        self._footer('↑↓=pole  ←→/L-stick=scroll  L1R1=×10 L2R2=×100  A=oblicz  Y=zapis  SEL=czyść  B=menu')

    # ── EKRAN: WYKRES ──────────────────────────────────────────────────────
    def render_plot(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('WYKRES — profil ISA')

        rows = [
            ('h0:', f'{self.plot_h0:.0f} m'),
            ('h1:', f'{self.plot_h1:.0f} m'),
            ('n: ', f'{self.plot_n}'),
        ]
        for i, (lab, val) in enumerate(rows):
            sel = (i == self.plot_field)
            col = SEL if sel else FG
            mark = '►' if sel else ' '
            x = 10 + i*200
            self._text(x, 36, f'{mark}{lab} {val}', self.fmd, col)
        self._hline(64)

        if self.plot_path and Path(self.plot_path).exists():
            try:
                img = Image.open(self.plot_path).convert('RGBA')
                maxw, maxh = W - 20, H - 120
                img.thumbnail((maxw, maxh))
                ix = (W - img.width) // 2
                iy = 72
                self.img.paste(img, (ix, iy))
            except Exception as e:
                self._text(20, 80, f'Błąd odczytu PNG: {e}', self.fsm, RED)
        else:
            self._text(20, 80, 'Naciśnij A aby wygenerować wykres.', self.fmd, DIM)
            self._text(20, 100, 'Wynik zostanie zapisany w:', self.fsm, DIM)
            self._text(20, 116, str(WYK_DIR) + '/', self.fsm, ACC)

        if self.plot_status:
            col = GRN if 'OK' in self.plot_status else (RED if 'BŁĄD' in self.plot_status else YEL)
            self._text(20, H - 40, self.plot_status, self.fsm, col)

        self._footer('↑↓=pole  ←→=±krok  L1R1=×10 L2R2=×100  A=generuj  SEL=czyść  B=menu')

    def _generate_plot(self):
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except Exception as e:
            self.plot_status = f'BŁĄD: brak matplotlib ({e})'
            return
        try:
            n = max(2, int(self.plot_n))
            h0, h1 = self.plot_h0, self.plot_h1
            if h1 <= h0:
                self.plot_status = 'BŁĄD: h1 musi być > h0'
                return
            hs = [h0 + (h1 - h0) * i / (n - 1) for i in range(n)]
            T, p, rho, a = [], [], [], []
            for h in hs:
                r = isa(h)
                T.append(r['T_K']); p.append(r['p_Pa'])
                rho.append(r['rho_kg_m3']); a.append(r['a_m_s'])
            fig, axes = plt.subplots(2, 2, figsize=(8, 6), dpi=100)
            (ax1, ax2), (ax3, ax4) = axes
            for ax, y, lab, c in (
                (ax1, T,   'T [K]',          'tab:red'),
                (ax2, p,   'p [Pa]',         'tab:blue'),
                (ax3, rho, 'rho [kg/m^3]',   'tab:green'),
                (ax4, a,   'a [m/s]',        'tab:orange'),
            ):
                ax.plot(y, hs, color=c, lw=1.5)
                ax.set_ylabel('h [m]')
                ax.set_xlabel(lab)
                ax.grid(True, alpha=0.3)
            fig.suptitle(f'Profil ISA: h={h0:.0f}..{h1:.0f} m, n={n}')
            fig.tight_layout()
            out = WYK_DIR / f'isa_{int(h0)}_{int(h1)}_{n}.png'
            fig.savefig(out)
            plt.close(fig)
            self.plot_path = str(out)
            self.plot_status = f'OK: {out.name}'
            self.log.write(f'wykres zapisany: {out}\n'); self.log.flush()
        except Exception as e:
            self.plot_status = f'BŁĄD: {e}'

    def _save_table_csv(self):
        if not self.tab_rows:
            self.tab_status = 'BŁĄD: brak danych do zapisu (najpierw A=oblicz)'
            return
        try:
            ts = time.strftime('%Y%m%d_%H%M%S')
            out = TAB_DIR / f'isa_tab_{int(self.tab_h0)}_{int(self.tab_h1)}_dT{int(self.tab_dT):+d}_{ts}.csv'
            with out.open('w', encoding='utf-8') as f:
                f.write(f'# AnberReMach tabela ISO 2533:1975 / ICAO Doc 7488/3\n')
                f.write(f'# h0={self.tab_h0} m  h1={self.tab_h1} m  step={self.tab_step} m  dT={self.tab_dT:+g} K\n')
                f.write('# decimal=point\n')
                f.write('h_geo_m;T_K;T_C;p_Pa;rho_kg_m3;a_m_s;sigma;delta;theta\n')
                for r in self.tab_rows:
                    f.write(
                        f'{r["h_geo_m"]:.2f};{r["T_K"]:.4f};{r["T_K"]-273.15:+.4f};'
                        f'{r["p_Pa"]:.4f};{r["rho_kg_m3"]:.6f};{r["a_m_s"]:.4f};'
                        f'{r["sigma"]:.6f};{r["delta"]:.6f};{r["theta"]:.6f}\n'
                    )
            self.tab_save_path = str(out)
            self.tab_status = f'OK: zapisano {out.name} ({len(self.tab_rows)} wierszy)'
            self.log.write(f'tabela zapisana: {out}\n'); self.log.flush()
        except Exception as e:
            self.tab_status = f'BŁĄD: {e}'

    def _clear_data(self):
        if self.screen == 'table':
            self.tab_rows = []
            self.tab_scroll = 0
            self.tab_status = 'wyczyszczono'
            self.tab_save_path = None
        elif self.screen == 'plot':
            self.plot_path = None
            self.plot_status = 'wyczyszczono'
        elif self.screen == 'invert':
            self.inv_result = None
            self.inv_status = ''

    # ── EKRAN: INWERSJA ────────────────────────────────────────────────────
    def render_invert(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('INWERSJA — wysokość z p lub rho')

        mode_name = 'p → h_p (wys. cisnieniowa)' if self.inv_mode == 0 else 'rho → h_rho (wys. gestosciowa)'
        val_lab = 'p [Pa]' if self.inv_mode == 0 else 'rho [kg/m³]'
        val_v = self.inv_p if self.inv_mode == 0 else self.inv_rho

        rows = [
            ('Tryb:', mode_name),
            (val_lab + ':', f'{val_v:.4f}' if self.inv_mode == 1 else f'{val_v:.1f}'),
            ('ΔT:',   f'{self.inv_dT:+.0f} K  (tylko dla rho)'),
        ]
        for i, (lab, val) in enumerate(rows):
            sel = (i == self.inv_field)
            col = SEL if sel else FG
            mark = '►' if sel else ' '
            self._text(20, 40 + i*24, f'{mark} {lab}', self.fmd, col)
            self._text(160, 40 + i*24, val, self.fmd, col)

        self._hline(118)

        if self.inv_result is not None:
            h = self.inv_result
            self._text(20, 128, 'Wynik:', self.fmd, ACC)
            self._text(20, 154, f'h_geo  = {h:.2f} m', self.fmd, FG)
            try:
                from isa_lib import geometric
                hgm = geometric(h)
                self._text(20, 178, f'h_geom = {hgm:.2f} m   ({hgm/0.3048:.1f} ft)', self.fmd, FG)
                r = isa(h, dT=(self.inv_dT if self.inv_mode == 1 else 0.0))
                self._text(20, 208, f'T   = {r["T_K"]:.2f} K   ({r["T_K"]-273.15:+.2f} °C)', self.fsm, DIM)
                self._text(20, 224, f'p   = {r["p_Pa"]:.2f} Pa', self.fsm, DIM)
                self._text(20, 240, f'rho = {r["rho_kg_m3"]:.5f} kg/m³', self.fsm, DIM)
                self._text(20, 256, f'a   = {r["a_m_s"]:.2f} m/s', self.fsm, DIM)
            except Exception as e:
                self._text(20, 178, f'(błąd geom: {e})', self.fmd, RED)
        else:
            self._text(20, 138, 'Naciśnij A aby obliczyć.', self.fmd, DIM)

        # W3: status inwersji (błąd na czerwono, jak tab_status/plot_status)
        if self.inv_status:
            col = GRN if 'OK' in self.inv_status else (RED if 'BŁĄD' in self.inv_status else YEL)
            self._text(20, H - 106, self.inv_status, self.fsm, col)

        # legenda norm — odseparowana sekcja na dole
        self._hline(H - 92)
        self._text(20, H - 86, 'h_p = wys. ciśnieniowa: h z ISA dla danego p (QNE/QNH/QFE).', self.fsm, DIM)
        self._text(20, H - 72, 'h_ρ = wys. gęstościowa: h z ISA dla danego ρ (uwzgl. ΔT).', self.fsm, DIM)
        self._text(20, H - 58, 'def.: ICAO Doc 7488/3 §4.2; ISO 2533:1975 §3.2/3.3.', self.fsm, DIM)
        self._text(20, H - 44, 'model: p ≤ 1013,25 hPa (h ≥ 0) — wartości p obcinane do p0.', self.fsm, DIM)

        self._footer('↑↓=pole  ←→=±krok  L1R1=×10 L2R2=×100  X=ΔT  A=oblicz  SEL=czyść  B=menu')

    # ── EKRAN: NORMY ───────────────────────────────────────────────────────
    NORMS_LINES = [
        ('NORMY I ŹRÓDŁA', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('• ISO 2533:1975  — Standard Atmosphere.', FG, FONT_SM),
        ('  Międzynarodowa norma definiująca atmosferę wzorcową do 80 km', DIM, FONT_SM),
        ('  geopotencjalnych. Wraz z Add.1:1985 i Add.2:1997.', DIM, FONT_SM),
        ('• ICAO Doc 7488/3 (1993) — Manual of the ICAO Std Atmosphere', FG, FONT_SM),
        ('  rozszerzony do 80 km geopot.; podstawa certyfikacji lotniczej', DIM, FONT_SM),
        ('  (CS-25, FAR Part 25, Annex 8 do Konwencji Chicagowskiej).', DIM, FONT_SM),
        ('• U.S. Standard Atmosphere, 1976 — NOAA/NASA/USAF;', FG, FONT_SM),
        ('  identyczny z ICAO do 32 km, rozszerzony do 86 km h_geom', DIM, FONT_SM),
        ('  (≈ 84 852 m h_geo — koniec naszego modelu).', DIM, FONT_SM),
        ('• PN-83/L-01551 — Atmosfera wzorcowa (norma polska, wycofana', FG, FONT_SM),
        ('  na rzecz ISO; spotykana w literaturze PWN i opracowaniach', DIM, FONT_SM),
        ('  PŁ / PW dla lotnictwa).', DIM, FONT_SM),
        ('• NACA Report 1235 (1955) — historyczne korzenie ICAO ISA.', FG, FONT_SM),
        ('', FG, FONT_SM),
        ('ZMIENNE I JEDNOSTKI (SI; ISO 2533 §2)', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('h_geo  [m]    — wysokość geopotencjalna.', FG, FONT_SM),
        ('               Wysokość mierzona tak jakby g=g0=const wszędzie;', DIM, FONT_SM),
        ('               niezależna od szerokości i wysokości terenu.', DIM, FONT_SM),
        ('               h = (r·Z)/(r+Z), r = 6 356 766 m (ICAO §2.3).', DIM, FONT_SM),
        ('h_geom [m]    — wysokość geometryczna Z (od MSL, ICAO §2.2).', FG, FONT_SM),
        ('               Z = (r·h)/(r-h)  — odwrócenie powyższego.', DIM, FONT_SM),
        ('T      [K]    — temperatura termodynamiczna powietrza suchego.', FG, FONT_SM),
        ('               T(h) = T_b + L_b·(h − h_b) w danej warstwie b.', DIM, FONT_SM),
        ('               (ISO 2533 §3.1; ICAO Doc 7488/3 rozdz. 2.7).', DIM, FONT_SM),
        ('p      [Pa]   — ciśnienie statyczne (ISO 2533 §3.2).', FG, FONT_SM),
        ('               L≠0: p = p_b·(T_b/T)^(g0·M/(R*·L))', DIM, FONT_SM),
        ('               L=0: p = p_b·exp(−g0·M·(h−h_b)/(R*·T_b))', DIM, FONT_SM),
        ('rho    [kg/m³]— gęstość: ρ = p·M/(R*·T)  (równanie stanu).', FG, FONT_SM),
        ('               Równoważnie: ρ = p/(R_air·T), R_air=287,05 J/(kg·K).', DIM, FONT_SM),
        ('a      [m/s]  — prędkość dźwięku: a = √(γ·R*·T/M).', FG, FONT_SM),
        ('               Równoważnie: a = √(γ·R_air·T); zależy tylko od T.', DIM, FONT_SM),
        ('mu     [Pa·s] — lepkość dynamiczna powietrza (Sutherland).', FG, FONT_SM),
        ('               μ = β·T^(3/2)/(T+S), β=1,458e-6, S=110,4 K', DIM, FONT_SM),
        ('               (ISO 2533 §3.7; ważne dla 100–1000 K).', DIM, FONT_SM),
        ('ΔT     [K]    — odchyłka temperatury od ISA na danej h.', FG, FONT_SM),
        ('               T_real(h) = T_ISA(h) + ΔT  (gradient L_b bez', DIM, FONT_SM),
        ('               zmian — atmosfera "non-standard day" ICAO §4.2).', DIM, FONT_SM),
        ('               ISA+15 = "gorący dzień", używane w certyfikacji', DIM, FONT_SM),
        ('               osiągów startowych (CS-25.105, hot-and-high).', DIM, FONT_SM),
        ('σ = ρ/ρ0      — gęstość względna (do EAS, IAS — CAS).', FG, FONT_SM),
        ('δ = p/p0      — ciśnienie względne (do FL, mocy turbiny).', FG, FONT_SM),
        ('θ = T/T0      — temperatura względna; związek: δ = σ·θ.', FG, FONT_SM),
        ('h_p    [m]    — wysokość ciśnieniowa: h taka, że p_ISA(h)=p.', FG, FONT_SM),
        ('               Standardowy QNE/QNH dla altimetrii lotniczej.', DIM, FONT_SM),
        ('h_ρ    [m]    — wysokość gęstościowa (uwzględnia ΔT).', FG, FONT_SM),
        ('               Decyduje o osiągach startowych (ρ → ciąg, siła nośna).', DIM, FONT_SM),
        ('', FG, FONT_SM),
        ('STAŁE (ICAO Doc 7488/3 Tab.A; ISO 2533 Tab.1)', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('g0  = 9,80665 m/s²       — przyspieszenie ziemskie wzorcowe', FG, FONT_SM),
        ('R*  = 8 314,32 J/(kmol·K)— uniwersalna stała gazowa', FG, FONT_SM),
        ('M   = 28,9644 kg/kmol    — masa molowa powietrza suchego', FG, FONT_SM),
        ('R_air=287,05287 J/(kg·K) — indywidualna stała gazowa (=R*/M)', FG, FONT_SM),
        ('γ   = 1,4                — wykł. adiabaty (5/7 powietrza dwuat.)', FG, FONT_SM),
        ('r   = 6 356 766 m        — promień Ziemi wzorcowy (h_geom↔h_geo)', FG, FONT_SM),
        ('β   = 1,458e-6 kg/(m·s·√K)— stała Sutherlanda', FG, FONT_SM),
        ('S   = 110,4 K            — temp. Sutherlanda', FG, FONT_SM),
        ('', FG, FONT_SM),
        ('WARUNKI NA POZIOMIE MORZA (MSL, h=0)', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('T0  = 288,15 K           = 15,00 °C', FG, FONT_SM),
        ('p0  = 101 325 Pa         = 1013,25 hPa = 29,9213 inHg', FG, FONT_SM),
        ('ρ0  = 1,225 kg/m³', FG, FONT_SM),
        ('a0  = 340,294 m/s        ≈ Mach 1 na MSL ISA', FG, FONT_SM),
        ('', FG, FONT_SM),
        ('WARSTWY (h_geo, L = dT/dh; ICAO §2.7 Tab.2)', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('  0–11 000 m   troposfera        L = −6,5 K/km', FG, FONT_SM),
        ('11–20 000 m   tropopauza        L =  0       (T = 216,65 K)', FG, FONT_SM),
        ('20–32 000 m   stratosfera 1     L = +1,0 K/km', FG, FONT_SM),
        ('32–47 000 m   stratosfera 2     L = +2,8 K/km', FG, FONT_SM),
        ('47–51 000 m   stratopauza       L =  0       (T = 270,65 K)', FG, FONT_SM),
        ('51–71 000 m   mezosfera 1       L = −2,8 K/km', FG, FONT_SM),
        ('71–84 852 m   mezosfera 2       L = −2,0 K/km', FG, FONT_SM),
        ('', FG, FONT_SM),
        ('LITERATURA UZUPEŁNIAJĄCA', ACC, FONT_MD),
        ('', FG, FONT_SM),
        ('• Anderson J.D. "Introduction to Flight" (rozdz. 3 — ISA).', FG, FONT_SM),
        ('• Houghton/Carpenter "Aerodynamics for Eng. Students".', FG, FONT_SM),
        ('• Fiszdon W. "Mechanika lotu" (PWN, PL).', FG, FONT_SM),
        ('• Goraj Z. "Dynamika i aerodynamika samolotów" (PWN, PL).', FG, FONT_SM),
    ]

    def render_norms(self):
        self.draw.rectangle([(0, 0), (W, H)], fill=BG)
        self._header('NORMY — opis i odniesienia')

        view_h = H - 60   # od y=32 do y=H-28
        y0 = 34
        line_h = 14
        max_lines = view_h // line_h

        total = len(self.NORMS_LINES)
        self.norms_scroll = max(0, min(max(0, total - max_lines), self.norms_scroll))
        end = min(total, self.norms_scroll + max_lines)

        y = y0
        for txt, col, fsz in self.NORMS_LINES[self.norms_scroll:end]:
            font = self.fmd if fsz == FONT_MD else self.fsm
            self._text(12, y, txt, font, col)
            y += line_h

        if total > max_lines:
            self._text(W - 110, H - 38,
                       f'{self.norms_scroll+1}-{end}/{total}', self.fsm, DIM)

        self._footer('D-pad ↑↓=±1 linia   L-stick=ciągły   L1/R1=±5   L2/R2=±20 (strona)   B=menu')

    # ── render: dispatch ────────────────────────────────────────────────────
    def render(self):
        if self.screen == 'menu':    self.render_menu()
        elif self.screen == 'point': self.render_point()
        elif self.screen == 'table': self.render_table()
        elif self.screen == 'plot':  self.render_plot()
        elif self.screen == 'invert': self.render_invert()
        elif self.screen == 'norms': self.render_norms()
        raw = self.img.tobytes()
        surf = sdl2.SDL_CreateRGBSurfaceWithFormatFrom(
            raw, W, H, 32, W*4, sdl2.SDL_PIXELFORMAT_RGBA32)
        if self._tex:
            sdl2.SDL_DestroyTexture(self._tex)
        self._tex = sdl2.SDL_CreateTextureFromSurface(self.ren, surf)
        sdl2.SDL_FreeSurface(surf)
        sdl2.SDL_RenderClear(self.ren)
        sdl2.SDL_RenderCopy(self.ren, self._tex, None, None)
        sdl2.SDL_RenderPresent(self.ren)

    # ── nawigacja kursora (zmiana aktywnego pola) ───────────────────────────
    def nav(self, dy):
        if self.screen == 'point':    self.point_field = (self.point_field + dy) % 3
        elif self.screen == 'table':  self.tab_field   = (self.tab_field   + dy) % 4
        elif self.screen == 'plot':   self.plot_field  = (self.plot_field  + dy) % 3
        elif self.screen == 'invert': self.inv_field   = (self.inv_field   + dy) % 3

    # ── zmiana wartości aktywnego pola (dy = ±1; mnożnik z Y lub L1/R1/L2/R2) ──
    def adjust(self, dy):
        if self.screen == 'point':
            f = self.point_field
            # mnożnik: D-pad ←→ = STEP_MODES[idx] (cykl Y); L1/R1=×10, L2/R2=×100 (chwilowy override)
            factor = getattr(self, '_step_factor', STEP_MODES[self.step_mode_idx])
            if f == 0:   # Wysokość — point_h ZAWSZE w metrach (W1); krok = 1 jedn. wyświetlania
                unit = UNITS[self.point_unit_idx]
                base_m = 1.0 if unit == 'km' else UNIT_TO_M[unit]  # km: krok 0.001 km = 1 m
                self.point_h = max(0.0, min(C.H_MAX_GEO, self.point_h + dy * base_m * factor))
            elif f == 1:   # V — w wybranej jednostce, przechowywane w m/s
                v_unit = V_UNITS[self.point_V_unit_idx]
                step_ms = 1.0 * V_UNIT_TO_MS[v_unit] * factor   # 1 jedn V × factor
                self.point_V = max(0.0, self.point_V + dy * step_ms)   # N9: V=0 dozwolone
            elif f == 2:   # L [m]
                self.point_L = max(0.001, self.point_L + dy * 0.01 * factor)

        elif self.screen == 'table':
            f = self.tab_field
            factor = getattr(self, '_step_factor', STEP_MODES[self.step_mode_idx])
            if f == 0:
                self.tab_h0 = max(0.0, self.tab_h0 + dy * 100 * factor)
                if self.tab_h1 <= self.tab_h0:
                    self.tab_h1 = self.tab_h0 + self.tab_step
            elif f == 1:
                self.tab_h1 = max(self.tab_h0 + self.tab_step, self.tab_h1 + dy * 100 * factor)
            elif f == 2:
                self.tab_step = max(50.0, self.tab_step + dy * 50 * factor)
            elif f == 3:
                self.tab_dT = max(-50.0, min(50.0, self.tab_dT + dy * 1 * factor))

        elif self.screen == 'plot':
            f = self.plot_field
            factor = getattr(self, '_step_factor', STEP_MODES[self.step_mode_idx])
            if f == 0:    # h0
                self.plot_h0 = max(0.0, self.plot_h0 + dy * 1.0 * factor)
                if self.plot_h1 <= self.plot_h0:
                    self.plot_h1 = self.plot_h0 + 100
            elif f == 1:  # h1
                self.plot_h1 = max(self.plot_h0 + 100, self.plot_h1 + dy * 1.0 * factor)
            elif f == 2:  # n — zawsze int (N2: factor bywa 0.1)
                self.plot_n = max(10, min(500, int(round(self.plot_n + dy * 1 * factor))))

        elif self.screen == 'invert':
            f = self.inv_field
            factor = getattr(self, '_step_factor', STEP_MODES[self.step_mode_idx])
            if f == 0:    # tryb p/rho
                self.inv_mode = (self.inv_mode + dy) % 2
            elif f == 1:
                if self.inv_mode == 0:   # p [Pa]
                    self.inv_p = max(1.0, min(C.p0_SL, self.inv_p + dy * 1.0 * factor))
                else:                    # rho [kg/m³]
                    self.inv_rho = max(1e-5, self.inv_rho + dy * 0.001 * factor)
            elif f == 2:  # ΔT
                self.inv_dT = max(-50.0, min(50.0, self.inv_dT + dy * 1.0 * factor))

    # ── analog: docelowy scroll w zależności od ekranu ──────────────────────
    def _analog_scroll(self, sign):
        """sign=+1 (gałka w dół) przewija w przód; sign=-1 w tył."""
        if self.screen == 'norms':
            view_h = H - 60
            max_lines = view_h // 14
            total = len(self.NORMS_LINES)
            cap = max(0, total - max_lines)
            self.norms_scroll = max(0, min(cap, self.norms_scroll + sign * 3))
        elif self.screen == 'table' and self.tab_rows:
            n = len(self.tab_rows)
            self.tab_scroll = max(0, min(max(0, n - 1), self.tab_scroll + sign * 3))
        elif self.screen == 'point':
            # Output ISA + AnberReMach ma 16 wierszy; analog Y przewija je gdy nie mieszczą się.
            self.point_scroll = max(0, min(20, self.point_scroll + sign * 3))
        elif self.screen == 'menu':
            self.menu_idx = (self.menu_idx + sign) % 5

    # ── obsługa wejść ───────────────────────────────────────────────────────
    def handle(self, etype, code, val):
        # diagnostyka — log każdego naciśniętego klawisza
        if etype == EV_KEY and val == 1:
            self.log.write(f'KEY code={code} screen={self.screen}\n'); self.log.flush()

        # Lewy analog Y — tylko zapamiętujemy wartość; auto-repeat w pętli run().
        if etype == EV_ABS and code == ABS_LY_CODE:
            self.analog_y = val
            return None

        # EXIT
        if etype == EV_KEY and val == 1 and code in EXIT_KEYS:
            return 'quit'
        # B → wstecz / quit z menu
        if etype == EV_KEY and code == BTN_B and val == 1:
            if self.screen == 'menu':
                return 'quit'
            self.screen = 'menu'
            return 'render'

        # MENU
        if self.screen == 'menu':
            if etype == EV_KEY and val == 1:
                if code == BTN_A:
                    self.screen = ('point', 'table', 'plot', 'invert', 'norms')[self.menu_idx]
                    if self.screen == 'norms':
                        self.norms_scroll = 0
                    return 'render'
            if etype == EV_ABS and code == 17 and val != 0:
                self.menu_idx = (self.menu_idx + (1 if val > 0 else -1)) % 5
                return 'render'
            return None

        # EKRANY ROBOCZE
        if etype == EV_KEY and val == 1:
            if code == BTN_A:
                if self.screen == 'table':
                    self._recompute_table()
                    self.tab_scroll = 0
                elif self.screen == 'plot':
                    self.plot_status = 'Generuję...'
                    self.render()   # S4: pokaż status PRZED długim generowaniem (UI nie wygląda na zamrożone)
                    self._generate_plot()
                elif self.screen == 'invert':
                    try:
                        if self.inv_mode == 0:
                            self.inv_result = pressure_altitude(self.inv_p)
                        else:
                            self.inv_result = density_altitude(self.inv_rho, dT=self.inv_dT)
                        self.inv_status = ''
                    except Exception as e:
                        self.inv_result = None
                        self.inv_status = f'BŁĄD: {e}'   # W3: błąd widoczny na ekranie
                        self.log.write(f'invert ERR: {e}\n'); self.log.flush()
                return 'render'

            if code == BTN_X:
                if self.screen == 'point':
                    self.point_dT_idx = (self.point_dT_idx + 1) % len(DT_CYCLE)
                elif self.screen == 'table':
                    self.tab_dT = ((self.tab_dT + 5 + 50) % 100) - 50
                elif self.screen == 'invert':
                    self.inv_dT = ((self.inv_dT + 5 + 50) % 100) - 50
                return 'render'

            if code == BTN_Y:
                # Y: cykl kroku (×1 → ×10 → ×100) w PUNKT; zapis CSV w TABELI
                if self.screen == 'point':
                    self.step_mode_idx = (self.step_mode_idx + 1) % len(STEP_MODES)
                elif self.screen == 'table':
                    self._save_table_csv()
                return 'render'

            if code == BTN_START:
                # cykl jednostki prędkości (m/s → km/h → kt → mph → ft/s)
                if self.screen == 'point':
                    self.point_V_unit_idx = (self.point_V_unit_idx + 1) % len(V_UNITS)
                return 'render'

            if code == BTN_SELECT:
                # cykl jednostki wysokości (m → ft → km)
                if self.screen == 'point':
                    self.point_unit_idx = (self.point_unit_idx + 1) % len(UNITS)
                else:
                    self._clear_data()
                return 'render'

            # L1/R1 = krok ×10, L2/R2 = krok ×100 (D-pad ←→ = krok ×1)
            if code in SHOULDER_KEYS:
                dy     = 1 if code in (BTN_R1, BTN_R2) else -1
                factor = 100 if code in (BTN_L2, BTN_R2) else 10
                if self.screen == 'norms':
                    page = 20 if factor == 100 else 5
                    self.norms_scroll = max(0, self.norms_scroll + dy * page)
                    return 'render'
                try:
                    self._step_factor = factor
                    self.adjust(dy)
                finally:
                    del self._step_factor   # W2: mnożnik wraca do trybu z Y, nie „lepki" ×1
                return 'render'

        # D-pad (EV_ABS)
        if etype == EV_ABS and val != 0 and code in (16, 17):
            sign = 1 if val > 0 else -1
            if code == 17:
                # góra / dół — nawigacja po polach (lub scroll w NORMACH)
                if self.screen == 'norms':
                    self.norms_scroll = max(0, self.norms_scroll + sign)
                    return 'render'
                self.nav(sign)
                return 'render'
            else:
                # ←/→ — w TABELI (gdy są wyniki) scroll, inaczej adjust(krok mały)
                if self.screen == 'table' and self.tab_rows:
                    self.tab_scroll = max(0, min(max(0, len(self.tab_rows)-1), self.tab_scroll + sign))
                    return 'render'
                if self.screen in ('point', 'plot', 'invert', 'table'):
                    try:
                        self.adjust(sign)
                    except Exception as e:
                        self.log.write(f'adjust(D-pad) ERR: {e}\n'); self.log.flush()
                    return 'render'
                return None

        return None

    # ── pętla główna ────────────────────────────────────────────────────────
    def run(self):
        self._pwrscr = ScreenPowerToggle()
        ev = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(ev)):
            pass
        start_ms = sdl2.SDL_GetTicks()
        GUARD_MS = 1500
        self.render()
        last_periodic = 0

        while True:
            self._pwrscr.poll()
            self._pwrscr.tick(0)
            if self._pwrscr.is_off:
                sdl2.SDL_Delay(120)
                continue
            now = sdl2.SDL_GetTicks()
            guard = (now - start_ms) < GUARD_MS

            evs = self._poll_gamepad()
            for etype, code, val in evs:
                if guard:
                    continue
                r = self.handle(etype, code, val)
                if r == 'quit':
                    self.quit(); return
                if r == 'render':
                    self.render()

            while sdl2.SDL_PollEvent(ctypes.byref(ev)):
                if guard:
                    continue
                if ev.type == sdl2.SDL_KEYDOWN:
                    if ev.key.keysym.sym == sdl2.SDLK_ESCAPE:
                        self.quit(); return

            # Lewy analog — auto-repeat scroll z proporcjonalną prędkością.
            # Skill rg40xx-buttons: zakres ±4096, deadzone 400. Wzór anbercc/filepicker:
            # lekkie wychylenie → 200ms/krok (precyzja), max → 20ms/krok (szybkie scroll).
            ay = abs(self.analog_y)
            if not guard and ay > ANALOG_DEADZONE:
                tnow = time.monotonic()
                if tnow >= self.analog_next:
                    sign = 1 if self.analog_y > 0 else -1
                    self._analog_scroll(sign)
                    self.render()
                    deflect = min(1.0, (ay - ANALOG_DEADZONE) / max(1, ANALOG_MAX - ANALOG_DEADZONE))
                    repeat_ms = ANALOG_SLOW_MS - deflect * (ANALOG_SLOW_MS - ANALOG_FAST_MS)
                    self.analog_next = tnow + repeat_ms / 1000.0

            if now - last_periodic >= 10000:
                self.render()
                last_periodic = now

            sdl2.SDL_Delay(40)

    def quit(self):
        try:
            self._pwrscr.restore()
        except Exception:
            pass
        self.log.write(f'[{time.strftime("%H:%M:%S")}] exit\n'); self.log.flush()
        if self.gp_fd is not None:
            try: os.close(self.gp_fd)
            except Exception: pass
        if self._tex:
            sdl2.SDL_DestroyTexture(self._tex)
        sdl2.SDL_DestroyRenderer(self.ren)
        sdl2.SDL_DestroyWindow(self.win)
        sdl2.SDL_Quit()
        try:
            self.log.close()   # N6: log zamykany przy wyjściu
        except Exception:
            pass


if __name__ == '__main__':
    AnberReMach().run()
