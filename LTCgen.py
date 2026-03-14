import tkinter as tk
from tkinter import font
from tkinter import messagebox
import ctypes
import ctypes.util
import numpy as np
import sounddevice as sd
import threading
import queue
import time
import sys
import re
import os

# --- New Mappings (Add this section) ---

# Define our SMPTE framerates
# We map the display name to a tuple: (fps_double, libltc_flags)
# LTC_USE_DF = 1 (Tells libltc to use drop-frame for 29.97)
LTC_USE_DF = 1
FRAMERATE_MAP = {
    "23.98": (24.0 * 1000 / 1001, 0),
    "24": (24.0, 0),
    "25": (25.0, 0),
    "29.97": (30.0 * 1000 / 1001, 0),
    "29.97 DF": (30.0 * 1000 / 1001, LTC_USE_DF),
    "30": (30.0, 0),
}

# --- Configuration ---
SAMPLE_RATE = 48000  # Audio sample rate in Hz
FRAMERATE = "30"       # LTC framerate (e.g., 24, 25, 29.97, 30)
BUFFER_SIZE = 10     # Number of audio frames to buffer ahead of time


# --- Global State ---
generator_thread = None     # Holds the generator thread object
stop_event = threading.Event()
pause_event = threading.Event()
audio_queue = queue.Queue(maxsize=BUFFER_SIZE)
gui_queue = queue.Queue()
command_queue = queue.Queue()
DEVICE_MAP = {}
jam_map = {}

def get_output_devices():
    """
    Builds a list of unique device names, prioritizing the most stable drivers (MME).
    Populates the global DEVICE_MAP with {name: index}.
    """
    global DEVICE_MAP
    DEVICE_MAP.clear()
    
    try:
        print("Querying all devices and host APIs...")
        all_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        
        api_map = {i: api['name'] for i, api in enumerate(host_apis)}
        print(f"Found APIs: {api_map}")

        # --- REVERSED Driver Priority ---
        # We now know MME is the most stable for your device.
        # We will prioritize it by giving it the lowest number.
        DRIVER_PRIORITY = {
            "MME": 1,
            "Windows DirectSound": 2,
            "Windows WASAPI": 3,
            # All other drivers get a low priority
        }
        
        # This will hold our best choice for each device
        # {device_name: (priority, index)}
        best_choices = {}

        for idx, device in enumerate(all_devices):
            if device['max_output_channels'] > 0:
                api_name = api_map.get(device['hostapi'], "Unknown API")
                
                # Skip the known-bad driver
                if api_name == 'Windows WDM-KS':
                    continue
                
                device_name = device['name']
                
                # Get this driver's priority, default to 99
                priority = DRIVER_PRIORITY.get(api_name, 99)
                
                # Check if this device is new OR if this driver is better
                if device_name not in best_choices or priority < best_choices[device_name][0]:
                    # This is a better choice! Update our map.
                    best_choices[device_name] = (priority, idx)

        # Now, build the final DEVICE_MAP and name list from our best choices
        output_device_names = []
        for device_name, (priority, idx) in best_choices.items():
            api_name = api_map.get(all_devices[idx]['hostapi'])
            print(f"  -> Selecting '{device_name}' [Using {api_name}] (Index: {idx})")
            
            DEVICE_MAP[device_name] = idx
            output_device_names.append(device_name)
        
        if not output_device_names:
            print("No output devices found.")
            return ["Default"]
        
        return sorted(output_device_names)
        
    except Exception as e:
        print(f"CRITICAL: Could not query audio devices: {e}")
        return ["Default"]
   
OUTPUT_DEVICES_NAMES = get_output_devices()

# --- 1. libltc CTypes Wrapper ---

# Define the C structures in Python
class SMPTETimecode(ctypes.Structure):
    _fields_ = [
        ("timezone", ctypes.c_char * 6),
        ("years", ctypes.c_uint8),
        ("months", ctypes.c_uint8),
        ("days", ctypes.c_uint8),
        ("hours", ctypes.c_uint8),
        ("mins", ctypes.c_uint8),
        ("secs", ctypes.c_uint8),
        ("frame", ctypes.c_uint8),
    ]

# --- Find and load the libltc shared library ---
libltc_name = None

if sys.platform == "win32":
    # On Windows, look for 'libltc.dll' in the script's directory first.
    # This gets the directory the script is in, even if run from elsewhere.

    if getattr(sys, 'frozen', False):
        local_dll_path = file=os.path.join(sys._MEIPASS, 'libltc.dll') # type: ignore
    else:
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        local_dll_path = os.path.join(script_dir, 'libltc.dll')

    if os.path.exists(local_dll_path):
        libltc_name = local_dll_path
    else:
        # If not found locally, try the system path
        libltc_name = ctypes.util.find_library('ltc')
        if not libltc_name:
             libltc_name = ctypes.util.find_library('libltc') # Sometimes it's named libltc.dll
else:
    # On macOS or Linux, find_library works well
    libltc_name = ctypes.util.find_library('ltc')

if not libltc_name:
    print("Error: libltc library not found.")
    if sys.platform == "win32":
        print(f"Make sure 'libltc.dll' is in this folder:")
        print(f"{os.path.dirname(os.path.abspath(sys.argv[0]))}")
    else:
        print("Please install it (e.g., 'brew install libltc' or 'sudo apt-get install libltc11')")
    sys.exit(1)

try:
    lib = ctypes.CDLL(libltc_name)
    print(f"Successfully loaded libltc from: {libltc_name}")
except Exception as e:
    print(f"Error loading libltc: {e}")
    # This is a common 32-bit vs 64-bit error
    if sys.platform == "win32" and isinstance(e, OSError):
         print("\n--- POTENTIAL 64-bit/32-bit MISMATCH ---")
         print("This error (WinError 193) usually means you are running 64-bit Python")
         print("but you compiled a 32-bit (x86) 'libltc.dll'.")
         print(f"\nYour Python is: {64 if sys.maxsize > 2**32 else 32}-bit")
         print("\nPlease go back to Visual Studio and ensure you compiled")
         print("the 'x64' version, not 'x86' or 'Win32'.")
    sys.exit(1)

# --- Define C function prototypes for ctypes ---
# (This part is unchanged)

# --- Define C function prototypes for ctypes ---
# This tells Python what argument types (argtypes) and return types (restype)
# the C functions expect.

# LTCEncoder *ltc_encoder_create(double sample_rate, double fps, int flags);
lib.ltc_encoder_create.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_int]
lib.ltc_encoder_create.restype = ctypes.c_void_p  # LTCEncoder* is an opaque pointer

# void ltc_encoder_set_timecode(LTCEncoder *encoder, SMPTETimecode *timecode);
lib.ltc_encoder_set_timecode.argtypes = [ctypes.c_void_p, ctypes.POINTER(SMPTETimecode)]
lib.ltc_encoder_set_timecode.restype = None

# void ltc_encoder_get_timecode(LTCEncoder *encoder, SMPTETimecode *timecode);
lib.ltc_encoder_get_timecode.argtypes = [ctypes.c_void_p, ctypes.POINTER(SMPTETimecode)]
lib.ltc_encoder_get_timecode.restype = None

# void ltc_encoder_encode_frame(LTCEncoder *encoder);
lib.ltc_encoder_encode_frame.argtypes = [ctypes.c_void_p]
lib.ltc_encoder_encode_frame.restype = None

# int ltc_encoder_get_bufferptr(LTCEncoder *encoder, unsigned char **buffer, int flush);
# ltcsnd_sample_t is defined as 'unsigned char' in ltc.h
lib.ltc_encoder_get_bufferptr.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), ctypes.c_int]
lib.ltc_encoder_get_bufferptr.restype = ctypes.c_int

# void ltc_encoder_inc_timecode(LTCEncoder *encoder);
lib.ltc_encoder_inc_timecode.argtypes = [ctypes.c_void_p]
lib.ltc_encoder_inc_timecode.restype = None

# void ltc_encoder_free(LTCEncoder *encoder);
lib.ltc_encoder_free.argtypes = [ctypes.c_void_p]
lib.ltc_encoder_free.restype = None

# --- 2. Audio Generation Thread ---

def timecode_from_string(tc_str):
    """Parses a 'HH:MM:SS:FF' string into an SMPTETimecode struct."""
    parts = re.split('[:;]', tc_str) # Allow : or ;
    if len(parts) != 4:
        raise ValueError("Invalid timecode format. Use HH:MM:SS:FF.")
    
    tc = SMPTETimecode()
    tc.hours = int(parts[0])
    tc.mins = int(parts[1])
    tc.secs = int(parts[2])
    tc.frame = int(parts[3])
    # Set dummy date/timezone info
    tc.years = 24
    tc.months = 11
    tc.days = 16
    strcpy = ctypes.c_char_p(b"+0000")
    ctypes.memmove(tc.timezone, strcpy, 6)
    return tc

def format_timecode_struct(tc_struct):
    """Formats an SMPTETimecode struct back into a string."""
    return f"{tc_struct.hours:02}:{tc_struct.mins:02}:{tc_struct.secs:02}:{tc_struct.frame:02}"

audio_buffer = np.empty((0, 1), dtype='float32')
def ltc_generator_task(start_tc_str, framerate_info, device_index, thread_jam_map):
    """
    This function runs in a separate thread.
    It generates LTC audio and puts it in a queue.
    """
    stream = None
    fps_val, fps_flags = framerate_info
    try:
        # 1. Initialize Encoder
        # LTC_TV_625_50 is the enum for 25fps. Use 0 for default flags.
        encoder = lib.ltc_encoder_create(SAMPLE_RATE, fps_val, fps_flags)
        if not encoder:
            print("Failed to create LTC encoder.")
            return

        # 2. Set Initial Timecode
        start_tc = timecode_from_string(start_tc_str)
        lib.ltc_encoder_set_timecode(encoder, ctypes.byref(start_tc))

        # 3. Setup Audio Stream
        # audio_buffer = np.empty((0, 1), dtype='float32')
        def audio_callback(outdata, frames, time_info, status):
            """This is called by the audio driver in a separate thread."""
            global audio_buffer  # Use the global buffer for leftovers
            
            if pause_event.is_set():
                outdata.fill(0)
                return

            if status:
                print(status, file=sys.stderr)
            
            num_frames_needed = frames
            outdata_index = 0  # Our current write position in outdata

            # 1. First, use up any leftover samples from the last callback
            if len(audio_buffer) > 0:
                samples_to_take = min(num_frames_needed, len(audio_buffer))
                
                # Copy from our buffer into the start of outdata
                outdata[outdata_index : outdata_index + samples_to_take] = audio_buffer[:samples_to_take]
                
                # Update our position and remaining needs
                outdata_index += samples_to_take
                num_frames_needed -= samples_to_take
                
                # Remove what we just used from the global buffer
                audio_buffer = audio_buffer[samples_to_take:]

            # 2. Keep pulling new frames from the queue until outdata is full
            while num_frames_needed > 0:
                try:
                    # Get a new full LTC frame (e.g., 1920 samples)
                    new_data = audio_queue.get_nowait().reshape(-1, 1)
                    
                    # How much of this new frame can we use?
                    samples_to_take = min(num_frames_needed, len(new_data))
                    
                    # Copy the part we need into outdata
                    outdata[outdata_index : outdata_index + samples_to_take] = new_data[:samples_to_take]
                    
                    # Update our position and remaining needs
                    outdata_index += samples_to_take
                    num_frames_needed -= samples_to_take
                    
                    # Save the *leftover* part for the *next* callback
                    audio_buffer = new_data[samples_to_take:]
                    
                except queue.Empty:
                    # Queue is empty (paused or lagging).
                    # We must fill the rest of outdata with silence.
                    silence_needed = frames - outdata_index
                    if silence_needed > 0:
                        outdata[outdata_index:] = np.zeros((silence_needed, 1), dtype='float32')
                    
                    # We are done, exit the loop
                    break

        stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=audio_callback,
            dtype='float32',
            device=device_index
        )

        stream.start()

        # 4. Generation Loop
        current_tc = SMPTETimecode()
        buf_ptr = ctypes.POINTER(ctypes.c_ubyte)()

        while not stop_event.is_set():
            
            # --- 1. Check for a Jam Sync command ---
            # This is the new "off-by-one"-safe logic
            jam_sync_done = False
            try:
                # Check for a new timecode "jam" (from 'Set' button OR auto-jammer)
                new_tc_str = command_queue.get_nowait()
                new_tc = timecode_from_string(new_tc_str)
                # Set the encoder's *current* time
                lib.ltc_encoder_set_timecode(encoder, ctypes.byref(new_tc))
                print(f"Generator JAM SYNC to {new_tc_str}")
                jam_sync_done = True # We just jammed
            except queue.Empty:
                pass # No command, proceed normally

            # --- 2. Check for Pause ---
            if pause_event.is_set():
                # Paused: just sleep and wait
                time.sleep(0.01)
                continue

            # --- 3. CHECK AUTO-JAMMER (BEFORE INCREMENT) ---
            if not jam_sync_done: # Don't auto-jam if we just manual-jammed
                # Get the *current* timecode
                lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc))
                current_tc_string = format_timecode_struct(current_tc)
                
                # Check if this is a trigger frame
                if current_tc_string in thread_jam_map:
                    target_tc = thread_jam_map[current_tc_string]
                    print(f"AUTO JAMMER: {current_tc_string} -> {target_tc}")
                    # Put the jam command in the queue *for the next loop*
                    command_queue.put(target_tc)
                    # We continue this loop to encode the *trigger frame itself*
            
            # --- 4. Increment (if not just jammed) ---
            if not jam_sync_done:
                # If we *didn't* just jam, increment normally
                lib.ltc_encoder_inc_timecode(encoder)
            
            # If we *did* jam, we skip the increment
            # so the *exact* frame we set is encoded.
            
            # --- 5. Generate one frame of LTC audio ---
            lib.ltc_encoder_encode_frame(encoder)
            
            # Get a pointer to the generated audio buffer
            num_samples = lib.ltc_encoder_get_bufferptr(encoder, ctypes.byref(buf_ptr), 1)

            if num_samples > 0:
                # ... (Convert C pointer to NumPy array - UNCHANGED)
                audio_c_array = np.ctypeslib.as_array(buf_ptr, (num_samples,))
                audio_float_array = (audio_c_array.astype(np.float32) / 127.5) - 1.0
                
                # --- 6. Put audio in queue (non-blocking) ---
                try:
                    audio_queue.put(audio_float_array, timeout=0.1)
                except queue.Full:
                    continue # Queue is full (paused/stopping)
                
                # --- 7. Update the GUI display (via queue) ---
                # We already got this string, but if we jammed, get the new one
                if jam_sync_done:
                    lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc))
                    current_tc_string = format_timecode_struct(current_tc)
                
                try:
                    gui_queue.put_nowait(current_tc_string)
                except queue.Full:
                    pass # Don't block
            
            # --- 7. CHECK AUTO JAMMER --- (NEW SECTION)
                if current_tc_string in thread_jam_map:
                    target_tc = thread_jam_map[current_tc_string]
                    print(f"AUTO JAMMER: {current_tc_string} -> {target_tc}")
                    command_queue.put(target_tc)

    except Exception as e:
        print(f"Error in generator thread: {e}")
    finally:
        # Cleanup
        if stream:
            stream.stop()
            stream.close()
        if 'encoder' in locals() and encoder:
            lib.ltc_encoder_free(encoder)
        # Clear the queue in case of an unclean exit
        while not audio_queue.empty():
            audio_queue.get_nowait()
        print("Generator thread stopped.")


# --- 3. GUI and Control Logic ---

def update_gui_display():
    """
    Checks the GUI queue for new timecode strings and updates the label.
    This function runs safely on the main Tkinter thread.
    """
    try:
        while not gui_queue.empty():
            # Get the *latest* message from the queue to avoid lag
            tc_str = gui_queue.get_nowait()
            current_tc_str.set(tc_str)
    except queue.Empty:
        pass

    # Schedule this function to run again in 50ms
    root.after(50, update_gui_display)

def is_timecode_valid(tc_str, fr_name):
    """
    Checks if a timecode string is valid for a given framerate,
    including max frame and drop-frame (DF) rules.
    """
    if fr_name not in FRAMERATE_MAP:
        return False, "Unknown framerate"
        
    fps_val, flags = FRAMERATE_MAP[fr_name]
    
    try:
        parts = re.split('[:;]', tc_str)
        h, m, s, f = [int(p) for p in parts]
    except:
        return False, "Invalid format"

    # 1. Check max frame
    # We use round() to handle 23.98 (24) vs 29.97 (30)
    max_frame = int(round(fps_val))
    if f >= max_frame:
        return False, f"Frame {f} is >= max {max_frame}fps"

    # 2. Check Drop-Frame (DF) rules
    is_df = (flags & LTC_USE_DF)
    if is_df:
        # Frames 00 and 01 are dropped
        if f == 0 or f == 1:
            # At the start of every minute
            if s == 0:
                # *Except* for minutes 00, 10, 20, 30, 40, 50
                if m % 10 != 0:
                    return False, f"DF time {tc_str} does not exist"
    
    return True, "Valid"

def on_load_jam_list():
    """
    Parses the text from the jammer_text_widget into the global jam_map,
    VALIDATING against the currently selected framerate.
    """
    global jam_map
    jam_map.clear() # Clear the old map
    
    # Get the framerate to validate against
    fr_name = selected_framerate.get()
    
    text_content = jammer_text_widget.get("1.0", tk.END)
    lines = text_content.splitlines()
    
    valid_jams = 0
    errors = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
            
        parts = re.split(r'\s*->\s*|\s*>\s*', line)
        
        if len(parts) != 2:
            errors.append(f"Line {i+1}: Invalid format (missing '>')")
            continue
            
        trigger_tc = parts[0].strip()
        target_tc = parts[1].strip()
            
        # --- NEW VALIDATION ---
        is_trigger_valid, trigger_err = is_timecode_valid(trigger_tc, fr_name)
        if not is_trigger_valid:
            errors.append(f"Line {i+1} TRIGGER Error: {trigger_err}")
            continue # Don't load this jam
            
        is_target_valid, target_err = is_timecode_valid(target_tc, fr_name)
        if not is_target_valid:
            print(f"Warning: Line {i+1} TARGET '{target_tc}' is invalid ({target_err}).")
            print("         libltc will auto-correct this, which may be unexpected.")
            # We still load it, as libltc can handle (and correct)
            # invalid target times. The TRIGGER is the critical one.
        # --- END VALIDATION ---
            
        try:
            # We still use timecode_from_string to check basic syntax,
            # even though is_timecode_valid is more thorough.
            timecode_from_string(trigger_tc)
            timecode_from_string(target_tc)
            
            jam_map[trigger_tc] = target_tc
            valid_jams += 1
            
        except ValueError:
            # This is now a fallback, should be caught by is_timecode_valid
            errors.append(f"Line {i+1}: Invalid syntax")
        except Exception as e:
            errors.append(f"Line {i+1}: Unknown error: {e}")

    if errors:
        # Join the first 10 errors to keep the popup manageable
        error_summary = "\n".join(errors[:10])
        if len(errors) > 10:
            error_summary += f"\n...and {len(errors) - 10} more errors."
            
        error_title = f"Jam List Error ({fr_name})"
        error_message = f"Loaded {valid_jams} jams with {len(errors)} errors:\n\n{error_summary}"
        
        # Show a popup error box
        messagebox.showerror(error_title, error_message)
        
        # Also put a simple message in the main display
        gui_queue.put(f"Jam List Errors")
    else:
        print(f"Successfully loaded {valid_jams} jam triggers for {fr_name}.")
        print(jam_map)
        
        # Also update the main display
        gui_queue.put(f"Loaded {valid_jams} jams")

def warm_up_audio_system():
    """
    Works around a bug in PortAudio/WASAPI by opening and closing
    a safe (MME) stream first. This "warms up" the audio system.
    """
    print("Warming up audio system to prevent WASAPI bug...")
    safe_device_index = None
    try:
        host_apis = sd.query_hostapis()
        all_devices = sd.query_devices()
        
        # Find the MME API index
        mme_index = -1
        for i, api in enumerate(host_apis):
            if api['name'] == 'MME':
                mme_index = i
                break
        
        if mme_index == -1:
            print("Could not find MME driver. Warm-up cancelled.")
            return

        # --- REVISED LOGIC ---
        # Get the index of the system's default output device
        default_out_idx = -1
        try:
            # This returns the *dictionary* for the default output device
            default_device_dict = sd.query_devices(kind='output')
            default_out_idx = default_device_dict['index']
        except Exception as e:
            print(f"Could not query default output device index: {e}")

        # Check if the system's default device is an MME device
        if default_out_idx != -1:
            device = all_devices[default_out_idx]
            if device['hostapi'] == mme_index and device['max_output_channels'] > 0:
                print(f"Default device is MME: {device['name']}")
                safe_device_index = default_out_idx

        # Fallback: if default wasn't MME, find *any* MME device
        if safe_device_index is None:
            print("Default device is not MME, finding first available MME device...")
            for idx, device in enumerate(all_devices):
                if device['hostapi'] == mme_index and device['max_output_channels'] > 0:
                    safe_device_index = idx
                    break
        # --- END REVISED LOGIC ---

        if safe_device_index:
            print(f"Opening dummy stream on safe device: {all_devices[safe_device_index]['name']}")
            # Open, start, stop, and close a dummy stream
            stream = sd.OutputStream(
                device=safe_device_index, 
                samplerate=48000, 
                channels=1,
                blocksize=1024 # Use a standard blocksize
            )
            stream.start()
            time.sleep(0.01) # Let it run for 10ms
            stream.stop()
            stream.close()
            print("Audio system is now warm.")
        else:
            print("No safe MME device found. Warm-up cancelled.")

    except Exception as e:
        print(f"Audio warm-up failed (this is non-fatal): {e}")

def on_start():
    global generator_thread
    
    # --- LOCK THE SETTINGS WIDGETS ---
    if framerate_menu_widget: # Check if GUI is built
        framerate_menu_widget.config(state="disabled")
        device_menu_widget.config(state="disabled")
        jammer_text_widget.config(state="disabled") # <-- ADD THIS
        jammer_load_button.config(state="disabled") # <-- ADD THIS
    
    if generator_thread and generator_thread.is_alive():
        # It's running, so unpause it
        print("Resuming...")
        pause_event.clear()
    else:
        # It's not running, so start it
        print("Starting...")
        stop_event.clear()
        pause_event.clear()
        
        # --- GET ALL SETTINGS FROM THE GUI ---
        start_tc = tc_entry.get()
        fr_name = selected_framerate.get()
        fr_info = FRAMERATE_MAP[fr_name]
        dev_name = selected_device.get()
        dev_index = DEVICE_MAP[dev_name]
        
        try:
            # Validate format before starting
            timecode_from_string(start_tc) 
            gui_queue.put(start_tc) # Use queue to set display
            
            generator_thread = threading.Thread(
                target=ltc_generator_task,
                args=(start_tc, fr_info, dev_index, jam_map), # Pass all settings
                daemon=True
            )
            generator_thread.start()
        except ValueError as e:
            gui_queue.put(str(e))
            # If we failed to start, unlock the widgets
            if framerate_menu_widget:
                framerate_menu_widget.config(state="normal")
                device_menu_widget.config(state="normal")

def on_pause():
    global audio_buffer  # Get access to the leftover sample buffer
    print("Pausing...")
    pause_event.set()
    
    # Clear the queue to prevent old frames from playing on resume
    while not audio_queue.empty():
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            break # Queue is empty
    
    # Also clear the callback's internal (leftover) buffer
    audio_buffer = np.empty((0, 1), dtype='float32')

def on_stop():
    global audio_buffer
    print("Stopping...")
    stop_event.set()
    pause_event.clear()
    
    # Clear queues to help thread exit
    while not command_queue.empty(): command_queue.get_nowait()
    while not audio_queue.empty(): audio_queue.get_nowait()
    audio_buffer = np.empty((0, 1), dtype='float32')
        
    if generator_thread:
        generator_thread.join() # Wait for the thread to finish
    
    print("Stopped.")
    
    # --- UNLOCK THE SETTINGS WIDGETS ---
    try:
        if framerate_menu_widget:
            framerate_menu_widget.config(state="normal")
            device_menu_widget.config(state="normal")
            jammer_text_widget.config(state="normal") # <-- ADD THIS
            jammer_load_button.config(state="normal")
    except tk.TclError:
        pass # Window is closing, widgets are already gone

def on_set():
    """
    Sets the timecode. If generator is running,
    it "jam syncs" it. If stopped, it just updates the display.
    """
    new_tc_str = tc_entry.get()
    try:
        # 1. Validate the timecode string first
        timecode_from_string(new_tc_str) 
    except ValueError as e:
        # Show error in the display
        gui_queue.put(str(e)) # Use the queue to show the error
        return

    if generator_thread and generator_thread.is_alive():
        # 2. Generator is RUNNING: send the jam sync command
        print(f"Sending JAM SYNC command: {new_tc_str}")
        command_queue.put(new_tc_str)
    else:
        # 3. Generator is STOPPED: just update the display
        print("Generator stopped, setting display timecode.")
        # Put the new, validated TC string into the GUI queue
        # The update_gui_display() function will handle setting the label
        gui_queue.put(new_tc_str)

def on_closing():
    """Called when the window's 'X' button is pressed."""
    jam_map.clear()
    on_stop()
    root.destroy()

# --- Build the GUI ---
root = tk.Tk()
root.title("Python LTC Generator")
root.geometry("480x550") # Made window taller

# --- GUI/Settings Variables ---
current_tc_str = tk.StringVar(value="01:00:00:00")
selected_framerate = tk.StringVar()
selected_device = tk.StringVar()

# Main frame
main_frame = tk.Frame(root, padx=20, pady=20)
main_frame.pack(fill=tk.BOTH, expand=True)

# Timecode Display
display_font = font.Font(family='Courier', size=36, weight='bold')
tc_display = tk.Label(main_frame, textvariable=current_tc_str, font=display_font, bg='black', fg='lime green', relief=tk.SUNKEN, borderwidth=2)
tc_display.pack(pady=10, fill=tk.X)

# Control Frame
control_frame = tk.Frame(main_frame)
control_frame.pack(fill=tk.X)

# Button Frame
button_frame = tk.Frame(control_frame)
button_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

btn_start = tk.Button(button_frame, text="Start / Resume", command=on_start, height=2)
btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

btn_pause = tk.Button(button_frame, text="Pause", command=on_pause, height=2)
btn_pause.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

btn_stop = tk.Button(button_frame, text="Stop", command=on_stop, height=2)
btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

# Set Timecode Frame
set_frame = tk.Frame(main_frame)
set_frame.pack(fill=tk.X, pady=10)

tk.Label(set_frame, text="Start TC:").pack(side=tk.LEFT)
tc_entry = tk.Entry(set_frame, width=15, font=('Courier', 14))
tc_entry.insert(0, "01:00:00:00")
tc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

btn_set = tk.Button(set_frame, text="Set", command=on_set)
btn_set.pack(side=tk.LEFT)

# --- NEW SETTINGS FRAME ---
settings_frame = tk.LabelFrame(main_frame, text="Settings (Locked while running)", padx=10, pady=10)
settings_frame.pack(fill=tk.X, pady=10)

# Framerate
fr_frame = tk.Frame(settings_frame)
fr_frame.pack(fill=tk.X, pady=2)
tk.Label(fr_frame, text="Framerate:", width=12, anchor='w').pack(side=tk.LEFT)

# Get the keys from our map
framerate_options = list(FRAMERATE_MAP.keys())
selected_framerate.set(framerate_options[framerate_options.index(FRAMERATE)]) # Default to FRAMERATE
framerate_menu_widget = tk.OptionMenu(fr_frame, selected_framerate, *framerate_options)
framerate_menu_widget.pack(fill=tk.X)

# Audio Device
dev_frame = tk.Frame(settings_frame)
dev_frame.pack(fill=tk.X, pady=2)
tk.Label(dev_frame, text="Audio Device:", width=12, anchor='w').pack(side=tk.LEFT)

# Use the device list we populated (OUTPUT_DEVICES)
try:
    # Get the name of the system's *actual* default output device
    default_device_name = sd.query_devices(kind='output')['name']
    
    # Check if this default device is in our (now filtered) list
    if default_device_name in OUTPUT_DEVICES_NAMES:
        selected_device.set(default_device_name)
    else:
        # If not (e.g., it was an MME device and we only listed WASAPI),
        # just select the first device from our new clean list.
        selected_device.set(OUTPUT_DEVICES_NAMES[0])
except Exception:
    # Fallback in case of any error
    selected_device.set(OUTPUT_DEVICES_NAMES[0])

device_menu_widget = tk.OptionMenu(dev_frame, selected_device, *OUTPUT_DEVICES_NAMES)
device_menu_widget.pack(fill=tk.X)

# --- NEW AUTO JAMMER FRAME ---
jammer_frame = tk.LabelFrame(main_frame, text="Auto Jammer (Locked while running)", padx=10, pady=10)
jammer_frame.pack(fill=tk.BOTH, pady=10, expand=True)

tk.Label(jammer_frame, text="Jam List (Format: TRIGGER_TC > TARGET_TC)", anchor='w').pack(fill=tk.X)
jammer_text_widget = tk.Text(jammer_frame, height=5, width=40)
jammer_text_widget.pack(fill=tk.BOTH, expand=True, pady=(5, 10))
# Add an example
jammer_text_widget.insert("1.0", "00:00:59:29 > 01:00:00:00\n")

jammer_load_button = tk.Button(jammer_frame, text="Load Jam List", command=on_load_jam_list)
jammer_load_button.pack(fill=tk.X)


# Start the main loop
root.protocol("WM_DELETE_WINDOW", on_closing) # Handle window close
warm_up_audio_system()
update_gui_display()
root.mainloop()