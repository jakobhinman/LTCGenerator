import tkinter as tk
from tkinter import font, messagebox

class GeneratorGUI:
    def __init__(self, root, device_names, framerate_options, callbacks):
        self.root = root
        self.callbacks = callbacks # Dictionary of functions: start, stop, pause, set, load_jam
        
        self.root.title("Python LTC Generator")
        self.root.geometry("480x580")
        
        # Variables
        self.current_tc_str = tk.StringVar(value="01:00:00:00")
        self.selected_framerate = tk.StringVar(value=framerate_options[0])
        self.selected_device = tk.StringVar()
        
        self.setup_ui(device_names, framerate_options)

    def setup_ui(self, device_names, framerate_options):
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Display
        display_font = font.Font(family='Courier', size=36, weight='bold')
        self.tc_display = tk.Label(main_frame, textvariable=self.current_tc_str, font=display_font, 
                                   bg='black', fg='lime green', relief=tk.SUNKEN, borderwidth=2)
        self.tc_display.pack(pady=10, fill=tk.X)

        # Buttons
        btn_frame = tk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)
        tk.Button(btn_frame, text="Start / Resume", command=self.callbacks['start'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(btn_frame, text="Pause", command=self.callbacks['pause'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(btn_frame, text="Stop", command=self.callbacks['stop'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # Entry
        set_frame = tk.Frame(main_frame, pady=10)
        set_frame.pack(fill=tk.X)
        tk.Label(set_frame, text="Start TC:").pack(side=tk.LEFT)
        self.tc_entry = tk.Entry(set_frame, width=15, font=('Courier', 14))
        self.tc_entry.insert(0, "01:00:00:00")
        self.tc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        tk.Button(set_frame, text="Set", command=self.callbacks['set']).pack(side=tk.LEFT)

        # Settings
        settings_frame = tk.LabelFrame(main_frame, text="Settings", padx=10, pady=10)
        settings_frame.pack(fill=tk.X, pady=10)

        self.fr_menu = tk.OptionMenu(settings_frame, self.selected_framerate, *framerate_options)
        self.fr_menu.pack(fill=tk.X)
        
        self.dev_menu = tk.OptionMenu(settings_frame, self.selected_device, *device_names)
        self.dev_menu.pack(fill=tk.X)

        # Jammer
        jammer_frame = tk.LabelFrame(main_frame, text="Auto Jammer", padx=10, pady=10)
        jammer_frame.pack(fill=tk.BOTH, pady=10, expand=True)
        self.jammer_text = tk.Text(jammer_frame, height=5, width=40)
        self.jammer_text.pack(fill=tk.BOTH, expand=True)
        self.jammer_text.insert("1.0", "00:00:59:29 > 01:00:00:00\n")
        self.load_btn = tk.Button(jammer_frame, text="Load Jam List", command=self.callbacks['load_jam'])
        self.load_btn.pack(fill=tk.X)

        # Buffer Health Indicator
        self.health_frame = tk.Frame(main_frame)
        self.health_frame.pack(fill=tk.X, pady=5)
        tk.Label(self.health_frame, text="Buffer Health:").pack(side=tk.LEFT)
        self.health_indicator = tk.Canvas(self.health_frame, width=20, height=20, bg='grey', highlightthickness=0)
        self.health_indicator.pack(side=tk.LEFT, padx=5)
        self.health_circle = self.health_indicator.create_oval(2, 2, 18, 18, fill='grey')
    
    def update_health(self, percent):
        """Updates the color of the health indicator."""
        if percent > 0.50: color = "lime green"
        elif percent > 0.15: color = "yellow"
        else: color = "red"
        self.health_indicator.itemconfig(self.health_circle, fill=color)

    def lock_ui(self, locked=True):
        state = "disabled" if locked else "normal"
        self.fr_menu.config(state=state)
        self.dev_menu.config(state=state)
        self.jammer_text.config(state=state)
        self.load_btn.config(state=state)