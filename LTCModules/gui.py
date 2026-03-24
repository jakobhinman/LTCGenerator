import tkinter as tk
from tkinter import font, messagebox, ttk, filedialog

class GeneratorGUI:
    def __init__(self, root, device_names, framerate_options, callbacks):
        self.root = root
        self.callbacks = callbacks # Dictionary of functions: start, stop, pause, set, load_jam
        
        self.root.title("Python LTC Generator")
        self.root.geometry("500x700") # Slightly wider/taller
        
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.simple_tab = tk.Frame(self.notebook)
        self.advanced_tab = tk.Frame(self.notebook)

        self.notebook.add(self.simple_tab, text="Simple Mode (Real-time)")
        self.notebook.add(self.advanced_tab, text="Advanced Mode (Baking)")
        
        # Variables
        self.current_tc_str = tk.StringVar(value="01:00:00:00")
        self.selected_framerate = tk.StringVar(value=framerate_options[0])
        self.selected_device = tk.StringVar()

        self.setup_simple_ui(device_names, framerate_options)
        self.setup_advanced_ui(device_names)        

        
    def setup_simple_ui(self, device_names, framerate_options):

        # Display
        display_font = font.Font(family='Courier', size=36, weight='bold')
        self.tc_display = tk.Label(self.simple_tab, textvariable=self.current_tc_str, font=display_font, 
                                   bg='black', fg='lime green', relief=tk.SUNKEN, borderwidth=2)
        self.tc_display.pack(pady=10, fill=tk.X)

        # Buttons
        btn_frame = tk.Frame(self.simple_tab)
        btn_frame.pack(fill=tk.X)
        tk.Button(btn_frame, text="Start / Resume", command=self.callbacks['start'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(btn_frame, text="Pause", command=self.callbacks['pause'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(btn_frame, text="Stop", command=self.callbacks['stop'], height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # Entry
        set_frame = tk.Frame(self.simple_tab, pady=10)
        set_frame.pack(fill=tk.X)
        tk.Label(set_frame, text="Start TC:").pack(side=tk.LEFT)
        self.tc_entry = tk.Entry(set_frame, width=15, font=('Courier', 14))
        self.tc_entry.insert(0, "01:00:00:00")
        self.tc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        # Add focus out binding
        self.tc_entry.bind("<FocusOut>", self.callbacks['tc_focus_out'])
        
        tk.Button(set_frame, text="Set", command=self.callbacks['set']).pack(side=tk.LEFT)

        # Settings
        settings_frame = tk.LabelFrame(self.simple_tab, text="Settings", padx=10, pady=10)
        settings_frame.pack(fill=tk.X, pady=10)

        self.fr_menu = tk.OptionMenu(settings_frame, self.selected_framerate, *framerate_options)
        self.fr_menu.pack(fill=tk.X)
        
        self.dev_menu = tk.OptionMenu(settings_frame, self.selected_device, *device_names)
        self.dev_menu.pack(fill=tk.X)

        # Jammer
        jammer_frame = tk.LabelFrame(self.simple_tab, text="Auto Jammer", padx=10, pady=10)
        jammer_frame.pack(fill=tk.BOTH, pady=10, expand=True)
        self.jammer_text = tk.Text(jammer_frame, height=5, width=40)
        self.jammer_text.pack(fill=tk.BOTH, expand=True)
        self.jammer_text.insert("1.0", "00:00:59:29 > 01:00:00:00\n")
        self.load_btn = tk.Button(jammer_frame, text="Load Jam List", command=self.callbacks['load_jam'])
        self.load_btn.pack(fill=tk.X)

        # Buffer Health Indicator
        self.health_frame = tk.Frame(self.simple_tab)
        self.health_frame.pack(fill=tk.X, pady=5)
        tk.Label(self.health_frame, text="Buffer Health:").pack(side=tk.LEFT)
        self.health_indicator = tk.Canvas(self.health_frame, width=20, height=20, bg='grey', highlightthickness=0)
        self.health_indicator.pack(side=tk.LEFT, padx=5)
        self.health_circle = self.health_indicator.create_oval(2, 2, 18, 18, fill='grey')
    
    def setup_advanced_ui(self, device_names):
        adv = tk.Frame(self.advanced_tab, padx=20, pady=20)
        adv.pack(fill=tk.BOTH, expand=True)

        # File selection
        tk.Label(adv, text="Project Settings", font=("Arial", 12, "bold")).pack(anchor='w')
        
        self.music_dir = tk.StringVar(value="Not Selected")
        tk.Label(adv, textvariable=self.music_dir, fg="blue").pack(anchor='w', pady=5)
        tk.Button(adv, text="Select Music Folder", command=self.pick_folder).pack(fill=tk.X)

        # Padding
        pad_frame = tk.Frame(adv, pady=10)
        pad_frame.pack(fill=tk.X)
        tk.Label(pad_frame, text="End Padding (Seconds):").pack(side=tk.LEFT)
        self.pad_entry = tk.Entry(pad_frame, width=5)
        self.pad_entry.insert(0, "30")
        self.pad_entry.pack(side=tk.LEFT, padx=5)

        # Advanced Jammer Field
        tk.Label(adv, text="Advanced Jam List (Saved in Master File)", font=("Arial", 10, "bold")).pack(anchor='w', pady=(10, 0))
        self.adv_jammer_text = tk.Text(adv, height=6, width=40, font=("Courier", 9))
        self.adv_jammer_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.bake_btn = tk.Button(adv, text="BAKE MASTER FILE", bg="orange", command=self.callbacks['bake'])
        self.bake_btn.pack(fill=tk.X, pady=10)

        # Status Label (The progress_cb target)
        self.status_msg = tk.StringVar(value="Ready")
        tk.Label(adv, textvariable=self.status_msg, fg="gray").pack()

        # Playback section
        tk.Label(adv, text="Playback Control", font=("Arial", 12, "bold")).pack(anchor='w')
        self.master_file = tk.StringVar(value="No Bake Loaded")
        tk.Label(adv, textvariable=self.master_file, fg="green").pack(anchor='w', pady=5)
        tk.Button(adv, text="Load Master Bake", command=self.pick_file).pack(fill=tk.X)

        # output assignment
        routing_frame = tk.LabelFrame(adv, text="Output Assignment (Channel #)", pady=10)
        routing_frame.pack(fill=tk.X, pady=10)

        # LTC Channel
        tk.Label(routing_frame, text="LTC Output:").grid(row=0, column=0)
        self.ltc_ch_entry = tk.Entry(routing_frame, width=3)
        self.ltc_ch_entry.insert(0, "1")
        self.ltc_ch_entry.grid(row=0, column=1, padx=5)

        # Music Channels
        tk.Label(routing_frame, text="Music L:").grid(row=0, column=2)
        self.music_l_entry = tk.Entry(routing_frame, width=3)
        self.music_l_entry.insert(0, "2")
        self.music_l_entry.grid(row=0, column=3, padx=5)

        tk.Label(routing_frame, text="Music R:").grid(row=0, column=4)
        self.music_r_entry = tk.Entry(routing_frame, width=3)
        self.music_r_entry.insert(0, "3")
        self.music_r_entry.grid(row=0, column=5, padx=5)

    def pick_folder(self):
        folder = filedialog.askdirectory()
        if folder: self.music_dir.set(folder)

    def pick_file(self):
        file = filedialog.askopenfilename(filetypes=[("WAV files", "*.wav")])
        if file: self.master_file.set(file)
    
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