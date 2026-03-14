import sys
import sounddevice as sd

def get_output_devices():
    """
    Builds a list of unique device names, prioritizing the most stable drivers (MME).
    Returns:
        (list): Sorted list of unique device names.
        (dict): Map of {name: index}.
    """
    device_map = {}
    
    try:
        all_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        
        # Build API index to name map
        api_map = {i: api['name'] for i, api in enumerate(host_apis)}

        # Driver Stability Priority (Lower is better)
        DRIVER_PRIORITY = {
            "MME": 1,
            "Windows DirectSound": 2,
            "Windows WASAPI": 3,
        }
        
        # Track the best driver choice for each unique hardware name
        best_choices = {}

        for idx, device in enumerate(all_devices):
            if device['max_output_channels'] > 0:
                api_name = api_map.get(device['hostapi'], "Unknown API")
                
                # Skip WDM-KS as it tends to be unstable with libltc/sounddevice
                if api_name == 'Windows WDM-KS':
                    continue
                
                device_name = device['name']
                priority = DRIVER_PRIORITY.get(api_name, 99)
                
                # Update map if this is a new device or a better driver for an existing one
                if device_name not in best_choices or priority < best_choices[device_name][0]:
                    best_choices[device_name] = (priority, idx)

        # Build final outputs
        output_names = []
        for name, (prio, idx) in best_choices.items():
            device_map[name] = idx
            output_names.append(name)
        
        if not output_names:
            return ["Default"], {"Default": 0}
            
        return sorted(output_names), device_map
        
    except Exception as e:
        print(f"CRITICAL: Could not query audio devices: {e}")
        return ["Default"], {"Default": 0}

def warm_up_audio_system():
    """
    Silently opens/closes an MME stream to 'warm up' the Windows audio subsystem.
    This prevents the WASAPI initialization bug.
    """
    try:
        # Get the default MME output device
        all_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        mme_idx = next((i for i, api in enumerate(host_apis) if api['name'] == 'MME'), None)
        
        if mme_idx is not None:
            # Find first available MME output
            safe_idx = next((i for i, d in enumerate(all_devices) 
                            if d['hostapi'] == mme_idx and d['max_output_channels'] > 0), None)
            
            if safe_idx is not None:
                stream = sd.OutputStream(device=safe_idx, samplerate=48000, channels=1)
                stream.start()
                stream.stop()
                stream.close()
    except Exception:
        pass # Non-fatal