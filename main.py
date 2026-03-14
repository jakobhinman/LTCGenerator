import tkinter as tk
import threading
import queue
import sys
import os
import ctypes

# Import our new modules
from LTCModules.helpers import timecode_from_string, SMPTETimecode
import LTCModules.audio_utils
from LTCModules.audio_utils import get_output_devices, warm_up_audio_system
from LTCModules.engine import ltc_generator_task
from LTCModules.gui import GeneratorGUI

# --- Configuration ---
SAMPLE_RATE = 48000
BUFFER_SIZE = 10
LIBLTC_DLL = "libltc.dll"

# --- Framerate Mapping ---
LTC_USE_DF = 1
FRAMERATE_MAP = {
    "23.98": (24.0 * 1000 / 1001, 0),
    "24": (24.0, 0),
    "25": (25.0, 0),
    "29.97": (30.0 * 1000 / 1001, 0),
    "29.97 DF": (30.0 * 1000 / 1001, LTC_USE_DF),
    "30": (30.0, 0),
}

class LTCApp:
    def __init__(self):
        self.root = tk.Tk()
        
        # Initialize Queues and Events
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.audio_queue = queue.Queue(maxsize=BUFFER_SIZE)
        self.gui_queue = queue.Queue()
        self.command_queue = queue.Queue()
        
        self.generator_thread = None
        self.jam_map = {}
        self.gui_loaded = False
        
        # 1. Load libltc
        self.lib = self.load_libltc()
        
        # 2. Setup Audio
        warm_up_audio_system()
        device_names, self.device_map = get_output_devices()
        
        # 3. Build GUI
        callbacks = {
            'start': self.on_start,
            'stop': self.on_stop,
            'pause': self.on_pause,
            'set': self.on_set,
            'load_jam': self.on_load_jam_list
        }
        
        self.gui = GeneratorGUI(self.root, device_names, list(FRAMERATE_MAP.keys()), callbacks)
        
        # Final setup
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_gui_loop()
        self.gui_loaded = True

    def load_libltc(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, LIBLTC_DLL)
        
        try:
            lib = ctypes.CDLL(dll_path)
            # Define prototypes
            lib.ltc_encoder_create.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_int]
            lib.ltc_encoder_create.restype = ctypes.c_void_p
            lib.ltc_encoder_set_timecode.argtypes = [ctypes.c_void_p, ctypes.POINTER(SMPTETimecode)]
            lib.ltc_encoder_get_timecode.argtypes = [ctypes.c_void_p, ctypes.POINTER(SMPTETimecode)]
            lib.ltc_encoder_encode_frame.argtypes = [ctypes.c_void_p]
            lib.ltc_encoder_get_bufferptr.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), ctypes.c_int]
            lib.ltc_encoder_get_bufferptr.restype = ctypes.c_int
            lib.ltc_encoder_inc_timecode.argtypes = [ctypes.c_void_p]
            lib.ltc_encoder_free.argtypes = [ctypes.c_void_p]
            return lib
        except Exception as e:
            print(f"Failed to load {LIBLTC_DLL}: {e}")
            sys.exit(1)

    def on_start(self):
        if self.generator_thread and self.generator_thread.is_alive():
            self.pause_event.clear()
        else:
            self.stop_event.clear()
            self.pause_event.clear()
            
            # Unpack GUI settings
            start_tc = self.gui.tc_entry.get()
            fr_name = self.gui.selected_framerate.get()
            fr_info = FRAMERATE_MAP[fr_name]
            dev_name = self.gui.selected_device.get()
            dev_index = self.device_map[dev_name]
            
            self.gui.lock_ui(True)
            self.generator_thread = threading.Thread(
                target=ltc_generator_task,
                args=(self.lib, start_tc, fr_info, dev_index, self.jam_map.copy(),
                      self.stop_event, self.pause_event, self.audio_queue, 
                      self.gui_queue, self.command_queue, SAMPLE_RATE),
                daemon=True
            )
            self.generator_thread.start()

    def on_pause(self):
        self.pause_event.set()
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except queue.Empty: break

    def on_stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        if self.generator_thread:
            self.generator_thread.join()
        self.gui.lock_ui(False)

    def on_set(self):
        new_tc = self.gui.tc_entry.get()
        try:
            timecode_from_string(new_tc)
            if self.generator_thread and self.generator_thread.is_alive():
                self.command_queue.put(new_tc)
            else:
                self.gui.current_tc_str.set(new_tc)
        except ValueError as e:
            self.gui.current_tc_str.set("ERR: Format")

    def on_load_jam_list(self):
        # Implementation of parsing logic (trigger > target)
        text = self.gui.jammer_text.get("1.0", tk.END).splitlines()
        new_map = {}
        for line in text:
            if ">" in line:
                parts = line.split(">")
                new_map[parts[0].strip()] = parts[1].strip()
        self.jam_map = new_map
        print(f"Loaded {len(self.jam_map)} jams.")

    def update_gui_loop(self):
        # Update Timecode
        while not self.gui_queue.empty():
            self.gui.current_tc_str.set(self.gui_queue.get_nowait())
        
        # Update Buffer Health
        # BUFFER_SIZE is the maxsize of self.audio_queue
        q_size = self.audio_queue.qsize()
        percent = q_size / BUFFER_SIZE
        self.gui.update_health(percent)
        
        self.root.after(50, self.update_gui_loop)

    def on_closing(self):
        self.on_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = LTCApp()
    app.run()