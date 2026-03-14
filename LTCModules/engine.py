import ctypes
import numpy as np
import sounddevice as sd
import queue
import time
import sys
from .helpers import timecode_from_string, format_timecode_struct, SMPTETimecode

def ltc_generator_task(lib, start_tc_str, framerate_info, device_index, 
                       thread_jam_map, stop_event, pause_event, 
                       audio_queue, gui_queue, command_queue, sample_rate):
    """
    Generates LTC audio and puts it in a queue for the audio callback.
    """
    stream = None
    fps_val, fps_flags = framerate_info
    
    try:
        # 1. Initialize Encoder
        encoder = lib.ltc_encoder_create(sample_rate, fps_val, fps_flags)
        if not encoder:
            return

        # 2. Set Initial Timecode
        start_tc = timecode_from_string(start_tc_str)
        lib.ltc_encoder_set_timecode(encoder, ctypes.byref(start_tc))

        # 3. Setup Audio Stream
        local_audio_buffer = [np.empty((0, 1), dtype='float32')]
        current_on_air_tc = [""]

        def audio_callback(outdata, frames, time_info, status):
            if pause_event.is_set():
                outdata.fill(0)
                return

            num_frames_needed = frames
            outdata_index = 0

            # Handle leftovers
            if len(local_audio_buffer[0]) > 0:
                samples_to_take = min(num_frames_needed, len(local_audio_buffer[0]))
                outdata[outdata_index : outdata_index + samples_to_take] = local_audio_buffer[0][:samples_to_take]
                outdata_index += samples_to_take
                num_frames_needed -= samples_to_take
                local_audio_buffer[0] = local_audio_buffer[0][samples_to_take:]

            while num_frames_needed > 0:
                try:
                    # NEW: Get the tuple (audio, timecode)
                    new_data, tc_str = audio_queue.get_nowait()
                    new_data = new_data.reshape(-1, 1)
                    
                    # Update "On-Air" TC only when we actually pull from the queue
                    if tc_str != current_on_air_tc[0]:
                        current_on_air_tc[0] = tc_str
                        # Send to GUI queue here - this is the "playback" moment
                        try:
                            gui_queue.put_nowait(tc_str)
                        except: pass

                    samples_to_take = min(num_frames_needed, len(new_data))
                    outdata[outdata_index : outdata_index + samples_to_take] = new_data[:samples_to_take]
                    outdata_index += samples_to_take
                    num_frames_needed -= samples_to_take
                    local_audio_buffer[0] = new_data[samples_to_take:]
                except queue.Empty:
                    if num_frames_needed > 0:
                        outdata[outdata_index:] = 0
                    break

        stream = sd.OutputStream(
            samplerate=sample_rate, channels=1, callback=audio_callback,
            dtype='float32', device=device_index
        )
        stream.start()

        # 4. Generation Loop
        current_tc = SMPTETimecode()
        buf_ptr = ctypes.POINTER(ctypes.c_ubyte)()
        first_run = True

        while not stop_event.is_set():
            jam_sync_done = False
            try:
                new_tc_str = command_queue.get_nowait()
                new_tc = timecode_from_string(new_tc_str)
                lib.ltc_encoder_set_timecode(encoder, ctypes.byref(new_tc))
                jam_sync_done = True 
            except queue.Empty:
                pass 

            if pause_event.is_set():
                time.sleep(0.01)
                continue

            if not jam_sync_done:
                lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc))
                current_tc_string = format_timecode_struct(current_tc)
                if current_tc_string in thread_jam_map:
                    command_queue.put(thread_jam_map[current_tc_string])
            
            if not jam_sync_done and not first_run:
                lib.ltc_encoder_inc_timecode(encoder)
            
            lib.ltc_encoder_encode_frame(encoder)
            num_samples = lib.ltc_encoder_get_bufferptr(encoder, ctypes.byref(buf_ptr), 1)

            if num_samples > 0:
                audio_c_array = np.ctypeslib.as_array(buf_ptr, (num_samples,))
                audio_float_array = (audio_c_array.astype(np.float32) / 127.5) - 1.0
                
                try:
                    audio_queue.put((audio_float_array, current_tc_string), timeout=0.1)
                except queue.Full:
                    continue
                
                if jam_sync_done or first_run:
                    lib.ltc_encoder_get_timecode(encoder, ctypes.byref(current_tc))
                    current_tc_string = format_timecode_struct(current_tc)
                
                try:
                    gui_queue.put_nowait(current_tc_string)
                except queue.Full:
                    pass
            first_run = False

    finally:
        if stream:
            stream.stop()
            stream.close()
        if encoder:
            lib.ltc_encoder_free(encoder)