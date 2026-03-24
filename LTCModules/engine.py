import ctypes
import numpy as np
import sounddevice as sd
import queue
import time
import sys
import soundfile as sf  # NEW: Required for Advanced Mode playback
from .helpers import timecode_from_string, format_timecode_struct, SMPTETimecode

def ltc_generator_task(lib, start_tc_str, framerate_info, device_index, 
                       thread_jam_map, stop_event, pause_event, 
                       audio_queue, gui_queue, command_queue, sample_rate,
                       baked_file_path=None, baked_meta=None, channel_map=None): # New parameter for Advanced Mode
    """
    Handles both Real-time LTC generation and Baked-file playback.
    """
    stream = None
    fps_val, fps_flags = framerate_info
    
    # State handles
    encoder = None
    sf_file = None

    current_on_air_tc = [""]
    try:
        if baked_file_path:
            sf_file = sf.SoundFile(baked_file_path)
            # Determine device max channels to open a wide enough stream
            dev_info = sd.query_devices(device_index, 'output')
            num_stream_channels = dev_info['max_output_channels']
        else:
            num_stream_channels = 1

        def audio_callback(outdata, frames, time_info, status):
            if pause_event.is_set():
                outdata.fill(0)
                return

            outdata.fill(0) # Default to silence on all channels
            num_frames_needed = frames
            outdata_index = 0

            while num_frames_needed > 0:
                try:
                    new_data, tc_str = audio_queue.get_nowait()
                    s = min(num_frames_needed, len(new_data))
                    
                    if baked_file_path and channel_map:
                        # Route LTC (Ch 1 in file) to user-selected output
                        outdata[outdata_index : outdata_index + s, channel_map[0]] = new_data[:s, 0]
                        # Route Music L/R (Ch 2-3 in file) to user-selected outputs
                        outdata[outdata_index : outdata_index + s, channel_map[1]] = new_data[:s, 1]
                        outdata[outdata_index : outdata_index + s, channel_map[2]] = new_data[:s, 2]
                    else:
                        outdata[outdata_index : outdata_index + s, 0] = new_data[:s, 0]

                    if tc_str != current_on_air_tc[0]:
                        current_on_air_tc[0] = tc_str
                        try: gui_queue.put_nowait(tc_str)
                        except: pass

                    outdata_index += s
                    num_frames_needed -= s
                except queue.Empty:
                    break

        stream = sd.OutputStream(
            samplerate=sample_rate, 
            channels=num_stream_channels, 
            callback=audio_callback,
            dtype='float32', device=device_index
        )
        stream.start()

        # --- 2. THE MAIN LOOP ---
        current_tc_struct = SMPTETimecode()
        buf_ptr = ctypes.POINTER(ctypes.c_ubyte)()
        first_run = True

        while not stop_event.is_set():
            jam_sync_done = False
            
            # Handle user commands (Set TC or Seek)
            try:
                cmd_tc_str = command_queue.get_nowait()
                if sf_file:
                    # Logic for seeking in baked file will be added here
                    pass
                else:
                    new_tc = timecode_from_string(cmd_tc_str)
                    lib.ltc_encoder_set_timecode(encoder, ctypes.byref(new_tc))
                    jam_sync_done = True 
            except queue.Empty:
                pass 

            if pause_event.is_set():
                time.sleep(0.01)
                continue

            # --- BRANCH: ADVANCED VS SIMPLE ---
            if sf_file:
                # ADVANCED MODE: Read one frame's worth of samples from the baked file
                samples_per_frame = int(sample_rate / fps_val)
                chunk = sf_file.read(samples_per_frame, dtype='float32')
                
                if len(chunk) == 0:
                    stop_event.set()
                    break
                
                # Placeholder for calculating TC string based on file position
                playback_tc = "PLAYING" 
                audio_queue.put((chunk, playback_tc), timeout=0.1)
                
            else:
                # SIMPLE MODE: Original real-time generation logic
                if not jam_sync_done:
                    lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc_struct))
                    current_tc_string = format_timecode_struct(current_tc_struct)
                    
                    # Auto-jammer check
                    if current_tc_string in thread_jam_map:
                        command_queue.put(thread_jam_map[current_tc_string])
                
                if not jam_sync_done and not first_run:
                    lib.ltc_encoder_inc_timecode(encoder)
                
                # Generate audio frame
                lib.ltc_encoder_encode_frame(encoder)
                num_samples = lib.ltc_encoder_get_bufferptr(encoder, ctypes.byref(buf_ptr), 1)

                if num_samples > 0:
                    audio_c_array = np.ctypeslib.as_array(buf_ptr, (num_samples,))
                    audio_float_array = (audio_c_array.astype(np.float32) / 127.5) - 1.0
                    
                    # Get the final string for this specific encoded frame
                    if jam_sync_done or first_run:
                        lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc_struct))
                        current_tc_string = format_timecode_struct(current_tc_struct)
                        
                    audio_queue.put((audio_float_array, current_tc_string), timeout=0.1)

            first_run = False

    finally:
        # Cleanup
        if stream: stream.stop(); stream.close()
        if encoder: lib.ltc_encoder_free(encoder)
        if sf_file: sf_file.close()