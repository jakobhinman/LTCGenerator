import re
import ctypes
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