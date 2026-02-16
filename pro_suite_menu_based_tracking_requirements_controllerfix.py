# PRO SUITE — menu-based build (matches your preferred UI)
# -------------------------------------------------------------------
# Single-file script. No external .ahk/.ini files created.
# Settings + anonymous stats stored in Windows Registry.
#
# Controller support:
#   - Xbox: XInput (built-in, no extra libs required)  ✅ reliable
#   - PS / generic: pygame (optional)                  ✅ best coverage
#
# Usage tracking (Discord webhook) — OPT-IN in Settings:
#   - "install" sent once per PC (won't count relaunches as new users)
#   - "daily" sent once per day per PC (daily active)
#
# Requirements tab:
#   - Button to install/repair Python libs via pip (logs inside the app; no ugly CMD window).
#   - Disabled automatically when running as a bundled EXE.
#
# Build EXE (no console window):
#   pyinstaller --onefile --windowed --noconsole --icon pro_suite_iconik.ico pro_suite_menu_based_tracking_requirements_controllerfix.py

from __future__ import annotations

import json
import os
import sys
import time
import uuid
import queue
import threading
import traceback
import subprocess
import urllib.request
import importlib.util
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, FrozenSet, Callable

sys.dont_write_bytecode = True
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# Optional: ttkbootstrap (keeps the exact same menu style you showed)
try:
    import ttkbootstrap as tb
    USE_BOOTSTRAP = True
except Exception:
    tb = None
    USE_BOOTSTRAP = False

import tkinter as tk
from tkinter import ttk, messagebox

# Optional: pynput for global keyboard hotkeys
try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

# Optional: pygame for PS/DInput controllers
try:
    import pygame
except Exception:
    pygame = None

# Windows-only
if sys.platform.startswith("win"):
    import ctypes
    import ctypes.wintypes as wt
    import winsound
    import winreg

    if not hasattr(wt, "ULONG_PTR"):
        wt.ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
else:
    ctypes = None
    winsound = None
    winreg = None

APP_TITLE = "PRO SUITE"
SUBTITLE = "Hotkeys, Toggles & Timing"
APP_VERSION = "1.0.0"

# --- Your webhook goes here (do not paste publicly; it can be extracted from an EXE) ---
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1472216530258493511/1ZAh6M3_R5IaZKBI0wglUwmHLIhc3UgQ0RNPaksTNOEhicC4YlPdNE7trAiyjEMxh4N4"

REG_PATH = r"Software\ProSuite"
REG_VALUE = "config_v3"

DEFAULTS = {
    "global_enabled": True,
    "features": {
        "stall": True,
        "speedflip": True,
        "straightdash": True,
        "turningdash": True,
    },
    # Hotkeys can be keyboard ("Ctrl+H") OR controller ("GP:A", "GP:DPAD_DOWN")
    "hotkeys": {
        "toggle_all": "F6",
        "stall": "F",
        "speedflip": "H",
        "straightdash": "V",
        "turningdash": "N",
        "emergency": "L",
        "exit": "F10",
    },
    "timings": {
        "stall_click_ms": 3,
        "sf_a_hold_ms": 80,
        "sf_wait_before_jump_ms": 20,
        "sf_s_hold_ms": 690,
        "sf_d_hold_ms": 720,
        "sf_airroll_time_ms": 799,
        "sf_shift_start_ms": 840,
        "sf_shift_duration_ms": 130,
        "sf_prejump_ms": 20,
        "sf_click_down_ms": 20,
        "sf_between_jumps_ms": 40,
        "sf_post_shift_ms": 50,
        "sd_click_down_ms": 15,
        "sd_between_clicks_ms": 25,
        "td_pre_ms": 10,
        "td_click_down_ms": 15,
        "td_between_clicks_ms": 25,
    },
    "telemetry": {
        "enabled": False,
        "install_id": "",
        "installed_sent": False,
        "last_daily_ymd": "",
    },
}

# ---------------------- helpers ----------------------
def win_msgbox(title: str, text: str):
    if sys.platform.startswith("win") and ctypes is not None:
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
            return
        except Exception:
            pass
    try:
        messagebox.showerror(title, text)
    except Exception:
        print(title)
        print(text)

def _deep_merge(a: dict, b: dict) -> dict:
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            a[k] = _deep_merge(a[k], v)
        else:
            a[k] = v
    return a

def _reg_load() -> dict:
    if not sys.platform.startswith("win") or winreg is None:
        return json.loads(json.dumps(DEFAULTS))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as k:
            raw, _ = winreg.QueryValueEx(k, REG_VALUE)
            data = json.loads(raw)
            merged = _deep_merge(json.loads(json.dumps(DEFAULTS)), data)
            return merged
    except Exception:
        return json.loads(json.dumps(DEFAULTS))

def _reg_save(data: dict) -> None:
    if not sys.platform.startswith("win") or winreg is None:
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            winreg.SetValueEx(k, REG_VALUE, 0, winreg.REG_SZ, json.dumps(data))
    except Exception:
        pass

def _font(size: int, *, bold: bool = False):
    family = "Segoe UI" if sys.platform.startswith("win") else "TkDefaultFont"
    weight = "bold" if bold else "normal"
    return (family, size, weight)

def _muted():
    return "#6c757d" if USE_BOOTSTRAP else "#666666"

def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False

# ---------------------- SendInput ----------------------
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

SPECIAL_VK = {
    "LSHIFT": 0xA0,
    "SHIFT": 0x10,
    "LCTRL": 0xA2,
    "CTRL": 0x11,
    "LALT": 0xA4,
    "ALT": 0x12,
    "WIN": 0x5B,
    "ENTER": 0x0D,
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESC": 0x1B,
    "BACKSPACE": 0x08,
}

def _vk_for_key(name: str) -> int:
    name = name.strip().upper()
    if len(name) == 1 and name.isalnum():
        return ord(name)
    if name.startswith("F") and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    if name in SPECIAL_VK:
        return SPECIAL_VK[name]
    return 0

if sys.platform.startswith("win"):

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wt.WORD),
            ("wScan", wt.WORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", wt.ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG),
            ("dy", wt.LONG),
            ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", wt.ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]

    SendInput = ctypes.windll.user32.SendInput

    def _send_key(vk: int, down: bool):
        if vk == 0:
            return
        flags = 0 if down else KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.union.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def key_down(name: str):
        _send_key(_vk_for_key(name), True)

    def key_up(name: str):
        _send_key(_vk_for_key(name), False)

    def mouse_right(down: bool):
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=0,
            dwFlags=(MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP),
            time=0,
            dwExtraInfo=0,
        )
        SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

else:
    def key_down(name: str): ...
    def key_up(name: str): ...
    def mouse_right(down: bool): ...

# ---------------------- Hotkey parsing ----------------------
MOD_NAMES = {"CTRL", "ALT", "SHIFT", "WIN"}

def is_gamepad_hotkey(s: str) -> bool:
    s = (s or "").strip().upper()
    return s.startswith("GP:")

def parse_hotkey(s: str) -> Tuple[str, FrozenSet[str], str]:
    s = (s or "").strip()
    if not s:
        return "kb", frozenset(), ""
    if is_gamepad_hotkey(s):
        btn = s.split(":", 1)[1].strip().upper()
        return "gp", frozenset(), btn

    parts = [p.strip().upper() for p in s.replace("-", "+").split("+") if p.strip()]
    mods = set()
    key = ""
    for p in parts:
        if p in ("CONTROL", "CTRL"):
            mods.add("CTRL")
        elif p == "ALT":
            mods.add("ALT")
        elif p == "SHIFT":
            mods.add("SHIFT")
        elif p in ("WIN", "WINDOWS", "SUPER", "META"):
            mods.add("WIN")
        else:
            key = p
    if not key and parts:
        key = parts[-1]
    return "kb", frozenset(mods), key

def format_hotkey(mods: FrozenSet[str], key: str) -> str:
    nice = []
    if "CTRL" in mods: nice.append("Ctrl")
    if "ALT" in mods: nice.append("Alt")
    if "SHIFT" in mods: nice.append("Shift")
    if "WIN" in mods: nice.append("Win")
    k = (key or "").upper()
    if k:
        nice.append(k)
    return "+".join(nice) if nice else ""

def display_hotkey(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.upper().startswith("GP:"):
        return raw.split(":", 1)[1].strip().upper()
    return raw

# ---------------------- Telemetry (opt-in) ----------------------
class Telemetry:
    def __init__(self, get_state: Callable[[], dict], set_state: Callable[[dict], None]):
        self.get_state = get_state
        self.set_state = set_state

    def ensure_install_id(self) -> str:
        st = self.get_state()
        tel = st.get("telemetry", {})
        iid = str(tel.get("install_id", "")).strip()
        if iid:
            return iid
        iid = uuid.uuid4().hex
        tel["install_id"] = iid
        tel["installed_sent"] = False
        tel.setdefault("last_daily_ymd", "")
        st["telemetry"] = tel
        self.set_state(st)
        _reg_save(st)
        return iid

    def _post(self, content: str):
        if not DISCORD_WEBHOOK_URL:
            return
        payload = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(DISCORD_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=2.0) as r:
                r.read()
        except Exception:
            pass  # stay silent

    def startup_tick(self):
        st = self.get_state()
        tel = st.get("telemetry", {})
        if not bool(tel.get("enabled", False)):
            return

        iid = self.ensure_install_id()
        today = time.strftime("%Y-%m-%d")

        # send INSTALL once ever (this is your "unique user")
        if not bool(tel.get("installed_sent", False)):
            threading.Thread(target=self._post, args=(f"install iid={iid} v={APP_VERSION}",), daemon=True).start()
            tel["installed_sent"] = True
            st["telemetry"] = tel
            self.set_state(st)
            _reg_save(st)

        # daily active
        if str(tel.get("last_daily_ymd", "")) != today:
            threading.Thread(target=self._post, args=(f"daily iid={iid} v={APP_VERSION} day={today}",), daemon=True).start()
            tel["last_daily_ymd"] = today
            st["telemetry"] = tel
            self.set_state(st)
            _reg_save(st)

# ---------------------- Controller input (pygame) ----------------------
class PygameControllerManager:
    def __init__(self, on_press, on_release, on_status=None):
        self.on_press = on_press
        self.on_release = on_release
        self.on_status = on_status

        self._stop = threading.Event()
        self._t = None

        self._joys = {}
        self._joy_names = {}
        self._hat_state = {}
        self._pressed = set()

    def start(self):
        if pygame is None:
            if self.on_status:
                self.on_status("Controller: pygame not installed (PS/generic controllers unavailable).")
            return
        if self._t and self._t.is_alive():
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        for tok in list(self._pressed):
            try: self.on_release(tok)
            except Exception: pass
        self._pressed.clear()

    def _status(self, msg: str):
        if self.on_status:
            try: self.on_status(msg)
            except Exception: pass

    def _ensure_joysticks(self):
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
        except Exception:
            pass
        count = pygame.joystick.get_count()
        for i in range(count):
            if i not in self._joys:
                j = pygame.joystick.Joystick(i)
                j.init()
                self._joys[i] = j
                self._joy_names[i] = j.get_name()
                self._hat_state[i] = (0, 0)
        if count == 0:
            self._status("Controller: none connected (pygame).")
        elif count == 1:
            self._status(f"Controller: 1 connected (pygame) — {list(self._joy_names.values())[0]}.")
        else:
            self._status(f"Controller: {count} connected (pygame).")

    def _map_button(self, joy_id: int, button_index: int) -> str:
        name = (self._joy_names.get(joy_id, "") or "").lower()
        xbox = {0:"A",1:"B",2:"X",3:"Y",4:"LB",5:"RB",6:"BACK",7:"START",8:"LS",9:"RS"}
        ps   = {0:"SQUARE",1:"CROSS",2:"CIRCLE",3:"TRIANGLE",4:"L1",5:"R1",8:"SHARE",9:"OPTIONS",10:"L3",11:"R3",12:"PS",13:"TOUCHPAD"}
        if any(x in name for x in ("xbox", "xinput")):
            return xbox.get(button_index, f"BTN{button_index}")
        if any(x in name for x in ("playstation", "wireless controller", "dualshock", "dualsense", "ps4", "ps5")):
            return ps.get(button_index, f"BTN{button_index}")
        return xbox.get(button_index, f"BTN{button_index}")

    def _emit_press(self, btn: str):
        tok = f"GP:{btn}".upper()
        if tok in self._pressed: return
        self._pressed.add(tok)
        self.on_press(tok)

    def _emit_release(self, btn: str):
        tok = f"GP:{btn}".upper()
        if tok not in self._pressed: return
        self._pressed.discard(tok)
        self.on_release(tok)

    def _handle_hat(self, joy_id: int, value: Tuple[int,int]):
        prev = self._hat_state.get(joy_id,(0,0))
        self._hat_state[joy_id] = value
        def dirs(v):
            x,y=v; s=set()
            if y==1: s.add("DPAD_UP")
            if y==-1: s.add("DPAD_DOWN")
            if x==1: s.add("DPAD_RIGHT")
            if x==-1: s.add("DPAD_LEFT")
            return s
        for d in dirs(prev)-dirs(value): self._emit_release(d)
        for d in dirs(value)-dirs(prev): self._emit_press(d)

    def _run(self):
        try:
            pygame.display.init()
            try:
                hidden_flag = getattr(pygame, "HIDDEN", 0)
                pygame.display.set_mode((1,1), hidden_flag)
            except Exception:
                pygame.display.set_mode((1,1))
            pygame.joystick.init()
            pygame.event.set_allowed([pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP, pygame.JOYHATMOTION, pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED])
        except Exception as e:
            self._status(f"Controller init failed (pygame): {e}")
            return

        last_scan=0.0
        while not self._stop.is_set():
            if time.time()-last_scan>1.0:
                last_scan=time.time()
                try: self._ensure_joysticks()
                except Exception: pass
            try:
                for ev in pygame.event.get():
                    if ev.type == pygame.JOYBUTTONDOWN:
                        self._emit_press(self._map_button(ev.joy, ev.button))
                    elif ev.type == pygame.JOYBUTTONUP:
                        self._emit_release(self._map_button(ev.joy, ev.button))
                    elif ev.type == pygame.JOYHATMOTION:
                        self._handle_hat(ev.joy, ev.value)
            except Exception:
                pass
            time.sleep(0.01)

# ---------------------- Controller input (XInput fallback: Xbox) ----------------------
if sys.platform.startswith("win") and ctypes is not None:

    class XINPUT_GAMEPAD(ctypes.Structure):
        _fields_ = [
            ("wButtons", wt.WORD),
            ("bLeftTrigger", wt.BYTE),
            ("bRightTrigger", wt.BYTE),
            ("sThumbLX", wt.SHORT),
            ("sThumbLY", wt.SHORT),
            ("sThumbRX", wt.SHORT),
            ("sThumbRY", wt.SHORT),
        ]

    class XINPUT_STATE(ctypes.Structure):
        _fields_ = [("dwPacketNumber", wt.DWORD), ("Gamepad", XINPUT_GAMEPAD)]

    XINPUT_DPAD_UP = 0x0001
    XINPUT_DPAD_DOWN = 0x0002
    XINPUT_DPAD_LEFT = 0x0004
    XINPUT_DPAD_RIGHT = 0x0008
    XINPUT_START = 0x0010
    XINPUT_BACK = 0x0020
    XINPUT_LS = 0x0040
    XINPUT_RS = 0x0080
    XINPUT_LB = 0x0100
    XINPUT_RB = 0x0200
    XINPUT_A = 0x1000
    XINPUT_B = 0x2000
    XINPUT_X = 0x4000
    XINPUT_Y = 0x8000

    _xinput = None
    for dll in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll", "xinput1_2.dll", "xinput1_1.dll"):
        try:
            _xinput = ctypes.windll.LoadLibrary(dll)
            break
        except Exception:
            _xinput = None

    if _xinput is not None:
        XInputGetState = _xinput.XInputGetState
        XInputGetState.argtypes = [wt.DWORD, ctypes.POINTER(XINPUT_STATE)]
        XInputGetState.restype = wt.DWORD
    else:
        XInputGetState = None
else:
    XInputGetState = None

class XInputControllerManager:
    def __init__(self, on_press, on_release, on_status=None):
        self.on_press = on_press
        self.on_release = on_release
        self.on_status = on_status
        self._stop = threading.Event()
        self._t = None
        self._prev_buttons = [0,0,0,0]
        self._pressed=set()

    def start(self):
        if not sys.platform.startswith("win") or XInputGetState is None:
            if self.on_status:
                self.on_status("Controller: XInput not available.")
            return
        if self._t and self._t.is_alive(): return
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        for tok in list(self._pressed):
            try: self.on_release(tok)
            except Exception: pass
        self._pressed.clear()

    def _status(self, msg: str):
        if self.on_status:
            try: self.on_status(msg)
            except Exception: pass

    def _emit_press(self, btn: str):
        tok=f"GP:{btn}".upper()
        if tok in self._pressed: return
        self._pressed.add(tok)
        self.on_press(tok)

    def _emit_release(self, btn: str):
        tok=f"GP:{btn}".upper()
        if tok not in self._pressed: return
        self._pressed.discard(tok)
        self.on_release(tok)

    def _run(self):
        btn_map = {
            XINPUT_A:"A", XINPUT_B:"B", XINPUT_X:"X", XINPUT_Y:"Y",
            XINPUT_LB:"LB", XINPUT_RB:"RB",
            XINPUT_BACK:"BACK", XINPUT_START:"START",
            XINPUT_LS:"LS", XINPUT_RS:"RS",
            XINPUT_DPAD_UP:"DPAD_UP", XINPUT_DPAD_DOWN:"DPAD_DOWN",
            XINPUT_DPAD_LEFT:"DPAD_LEFT", XINPUT_DPAD_RIGHT:"DPAD_RIGHT",
        }
        last_status=0.0
        while not self._stop.is_set():
            connected=0
            for i in range(4):
                st = XINPUT_STATE()
                res = XInputGetState(i, ctypes.byref(st))
                if res == 0:
                    connected += 1
                    buttons = int(st.Gamepad.wButtons)
                    prev = self._prev_buttons[i]
                    changed = buttons ^ prev
                    if changed:
                        for mask, name in btn_map.items():
                            if changed & mask:
                                if buttons & mask:
                                    self._emit_press(name)
                                else:
                                    self._emit_release(name)
                    self._prev_buttons[i] = buttons
                else:
                    self._prev_buttons[i] = 0

            if time.time()-last_status>1.0:
                last_status=time.time()
                if connected==0:
                    self._status("Controller: none connected (XInput).")
                elif connected==1:
                    self._status("Controller: 1 connected (XInput).")
                else:
                    self._status(f"Controller: {connected} connected (XInput).")

            time.sleep(0.01)

# ---------------------- Macro Engine ----------------------
class MacroEngine:
    def __init__(self, state: dict):
        self.state = state
        self._lock = threading.RLock()

        self._kb_pressed:set[str]=set()
        self._kb_mods:set[str]=set()
        self._gp_pressed:set[str]=set()

        self._kill = threading.Event()
        self._listener: Optional["pynput_keyboard.Listener"] = None

        self._running_hold: Dict[str,bool]={"straightdash":False,"turningdash":False}
        self._capture_mode = threading.Event()

        self.on_toggle_all: Optional[Callable[[bool],None]] = None

    def set_capture_mode(self, on: bool):
        if on: self._capture_mode.set()
        else: self._capture_mode.clear()

    def start_keyboard_listener(self):
        if not sys.platform.startswith("win"): return
        if pynput_keyboard is None: return
        if self._listener: return
        self._kill.clear()
        self._listener = pynput_keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon=True
        self._listener.start()

    def stop(self):
        self._kill.set()
        self._release_all_outputs()
        if self._listener:
            try: self._listener.stop()
            except Exception: pass
            self._listener=None

    def apply_state(self, st: dict):
        with self._lock:
            self.state = st

    def _key_name(self, key) -> str:
        try:
            if pynput_keyboard and isinstance(key, pynput_keyboard.KeyCode) and key.char:
                return key.char.upper()
        except Exception:
            pass
        try:
            k=str(key).replace("Key.","").upper()
            if k in ("ESC","TAB","SPACE","ENTER"): return k
            if k.startswith("F") and k[1:].isdigit(): return k
            return k
        except Exception:
            return ""

    def _update_kb_mods(self, name:str, down:bool):
        n=name.upper()
        is_ctrl = "CTRL" in n or "CONTROL" in n
        is_alt = "ALT" in n
        is_shift = "SHIFT" in n
        is_win = any(x in n for x in ("WIN","SUPER","META","CMD"))
        if down:
            if is_ctrl: self._kb_mods.add("CTRL")
            if is_alt: self._kb_mods.add("ALT")
            if is_shift: self._kb_mods.add("SHIFT")
            if is_win: self._kb_mods.add("WIN")
        else:
            if is_ctrl: self._kb_mods.discard("CTRL")
            if is_alt: self._kb_mods.discard("ALT")
            if is_shift: self._kb_mods.discard("SHIFT")
            if is_win: self._kb_mods.discard("WIN")

    def _on_press(self, key):
        if self._kill.is_set(): return False
        name=self._key_name(key)
        if not name: return
        self._update_kb_mods(name, True)
        if name not in MOD_NAMES: self._kb_pressed.add(name)
        self._maybe_trigger_kb(name)

    def _on_release(self, key):
        name=self._key_name(key)
        if not name: return
        self._update_kb_mods(name, False)
        self._kb_pressed.discard(name)

    def on_gamepad_press(self, tok:str):
        if self._kill.is_set(): return
        tok=tok.upper()
        self._gp_pressed.add(tok)
        self._maybe_trigger_gp(tok)

    def on_gamepad_release(self, tok:str):
        self._gp_pressed.discard(tok.upper())

    def _beep(self, hi:bool):
        if winsound:
            try: winsound.Beep(750 if hi else 500, 140)
            except Exception: pass

    def _maybe_trigger_kb(self, name:str):
        if self._capture_mode.is_set(): return
        with self._lock:
            cfg=self.state
            global_enabled=bool(cfg.get("global_enabled", True))
            hk=cfg.get("hotkeys", {})
            actions={
                "toggle_all": self._act_toggle_all,
                "stall": self._act_stall,
                "speedflip": self._act_speedflip,
                "straightdash": self._act_straightdash_hold,
                "turningdash": self._act_turningdash_hold,
                "emergency": self._act_emergency,
                "exit": self._act_exit,
            }
            for action, fn in actions.items():
                kind, mods_req, key_req = parse_hotkey(hk.get(action, ""))
                if kind!="kb" or not key_req: continue
                if name.upper()!=key_req.upper(): continue
                if not set(mods_req).issubset(self._kb_mods): continue
                if action not in ("toggle_all","emergency","exit") and not global_enabled:
                    return
                if action in ("straightdash","turningdash") and self._running_hold.get(action, False):
                    return
                threading.Thread(target=fn, daemon=True).start()
                return

    def _maybe_trigger_gp(self, tok:str):
        if self._capture_mode.is_set(): return
        with self._lock:
            cfg=self.state
            global_enabled=bool(cfg.get("global_enabled", True))
            hk=cfg.get("hotkeys", {})
            actions={
                "toggle_all": self._act_toggle_all,
                "stall": self._act_stall,
                "speedflip": self._act_speedflip,
                "straightdash": self._act_straightdash_hold,
                "turningdash": self._act_turningdash_hold,
                "emergency": self._act_emergency,
                "exit": self._act_exit,
            }
            for action, fn in actions.items():
                kind, _, btn = parse_hotkey(hk.get(action, ""))
                if kind!="gp" or not btn: continue
                if tok != f"GP:{btn}".upper(): continue
                if action not in ("toggle_all","emergency","exit") and not global_enabled:
                    return
                if action in ("straightdash","turningdash") and self._running_hold.get(action, False):
                    return
                threading.Thread(target=fn, daemon=True).start()
                return

    def _act_toggle_all(self):
        with self._lock:
            self.state["global_enabled"]=not self.state.get("global_enabled", True)
            on=self.state["global_enabled"]
            _reg_save(self.state)
        self._beep(on)
        if self.on_toggle_all:
            try: self.on_toggle_all(on)
            except Exception: pass

    def _act_exit(self):
        self._kill.set()
        self._release_all_outputs()
        os._exit(0)

    def _act_emergency(self):
        self._kill.set()
        self._release_all_outputs()
        self._beep(False)
        time.sleep(0.15)
        self._kill.clear()

    def _act_stall(self):
        with self._lock:
            if not self.state.get("global_enabled", True): return
            if not self.state.get("features", {}).get("stall", True): return
            t=int(self.state["timings"]["stall_click_ms"])
        key_down("Q"); key_down("D")
        mouse_right(True); time.sleep(max(0,t)/1000.0); mouse_right(False)
        key_up("D"); key_up("Q")

    def _act_speedflip(self):
        with self._lock:
            if not self.state.get("global_enabled", True): return
            if not self.state.get("features", {}).get("speedflip", True): return
            tm=dict(self.state.get("timings", {}))

        a_hold=tm["sf_a_hold_ms"]
        wait_before=tm["sf_wait_before_jump_ms"]
        s_dur=tm["sf_s_hold_ms"]
        d_dur=tm["sf_d_hold_ms"]
        airroll=tm["sf_airroll_time_ms"]
        shift_start=tm["sf_shift_start_ms"]
        shift_dur=tm["sf_shift_duration_ms"]
        prejump=tm["sf_prejump_ms"]
        click_down=tm["sf_click_down_ms"]
        between_jumps=tm["sf_between_jumps_ms"]
        post_shift=tm["sf_post_shift_ms"]

        key_down("I"); key_down("A")
        time.sleep(a_hold/1000.0)
        key_up("A")
        time.sleep(wait_before/1000.0)

        for k in ("W","S","E","Q"): key_up(k)
        key_down("E"); key_down("W")
        time.sleep(prejump/1000.0)

        mouse_right(True); time.sleep(click_down/1000.0); mouse_right(False)
        time.sleep(between_jumps/1000.0)
        mouse_right(True); time.sleep(click_down/1000.0); mouse_right(False)

        key_up("W"); key_down("S"); key_down("D")

        if s_dur < d_dur:
            time.sleep(s_dur/1000.0)
            key_up("S"); key_down("W")
            time.sleep((d_dur-s_dur)/1000.0)
            key_up("D")
        else:
            time.sleep(d_dur/1000.0)
            key_up("D")
            time.sleep((s_dur-d_dur)/1000.0)
            key_up("S"); key_down("W")

        max_hold=max(s_dur,d_dur)
        time_until_e=airroll-max_hold
        if time_until_e>0: time.sleep(time_until_e/1000.0)
        key_up("E")

        time_until_shift=shift_start-max(airroll,max_hold)
        if time_until_shift>0: time.sleep(time_until_shift/1000.0)

        key_down("LShift"); time.sleep(shift_dur/1000.0); key_up("LShift")
        time.sleep(post_shift/1000.0)
        key_up("I")

    def _hold_active(self, kind:str, key_or_btn:str)->bool:
        if kind=="kb":
            return key_or_btn.upper() in self._kb_pressed
        return f"GP:{key_or_btn}".upper() in self._gp_pressed

    def _act_straightdash_hold(self):
        self._running_hold["straightdash"]=True
        try:
            with self._lock:
                hk_raw=self.state.get("hotkeys",{}).get("straightdash","V")
                kind,_,key_req=parse_hotkey(hk_raw)
                down_ms=int(self.state["timings"]["sd_click_down_ms"])
                gap_ms=int(self.state["timings"]["sd_between_clicks_ms"])
            while (not self._kill.is_set()) and self._hold_active(kind,key_req):
                with self._lock:
                    if not self.state.get("global_enabled", True) or not self.state.get("features", {}).get("straightdash", True):
                        break
                key_down("W"); key_down("Q")
                mouse_right(True); time.sleep(down_ms/1000.0); mouse_right(False)
                time.sleep(gap_ms/1000.0)
                mouse_right(True); time.sleep(down_ms/1000.0); mouse_right(False)
                time.sleep(0.001)
        finally:
            key_up("Q"); key_up("W")
            self._running_hold["straightdash"]=False

    def _act_turningdash_hold(self):
        self._running_hold["turningdash"]=True
        try:
            with self._lock:
                hk_raw=self.state.get("hotkeys",{}).get("turningdash","N")
                kind,_,key_req=parse_hotkey(hk_raw)
                pre_ms=int(self.state["timings"]["td_pre_ms"])
                down_ms=int(self.state["timings"]["td_click_down_ms"])
                gap_ms=int(self.state["timings"]["td_between_clicks_ms"])
            while (not self._kill.is_set()) and self._hold_active(kind,key_req):
                with self._lock:
                    if not self.state.get("global_enabled", True) or not self.state.get("features", {}).get("turningdash", True):
                        break
                key_down("W"); key_down("Q"); key_down("D")
                time.sleep(pre_ms/1000.0)
                key_up("D"); key_down("A")
                mouse_right(True); time.sleep(down_ms/1000.0); mouse_right(False)
                time.sleep(gap_ms/1000.0)
                mouse_right(True); time.sleep(down_ms/1000.0); mouse_right(False)
                key_up("A")
                time.sleep(0.001)
        finally:
            for k in ("A","D","Q","W"): key_up(k)
            self._running_hold["turningdash"]=False

    def _release_all_outputs(self):
        for k in ("W","A","S","D","Q","E","I","LShift"):
            try: key_up(k)
            except Exception: pass
        try: mouse_right(False)
        except Exception: pass

# ---------------------- GUI ----------------------
@dataclass
class RowDef:
    label: str
    key: str
    feature_key: Optional[str]

class ProSuiteGUI:
    def __init__(self, root: tk.Tk):
        self.root=root
        self.state=_reg_load()

        # enforce stable install_id (1 PC = 1 user)
        tel = self.state.get("telemetry", {})
        if not str(tel.get("install_id","")).strip():
            tel["install_id"] = uuid.uuid4().hex
            tel["installed_sent"] = False
            tel.setdefault("last_daily_ymd","")
            self.state["telemetry"] = tel
            _reg_save(self.state)

        self.telemetry = Telemetry(lambda: self.state, self._set_state)

        self.engine=MacroEngine(self.state)
        self.engine.on_toggle_all=self._engine_toggle_all_callback
        # start keyboard listener only if pynput exists
        self.engine.start_keyboard_listener()

        self.status=tk.StringVar(value="Ready. Hotkeys won’t block your input.")
        self.controller_status=tk.StringVar(value="Controller: starting…")

        # controller managers
        self.pygame_ctrl = PygameControllerManager(self._on_gp_press, self._on_gp_release,
                                                   on_status=lambda m: self.root.after(0, lambda: self.controller_status.set(m)))
        self.xinput_ctrl = XInputControllerManager(self._on_gp_press, self._on_gp_release,
                                                   on_status=lambda m: self.root.after(0, lambda: self.controller_status.set(m)))

        # start both (xinput is the reliable xbox path)
        self.xinput_ctrl.start()
        self.pygame_ctrl.start()

        # vars
        self.var_global=tk.BooleanVar(value=self.state.get("global_enabled", True))
        self.var_features={k: tk.BooleanVar(value=bool(self.state["features"].get(k, True))) for k in DEFAULTS["features"]}
        self.var_hotkeys_raw={k: tk.StringVar(value=str(self.state["hotkeys"].get(k, DEFAULTS["hotkeys"][k]))) for k in DEFAULTS["hotkeys"]}
        self.var_hotkeys_disp={k: tk.StringVar(value=display_hotkey(self.var_hotkeys_raw[k].get())) for k in DEFAULTS["hotkeys"]}
        self.var_timings={k: tk.IntVar(value=int(self.state["timings"].get(k, DEFAULTS["timings"][k]))) for k in DEFAULTS["timings"]}
        self.var_telemetry=tk.BooleanVar(value=bool(self.state.get("telemetry",{}).get("enabled", False)))

        self._building=False
        self._capture_target: Optional[str]=None
        self._capture_window: Optional[tk.Toplevel]=None

        self._build_ui()
        self._sync_engine()

        # If deps missing, show a helpful status (but DO NOT block launch)
        if pynput_keyboard is None:
            self.status.set("Missing pynput — keyboard hotkeys disabled. Use Requirements tab to install.")
        elif not sys.platform.startswith("win"):
            self.status.set("Windows only. This app uses Windows SendInput.")
        else:
            self.status.set("Ready.")

        # telemetry tick on start (opt-in)
        self.telemetry.startup_tick()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_state(self, st: dict):
        self.state = st

    # controller events
    def _on_gp_press(self, tok: str):
        # capture mode: accept controller button immediately
        if self._capture_target and self._capture_window and self._capture_window.winfo_exists():
            btn = tok.split(":",1)[1].strip().upper()
            self.root.after(0, lambda: self._commit_hotkey_capture(f"GP:{btn}"))
            return
        self.engine.on_gamepad_press(tok)

    def _on_gp_release(self, tok: str):
        self.engine.on_gamepad_release(tok)

    # UI
    def _build_ui(self):
        pad=16
        container=ttk.Frame(self.root, padding=pad)
        container.pack(fill="both", expand=True)

        header=ttk.Frame(container)
        header.pack(fill="x", pady=(0,10))
        ttk.Label(header, text=APP_TITLE, font=_font(18, bold=True)).pack(side="left")
        ttk.Label(header, text=SUBTITLE, font=_font(11), foreground=_muted()).pack(side="left", padx=(10,0), pady=(6,0))

        nb=ttk.Notebook(container)
        nb.pack(fill="both", expand=True)

        tab_controls=ttk.Frame(nb, padding=14)
        tab_settings=ttk.Frame(nb, padding=14)
        tab_req=ttk.Frame(nb, padding=14)
        nb.add(tab_controls, text="Controls")
        nb.add(tab_settings, text="Settings")
        nb.add(tab_req, text="Requirements")

        self._build_controls(tab_controls)
        self._build_settings(tab_settings)
        self._build_requirements(tab_req)

        statusbar=ttk.Frame(container)
        statusbar.pack(fill="x", pady=(10,0))
        ttk.Label(statusbar, textvariable=self.status, foreground=_muted(), font=_font(10)).pack(side="left")
        ttk.Label(statusbar, textvariable=self.controller_status, foreground=_muted(), font=_font(10)).pack(side="right")

    def _hotkey_button(self, parent, hk_key: str):
        return ttk.Button(parent, textvariable=self.var_hotkeys_disp[hk_key], command=lambda k=hk_key: self._capture_hotkey(k))

    def _build_controls(self, parent: ttk.Frame):
        card=ttk.LabelFrame(parent, text="Hotkeys & Toggles", padding=14)
        card.pack(fill="both", expand=True)

        top=ttk.Frame(card)
        top.grid(row=0, column=0, sticky="ew", pady=(0,10))
        top.grid_columnconfigure(0, weight=1)
        ttk.Label(top, text="Master Enable", font=_font(10, bold=True)).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(top, variable=self.var_global, command=self._on_change).grid(row=0, column=1, sticky="e")

        table=ttk.Frame(card)
        table.grid(row=1, column=0, sticky="nsew")
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        table.grid_columnconfigure(0, weight=1)
        table.grid_columnconfigure(1, weight=0, minsize=180)
        table.grid_columnconfigure(2, weight=0, minsize=90)

        ttk.Label(table, text="Feature", font=_font(10, bold=True)).grid(row=0, column=0, sticky="w", pady=(0,8))
        ttk.Label(table, text="Hotkey", font=_font(10, bold=True)).grid(row=0, column=1, sticky="w", pady=(0,8), padx=(10,0))
        ttk.Label(table, text="Enabled", font=_font(10, bold=True)).grid(row=0, column=2, sticky="w", pady=(0,8), padx=(10,0))

        rows=[
            RowDef("Toggle All (global enable/disable)", "toggle_all", None),
            RowDef("Stall", "stall", "stall"),
            RowDef("Speed Flip", "speedflip", "speedflip"),
            RowDef("Straight Dash (hold)", "straightdash", "straightdash"),
            RowDef("Turning Dash (hold)", "turningdash", "turningdash"),
            RowDef("Emergency Stop (releases keys)", "emergency", None),
            RowDef("Exit Script", "exit", None),
        ]
        for i, r in enumerate(rows, start=1):
            ttk.Label(table, text=r.label).grid(row=i, column=0, sticky="w", pady=6)
            self._hotkey_button(table, r.key).grid(row=i, column=1, sticky="w", padx=(10,0), pady=6)
            if r.feature_key is None:
                ttk.Label(table, text="—", foreground=_muted()).grid(row=i, column=2, sticky="w", padx=(10,0), pady=6)
            else:
                ttk.Checkbutton(table, variable=self.var_features[r.feature_key], command=self._on_change).grid(row=i, column=2, sticky="w", padx=(10,0), pady=6)

        actions=ttk.Frame(card)
        actions.grid(row=2, column=0, sticky="ew", pady=(14,0))
        actions.grid_columnconfigure(3, weight=1)
        ttk.Button(actions, text="Save", command=self._save).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Reset", command=self._reset).grid(row=0, column=1, sticky="w", padx=(10,0))
        ttk.Button(actions, text="Emergency Stop", command=self._emergency).grid(row=0, column=2, sticky="w", padx=(10,0))
        ttk.Label(actions, text="Rebind: press keyboard key OR controller button (incl. D-pad).", foreground=_muted()).grid(row=0, column=3, sticky="e")

    def _build_settings(self, parent: ttk.Frame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        stall=ttk.LabelFrame(parent, text="Stall", padding=14)
        stall.grid(row=0, column=0, sticky="nsew", padx=(0,10), pady=(0,10))
        speed=ttk.LabelFrame(parent, text="Speed Flip", padding=14)
        speed.grid(row=0, column=1, sticky="nsew", pady=(0,10))
        dash=ttk.LabelFrame(parent, text="Dash (hold)", padding=14)
        dash.grid(row=1, column=0, sticky="nsew", padx=(0,10))
        turn=ttk.LabelFrame(parent, text="Turning Dash (hold)", padding=14)
        turn.grid(row=1, column=1, sticky="nsew")

        self._slider(stall, "Right-click press (ms)", "stall_click_ms", 0, 50)

        self._slider(speed, "A hold (ms)", "sf_a_hold_ms", 0, 300)
        self._slider(speed, "Wait before jump (ms)", "sf_wait_before_jump_ms", 0, 200)
        self._slider(speed, "S hold (ms)", "sf_s_hold_ms", 0, 1500)
        self._slider(speed, "D hold (ms)", "sf_d_hold_ms", 0, 1500)
        self._slider(speed, "Air-roll time (ms)", "sf_airroll_time_ms", 0, 1500)
        self._slider(speed, "Shift start (ms)", "sf_shift_start_ms", 0, 2000)
        self._slider(speed, "Shift duration (ms)", "sf_shift_duration_ms", 0, 500)

        ttk.Separator(speed).pack(fill="x", pady=10)
        ttk.Label(speed, text="Fine tuning", foreground=_muted()).pack(anchor="w", pady=(0,6))
        self._slider(speed, "Pre-jump settle (ms)", "sf_prejump_ms", 0, 150)
        self._slider(speed, "Click hold (ms)", "sf_click_down_ms", 0, 80)
        self._slider(speed, "Between jumps (ms)", "sf_between_jumps_ms", 0, 120)
        self._slider(speed, "After shift (ms)", "sf_post_shift_ms", 0, 150)

        self._slider(dash, "Click hold (ms)", "sd_click_down_ms", 0, 60)
        self._slider(dash, "Between clicks (ms)", "sd_between_clicks_ms", 0, 120)

        self._slider(turn, "Pre-step (ms)", "td_pre_ms", 0, 60)
        self._slider(turn, "Click hold (ms)", "td_click_down_ms", 0, 60)
        self._slider(turn, "Between clicks (ms)", "td_between_clicks_ms", 0, 120)

        tel = ttk.LabelFrame(parent, text="Usage Stats (optional)", padding=14)
        tel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        tel.grid_columnconfigure(1, weight=1)

        ttk.Checkbutton(tel, variable=self.var_telemetry, command=self._on_change).grid(row=0, column=0, sticky="w")
        ttk.Label(tel, text="Anonymous usage stats (counts installs + daily active).", foreground=_muted()).grid(row=0, column=1, sticky="w", padx=(8,0))

        iid = str(self.state.get("telemetry", {}).get("install_id",""))
        self._iid_label = ttk.Label(tel, text=f"Install ID: {iid}", foreground=_muted())
        self._iid_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6,0))

        bottom=ttk.Frame(parent)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14,0))
        bottom.grid_columnconfigure(4, weight=1)
        ttk.Button(bottom, text="Save", command=self._save).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Reset", command=self._reset).grid(row=0, column=1, sticky="w", padx=(10,0))
        ttk.Button(bottom, text="Reset Timings", command=self._reset_timings).grid(row=0, column=2, sticky="w", padx=(10,0))
        ttk.Button(bottom, text="Emergency Stop", command=self._emergency).grid(row=0, column=3, sticky="w", padx=(10,0))
        ttk.Label(bottom, text="Changes apply instantly.", foreground=_muted()).grid(row=0, column=4, sticky="e")

    def _build_requirements(self, parent: ttk.Frame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        ttk.Label(parent, text="Requirements Installer", font=_font(12, bold=True)).grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="Only for running the .py file. (Bundled EXE already includes libraries.)", foreground=_muted()).grid(row=1, column=0, sticky="w", pady=(4,10))

        box=ttk.LabelFrame(parent, text="Status", padding=12)
        box.grid(row=2, column=0, sticky="ew")
        self.req_status=tk.StringVar(value=self._deps_status_text())
        ttk.Label(box, textvariable=self.req_status, foreground=_muted(), justify="left").pack(anchor="w")

        actions=ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="ew", pady=(10,10))
        actions.grid_columnconfigure(2, weight=1)

        self.req_btn=ttk.Button(actions, text="Install / Repair Libraries", command=self._install_requirements)
        self.req_btn.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Refresh Status", command=lambda: self.req_status.set(self._deps_status_text())).grid(row=0, column=1, sticky="w", padx=(10,0))

        logs=ttk.LabelFrame(parent, text="Log", padding=8)
        logs.grid(row=4, column=0, sticky="nsew")
        parent.grid_rowconfigure(4, weight=1)

        self.req_log=tk.Text(logs, height=14, wrap="word")
        self.req_log.pack(fill="both", expand=True)
        self._req_queue: "queue.Queue[str]" = queue.Queue()
        self._req_running=False
        self._req_log("Click 'Install / Repair Libraries' to install: pynput, pygame/pygame-ce, ttkbootstrap.\n\n")

        self.root.after(100, self._pump_req_log)

        if getattr(sys, "frozen", False):
            self.req_btn.configure(state="disabled")
            self._req_log("Bundled EXE detected: pip install disabled here.\n")

    def _deps_status_text(self)->str:
        lines=[]
        lines.append(f"Python: {sys.version.split()[0]}")
        lines.append(f"pynput: {'OK' if _has('pynput') else 'MISSING'}")
        lines.append(f"pygame: {'OK' if _has('pygame') else 'MISSING'} (PS/generic controllers need pygame; Xbox works via XInput)")
        lines.append(f"ttkbootstrap: {'OK' if _has('ttkbootstrap') else 'optional'}")
        return "\n".join(lines)

    def _req_log(self, s:str):
        self.req_log.configure(state="normal")
        self.req_log.insert("end", s)
        self.req_log.see("end")
        self.req_log.configure(state="disabled")

    def _pump_req_log(self):
        try:
            while True:
                line=self._req_queue.get_nowait()
                self._req_log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_req_log)

    def _run_cmd(self, args:list[str])->int:
        self._req_queue.put("\n$ " + " ".join(args) + "\n")
        try:
            creation = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            p=subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=creation)
            assert p.stdout is not None
            for line in p.stdout:
                self._req_queue.put(line)
            return p.wait()
        except Exception as e:
            self._req_queue.put(f"[ERROR] {e}\n")
            return 1

    def _install_requirements(self):
        if self._req_running or getattr(sys, "frozen", False):
            return
        self._req_running=True
        self.req_btn.configure(state="disabled")
        self.req_status.set("Installing… please wait.")

        def worker():
            py=sys.executable
            # Ensure pip exists
            rc=self._run_cmd([py, "-m", "pip", "--version"])
            if rc != 0:
                self._req_queue.put("\n[INFO] pip missing — running ensurepip…\n")
                self._run_cmd([py, "-m", "ensurepip", "--upgrade"])

            self._req_queue.put("\n[INFO] Upgrading pip/setuptools/wheel…\n")
            self._run_cmd([py, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

            self._req_queue.put("\n[INFO] Installing/Upgrading pynput…\n")
            self._run_cmd([py, "-m", "pip", "install", "--upgrade", "pynput"])

            self._req_queue.put("\n[INFO] Installing/Upgrading pygame…\n")
            rc=self._run_cmd([py, "-m", "pip", "install", "--upgrade", "--prefer-binary", "pygame"])
            if rc != 0:
                self._req_queue.put("\n[WARN] pygame failed — trying pygame-ce…\n")
                self._run_cmd([py, "-m", "pip", "install", "--upgrade", "--prefer-binary", "pygame-ce"])
                self._req_queue.put("\n[NOTE] If you still can’t use PS controller: use Steam Input or DS4Windows to emulate Xbox (XInput).\n")

            self._req_queue.put("\n[INFO] Installing/Upgrading ttkbootstrap (optional)…\n")
            self._run_cmd([py, "-m", "pip", "install", "--upgrade", "ttkbootstrap"])

            self._req_queue.put("\n[DONE] Restart the app.\n")

            def finish():
                self._req_running=False
                self.req_btn.configure(state="normal")
                self.req_status.set(self._deps_status_text())
                # start keyboard listener if it was missing and now installed
                if pynput_keyboard is None and _has("pynput"):
                    self.status.set("pynput installed — restart required to enable keyboard hotkeys.")
                else:
                    self.status.set("Requirements install complete (restart recommended).")
            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # sliders
    def _slider(self, parent, label:str, key:str, vmin:int, vmax:int):
        row=ttk.Frame(parent); row.pack(fill="x", pady=6)
        left=ttk.Frame(row); left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=label).pack(anchor="w")
        val_lbl=ttk.Label(left, text=str(self.var_timings[key].get()), foreground=_muted()); val_lbl.pack(anchor="w")

        def on_change(_=None):
            if self._building: return
            val=int(float(scale.get()))
            self.var_timings[key].set(val)
            val_lbl.config(text=str(val))
            self._on_change()

        scale=ttk.Scale(row, from_=vmin, to=vmax, orient="horizontal", command=on_change)
        scale.set(self.var_timings[key].get())
        scale.pack(side="right", fill="x", expand=True, padx=(10,0))

    # capture hotkey
    def _capture_hotkey(self, hk_key:str):
        self.engine.set_capture_mode(True)
        self._capture_target = hk_key

        win=tk.Toplevel(self.root)
        self._capture_window = win
        win.title("Select a key")
        win.resizable(False, False)
        win.geometry("460x210")
        win.transient(self.root)

        wrap=ttk.Frame(win, padding=16); wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="Select a key or controller button", font=_font(12, bold=True)).pack(pady=(0,6))
        ttk.Label(wrap, text="Esc = cancel\nWaiting…", foreground=_muted(), justify="center").pack(pady=(0,10))

        preview=tk.StringVar(value="Waiting…")
        ttk.Label(wrap, textvariable=preview, font=_font(12)).pack(pady=(6,0))

        # if pynput missing, controller capture still works (via _on_gp_press)
        if pynput_keyboard is None:
            ttk.Label(wrap, text="Keyboard capture needs pynput (install from Requirements).", foreground=_muted()).pack(pady=(10,0))
            def on_close():
                self._close_capture(cancel=True)
            win.protocol("WM_DELETE_WINDOW", on_close)
            win.grab_set(); win.focus_force()
            return

        mods:set[str]=set()
        stop_event=threading.Event()

        def is_mod(k)->Optional[str]:
            if k in (pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.ctrl_r): return "CTRL"
            if k in (pynput_keyboard.Key.alt, pynput_keyboard.Key.alt_l, pynput_keyboard.Key.alt_r, pynput_keyboard.Key.alt_gr): return "ALT"
            if k in (pynput_keyboard.Key.shift, pynput_keyboard.Key.shift_l, pynput_keyboard.Key.shift_r): return "SHIFT"
            if k in (pynput_keyboard.Key.cmd, pynput_keyboard.Key.cmd_l, pynput_keyboard.Key.cmd_r): return "WIN"
            return None

        def on_press(k):
            if stop_event.is_set(): return False
            if k == pynput_keyboard.Key.esc:
                stop_event.set()
                self.root.after(0, lambda: self._close_capture(cancel=True))
                return False

            m=is_mod(k)
            if m:
                mods.add(m)
                self.root.after(0, lambda: preview.set(format_hotkey(frozenset(mods), "")))
                return

            key_name=None
            try:
                if isinstance(k, pynput_keyboard.KeyCode) and k.char:
                    key_name = k.char.upper()
            except Exception:
                key_name=None

            if key_name is None:
                try:
                    if k == pynput_keyboard.Key.space: key_name="SPACE"
                    elif k == pynput_keyboard.Key.tab: key_name="TAB"
                    elif k == pynput_keyboard.Key.enter: key_name="ENTER"
                    elif hasattr(k, "name") and k.name and k.name.lower().startswith("f") and k.name[1:].isdigit():
                        key_name = k.name.upper()
                except Exception:
                    key_name=None

            if not key_name: return
            hotkey = format_hotkey(frozenset(mods), key_name)
            self.root.after(0, lambda: preview.set(hotkey))
            stop_event.set()
            self.root.after(90, lambda: self._commit_hotkey_capture(hotkey))
            return False

        def on_release(k):
            m=is_mod(k)
            if m: mods.discard(m)

        listener=pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon=True
        listener.start()

        def on_close():
            stop_event.set()
            try: listener.stop()
            except Exception: pass
            self._close_capture(cancel=True)

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.grab_set(); win.focus_force()

    def _commit_hotkey_capture(self, raw:str):
        if not self._capture_target: return
        hk_key=self._capture_target
        self.var_hotkeys_raw[hk_key].set(raw)
        self.var_hotkeys_disp[hk_key].set(display_hotkey(raw))
        self._on_change()
        self._close_capture(cancel=False)

    def _close_capture(self, cancel: bool):
        if self._capture_window and self._capture_window.winfo_exists():
            try: self._capture_window.destroy()
            except Exception: pass
        self._capture_window=None
        self._capture_target=None
        self.engine.set_capture_mode(False)
        self.status.set("Hotkey capture canceled." if cancel else "Hotkey set (not saved).")

    # state sync/save
    def _read_ui_state(self)->dict:
        st=json.loads(json.dumps(DEFAULTS))
        st["global_enabled"]=bool(self.var_global.get())
        st["features"]={k: bool(v.get()) for k,v in self.var_features.items()}
        st["hotkeys"]={k: str(v.get()).strip() for k,v in self.var_hotkeys_raw.items()}
        st["timings"]={k: int(v.get()) for k,v in self.var_timings.items()}

        # keep the same install_id forever
        st["telemetry"]["enabled"]=bool(self.var_telemetry.get())
        st["telemetry"]["install_id"]=self.state.get("telemetry", {}).get("install_id","")
        st["telemetry"]["installed_sent"]=bool(self.state.get("telemetry", {}).get("installed_sent", False))
        st["telemetry"]["last_daily_ymd"]=str(self.state.get("telemetry", {}).get("last_daily_ymd",""))
        return st

    def _sync_engine(self):
        self.state=self._read_ui_state()
        self.engine.apply_state(self.state)
        # update install id label (in case of first launch)
        try:
            iid=str(self.state.get("telemetry", {}).get("install_id",""))
            self._iid_label.config(text=f"Install ID: {iid}")
        except Exception:
            pass

    def _on_change(self):
        if self._building: return
        self._sync_engine()
        self.status.set("Applied (not saved).")

    def _save(self):
        self._sync_engine()
        _reg_save(self.state)
        self.status.set("Saved to Windows registry.")
        # if opted in, send install/daily now
        self.telemetry.startup_tick()

    def _reset(self):
        self._building=True
        try:
            # preserve install id so it never changes for this PC
            iid=self.state.get("telemetry", {}).get("install_id","") or uuid.uuid4().hex
            installed_sent=self.state.get("telemetry", {}).get("installed_sent", False)
            last_daily=self.state.get("telemetry", {}).get("last_daily_ymd","")
            enabled=self.state.get("telemetry", {}).get("enabled", False)

            self.state=json.loads(json.dumps(DEFAULTS))
            self.state["telemetry"]["install_id"]=iid
            self.state["telemetry"]["installed_sent"]=installed_sent
            self.state["telemetry"]["last_daily_ymd"]=last_daily
            self.state["telemetry"]["enabled"]=enabled

            self._apply_state_to_ui(self.state)
        finally:
            self._building=False
        self._sync_engine()
        _reg_save(self.state)
        self.status.set("Reset to defaults.")

    def _reset_timings(self):
        self._building=True
        try:
            for k,v in DEFAULTS["timings"].items():
                self.var_timings[k].set(int(v))
        finally:
            self._building=False
        self._on_change()
        self.status.set("Timings reset (not saved).")

    def _emergency(self):
        self.engine._act_emergency()
        self.status.set("Emergency stop executed.")

    def _apply_state_to_ui(self, st: dict):
        self.var_global.set(bool(st.get("global_enabled", True)))
        for k in self.var_features:
            self.var_features[k].set(bool(st["features"].get(k, True)))
        for k in self.var_hotkeys_raw:
            raw=str(st["hotkeys"].get(k, DEFAULTS["hotkeys"][k]))
            self.var_hotkeys_raw[k].set(raw)
            self.var_hotkeys_disp[k].set(display_hotkey(raw))
        for k in self.var_timings:
            self.var_timings[k].set(int(st["timings"].get(k, DEFAULTS["timings"][k])))
        self.var_telemetry.set(bool(st.get("telemetry", {}).get("enabled", False)))

    def _engine_toggle_all_callback(self, on: bool):
        def _do():
            self.var_global.set(bool(on))
            self._on_change()
            self.status.set("Master Enable toggled by hotkey.")
        self.root.after(0, _do)

    def _on_close(self):
        try: self.engine.stop()
        except Exception: pass
        try: self.pygame_ctrl.stop()
        except Exception: pass
        try: self.xinput_ctrl.stop()
        except Exception: pass
        self.root.destroy()

def main():
    try:
        if not sys.platform.startswith("win"):
            win_msgbox("Windows only", "This app uses Windows SendInput. Run on Windows.")
            return

        # create window using your preferred menu style
        if USE_BOOTSTRAP and tb is not None:
            root = tb.Window(themename="flatly", title=f"{APP_TITLE} — {SUBTITLE}")
        else:
            root = tk.Tk()
            root.title(f"{APP_TITLE} — {SUBTITLE}")
            style = ttk.Style()
            if "clam" in style.theme_names():
                style.theme_use("clam")

        try:
            root.call("tk", "scaling", 1.25)
        except Exception:
            pass

        root.geometry("920x640")
        root.minsize(880, 560)

        ProSuiteGUI(root)
        root.mainloop()

    except Exception:
        win_msgbox("Pro Suite crashed on launch", traceback.format_exc())

if __name__ == "__main__":
    main()
