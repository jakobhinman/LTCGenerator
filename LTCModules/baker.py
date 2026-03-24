import os
import subprocess
import json
import wave
import ctypes
import hashlib
import shutil
from .helpers import (
    timecode_from_string, format_timecode_struct, 
    SMPTETimecode, FRAMERATE_MAP
)

class AdvancedBaker:
    def __init__(self, lib, temp_dir="temp_bake"):
        self.lib = lib
        self.temp_dir = temp_dir

    def get_hash(self, data_str):
        return hashlib.sha256(data_str.encode()).hexdigest()

    def get_audio_folder_hash(self, folder_path):
        """Creates a hash based on filenames and sizes in the folder."""
        files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(('.wav', '.mp3', '.m4a'))])
        hash_seed = "".join([f + str(os.path.getsize(os.path.join(folder_path, f))) for f in files])
        return self.get_hash(hash_seed)

    def generate_ltc_track(self, output_path, total_duration, jam_map, fr_name, start_tc_str):
        """Generates a mono LTC WAV file for the specific duration and jam points."""
        fps_val, fps_flags = FRAMERATE_MAP[fr_name]
        total_samples = int(total_duration * 48000)
        
        encoder = self.lib.ltc_encoder_create(48000, fps_val, fps_flags)
        curr_tc = timecode_from_string(start_tc_str)
        self.lib.ltc_encoder_set_timecode(encoder, ctypes.byref(curr_tc))

        with wave.open(output_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(1) # libltc outputs 8-bit unsigned
            wav_file.setframerate(48000)

            samples_written = 0
            # Track if a jam was already executed for a specific frame string
            last_jammed_tc = ""

            while samples_written < total_samples:
                # Check current timecode for jam triggers
                temp_struct = SMPTETimecode()
                self.lib.ltc_encoder_get_timecode(encoder, ctypes.byref(temp_struct))
                tc_str = format_timecode_struct(temp_struct)

                if tc_str in jam_map and tc_str != last_jammed_tc:
                    new_tc = timecode_from_string(jam_map[tc_str])
                    self.lib.ltc_encoder_set_timecode(encoder, ctypes.byref(new_tc))
                    last_jammed_tc = tc_str

                self.lib.ltc_encoder_encode_frame(encoder)
                buf_ptr = ctypes.POINTER(ctypes.c_ubyte)()
                num_samples = self.lib.ltc_encoder_get_bufferptr(encoder, ctypes.byref(buf_ptr), 1)
                
                wav_file.writeframes(ctypes.string_at(buf_ptr, num_samples))
                samples_written += num_samples
                self.lib.ltc_encoder_inc_timecode(encoder)

        self.lib.ltc_encoder_free(encoder)

    def bake(self, music_folder, jam_map, pad_secs, fr_name, start_tc, output_file, progress_cb=None):
        """
        Creates a clean temporary environment, performs the bake, 
        and then deletes the entire environment.
        """
        # --- 1. DEFINE PATHS FIRST ---
        list_path = os.path.join(self.temp_dir, "concat.txt")
        stitched_audio = os.path.join(self.temp_dir, "music_stitched.wav")
        ltc_wav = os.path.join(self.temp_dir, "ltc_track.wav")

        try:
            # --- 2. CREATE A CLEAN DIRECTORY ---
            if progress_cb: progress_cb("Initializing workspace...")
            # Ensure we start fresh: delete if it somehow exists, then recreate
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            os.makedirs(self.temp_dir)

            # 3. Create the concat list for FFmpeg
            if progress_cb: progress_cb("Preparing track list...")
            files = sorted([f for f in os.listdir(music_folder) if f.lower().endswith(('.wav', '.mp3', '.m4a'))])
            
            if not files:
                raise ValueError("No compatible audio files found in the folder.")

            with open(list_path, "w") as f:
                for file in files:
                    # FFmpeg concat demuxer requirement
                    f.write(f"file '{os.path.abspath(os.path.join(music_folder, file))}'\n")
            
            # 4. Stitch Music
            if progress_cb: progress_cb("Stitching audio files...")
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, 
                            "-ar", "48000", "-ac", "2", stitched_audio], check=True, capture_output=True)

            # 5. Get Duration
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                                    "-of", "default=noprint_wrappers=1:nokey=1", stitched_audio], 
                                   capture_output=True, text=True)
            duration = float(probe.stdout) + pad_secs

            # 6. Generate LTC track
            if progress_cb: progress_cb(f"Generating LTC ({duration:.2f}s)...")
            fps_val, fps_flags = FRAMERATE_MAP[fr_name]
            encoder = self.lib.ltc_encoder_create(48000.0, fps_val, fps_flags)
            lib_start = timecode_from_string(start_tc)
            self.lib.ltc_encoder_set_timecode(encoder, ctypes.byref(lib_start))

            with wave.open(ltc_wav, 'wb') as wav_file:
                wav_file.setnchannels(1); wav_file.setsampwidth(1); wav_file.setframerate(48000)
                samples_written = 0
                target_samples = int(duration * 48000)
                while samples_written < target_samples:
                    curr = SMPTETimecode(); self.lib.ltc_encoder_get_timecode(encoder, ctypes.byref(curr))
                    tc_str = format_timecode_struct(curr)
                    
                    if tc_str in jam_map:
                        target = timecode_from_string(jam_map[tc_str])
                        self.lib.ltc_encoder_set_timecode(encoder, ctypes.byref(target))
                    
                    self.lib.ltc_encoder_encode_frame(encoder)
                    buf_ptr = ctypes.POINTER(ctypes.c_ubyte)()
                    n = self.lib.ltc_encoder_get_bufferptr(encoder, ctypes.byref(buf_ptr), 1)
                    wav_file.writeframes(ctypes.string_at(buf_ptr, n))
                    samples_written += n
                    self.lib.ltc_encoder_inc_timecode(encoder)
            self.lib.ltc_encoder_free(encoder)

            # 7. Final Merge (Multi-channel approach)
            if progress_cb: progress_cb("Finalizing Master Bake...")
            meta = json.dumps({"jams": jam_map, "fps": fr_name, "start": start_tc, "pad": pad_secs})
            subprocess.run([
                "ffmpeg", "-y", "-i", ltc_wav, "-i", stitched_audio,
                "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=3.0[a]", "-map", "[a]",
                "-metadata", f"comment={meta}", "-c:a", "pcm_s16le", output_file
            ], check=True, capture_output=True)

            return True

        finally:
            # --- CLEANUP: DELETE THE ENTIRE DIRECTORY ---
            if progress_cb: progress_cb("Cleaning up temporary directory...")
            if os.path.exists(self.temp_dir):
                try:
                    # ignore_errors=True handles cases where a file handle 
                    # hasn't been fully released by FFmpeg yet.
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                except Exception as e:
                    print(f"Cleanup error: {e}")
            if progress_cb: progress_cb("Bake process complete.")