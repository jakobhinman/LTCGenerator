import tkinter as tk
from tkinter import messagebox
import threading
import queue
import sys
import os
import ctypes

# Import our new modules
from LTCModules.helpers import timecode_from_string, SMPTETimecode, FRAMERATE_MAP, normalize_timecode, is_timecode_valid
import LTCModules.audio_utils
from LTCModules.audio_utils import get_output_devices, warm_up_audio_system
from LTCModules.engine import ltc_generator_task
from LTCModules.gui import GeneratorGUI

# --- Configuration ---
SAMPLE_RATE = 48000
BUFFER_SIZE = 10
LIBLTC_DLL = "libltc.dll"

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
            'load_jam': self.on_load_jam_list,
            'tc_focus_out': self.on_tc_focus_out
        }
        
        self.gui = GeneratorGUI(self.root, device_names, list(FRAMERATE_MAP.keys()), callbacks)
        
        # Final setup
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_gui_loop()
        self.gui_loaded = True

    def on_tc_focus_out(self, event=None):
        raw_tc = self.gui.tc_entry.get()
        fr_name = self.gui.selected_framerate.get()
        
        # Normalize the entry field instantly
        normalized = normalize_timecode(raw_tc, fr_name)
        self.gui.tc_entry.delete(0, tk.END)
        self.gui.tc_entry.insert(0, normalized)

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
        # Trigger the same normalization as focus out
        self.on_tc_focus_out()
        new_tc = self.gui.tc_entry.get()
        fr_name = self.gui.selected_framerate.get()
        
        valid, err = is_timecode_valid(new_tc, fr_name)
        if valid:
            if self.generator_thread and self.generator_thread.is_alive():
                self.command_queue.put(new_tc)
            else:
                self.gui.current_tc_str.set(new_tc)
        else:
            messagebox.showwarning("Invalid TC", err)

    def on_load_jam_list(self):
        """Validates the jam list for the separator and valid timecodes."""
        text = self.gui.jammer_text.get("1.0", tk.END).splitlines()
        fr_name = self.gui.selected_framerate.get()
        new_map = {}
        processed_lines = []
        errors = []

        for i, line in enumerate(text):
            line = line.strip()
            # Skip empty lines or comments
            if not line or line.startswith(("#", "//")):
                processed_lines.append(line)
                continue
                
            # ENFORCE SEPARATOR
            if ">" not in line:
                errors.append(f"Line {i+1}: Missing '>' separator")
                processed_lines.append(line)
                continue

            parts = line.split(">")
            trigger_raw = parts[0].strip()
            target_raw = parts[1].strip()
            
            # This turns "59.29" into "00:00:59:29" so validation can read it
            n_trig = normalize_timecode(trigger_raw, fr_name)
            n_targ = normalize_timecode(target_raw, fr_name)
            
            # Now validate the standardized strings
            v_trig, e_trig = is_timecode_valid(n_trig, fr_name)
            v_targ, e_targ = is_timecode_valid(n_targ, fr_name)
            
            if v_trig and v_targ:
                new_map[n_trig] = n_targ
                processed_lines.append(f"{n_trig} > {n_targ}")
            else:
                err_msg = e_trig if not v_trig else e_targ
                errors.append(f"Line {i+1}: {err_msg}")
                processed_lines.append(line)

        if errors:
            # Show all errors at once in a messagebox
            messagebox.showerror("Jam List Errors", "\n".join(errors))
        else:
            # Update the text box with beautiful, normalized timecodes
            self.gui.jammer_text.delete("1.0", tk.END)
            self.gui.jammer_text.insert("1.0", "\n".join(processed_lines))
            self.jam_map = new_map
            messagebox.showinfo("Success", f"Loaded {len(self.jam_map)} valid jams.")

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