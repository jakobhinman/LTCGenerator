import re
import ctypes

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

# Import the struct from main or define it here
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

def samples_to_tc(sample_index, sample_rate, fr_name, start_tc_str):
    """
    Calculates the current timecode string based on sample position and start TC.
    """
    fps_val = FRAMERATE_MAP[fr_name][0]
    # Calculate offset in frames
    total_offset_frames = int((sample_index / sample_rate) * fps_val)
    
    # Parse start TC
    parts = re.split(r'[^0-9]+', start_tc_str)
    parts = [p for p in parts if p]
    h, m, s, f = map(int, parts)
    
    # Calculate total frames at start
    start_total_frames = int(((h * 3600) + (m * 60) + s) * fps_val) + f
    
    # New total frames
    current_total_frames = start_total_frames + total_offset_frames
    
    # Break back into TC
    f_new = int(current_total_frames % fps_val)
    remaining_s = int(current_total_frames // fps_val)
    s_new = remaining_s % 60
    remaining_m = remaining_s // 60
    m_new = remaining_m % 60
    h_new = remaining_m // 60
    
    # Use semicolon for DF rates
    sep = ";" if (FRAMERATE_MAP[fr_name][1] & LTC_USE_DF) else ":"
    return f"{h_new:02}:{m_new:02}:{s_new:02}{sep}{f_new:02}"

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

def normalize_timecode(tc_str, fr_name):
    """
    Flexibly parses user input (e.g., '0.30.12.1' or '12 1') and 
    returns a standard HH:MM:SS:FF or HH:MM:SS;FF string.
    """
    # Split by any non-digit character
    parts = re.split(r'[^0-9]+', tc_str.strip())
    # Filter out empty strings from re.split
    parts = [p for p in parts if p]
    
    # Pad to 4 parts (HH, MM, SS, FF)
    while len(parts) < 4:
        parts.insert(0, "00")
    
    # Take only the last 4 if user entered too many
    parts = parts[-4:]
    
    # Standardize to 2 digits each
    h, m, s, f = [p.zfill(2) for p in parts]
    
    # Determine separator for frames (';' for DF, ':' for NDF)
    fr_info = FRAMERATE_MAP.get(fr_name, (30.0, 0))
    sep = ";" if (fr_info[1] & LTC_USE_DF) else ":"
    
    return f"{h}:{m}:{s}{sep}{f}"

def is_timecode_valid(tc_str, fr_name):
    """
    Checks if a timecode string is valid for a given framerate,
    including max frame and drop-frame (DF) rules.
    """
    if fr_name not in FRAMERATE_MAP:
        return False, "Unknown framerate"
        
    fps_val, flags = FRAMERATE_MAP[fr_name]
    
    try:
        # Match digits regardless of separator used
        parts = re.split(r'[^0-9]+', tc_str)
        parts = [p for p in parts if p]
        h, m, s, f = [int(p) for p in parts]
    except (ValueError, IndexError):
        return False, "Invalid format"

    if h >= 24 or m >= 60 or s >= 60:
        return False, "Time component out of range"

    # Check max frame
    max_frame = int(round(fps_val))
    if f >= max_frame:
        return False, f"Frame {f} is >= max {max_frame}fps"

    # Check Drop-Frame (DF) rules
    if (flags & LTC_USE_DF):
        # Frames 00 and 01 are dropped at start of every minute except 0, 10, 20...
        if f < 2 and s == 0 and m % 10 != 0:
            return False, f"DF time {tc_str} does not exist"
    
    return True, "Valid"