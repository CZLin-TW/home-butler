"""Local masked input; no file, environment, clipboard reads or network access."""


def prompt_key():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("HomeButler — Claude 測試金鑰")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    width, height = 560, 240
    root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 2}")
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="請貼上 ai-agent-testkey 的 Key 值", font=("Microsoft JhengHei UI", 12, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Key 僅供這次程序使用，不存檔、不寫入環境變數。\n開始後最多 120 次 Claude API 呼叫，只解析指令，不控制家電。",
              wraplength=510).pack(anchor="w", pady=(10, 12))
    entry = ttk.Entry(frame, show="*", width=66)
    entry.pack(fill="x")
    status = ttk.Label(frame, text="")
    status.pack(anchor="w", pady=5)
    result = []

    def close():
        entry.delete(0, "end")
        root.destroy()

    def submit(event=None):
        value = entry.get().strip()
        if not value or not value.isascii() or any(char.isspace() for char in value):
            status.config(text="請貼上完整 Key，不能含空白或換行。")
            return
        result.append(value)
        close()

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e")
    ttk.Button(buttons, text="取消", command=close).pack(side="left", padx=8)
    ttk.Button(buttons, text="開始測試", command=submit).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Escape>", lambda event: close())
    root.bind("<Return>", submit)
    root.after(150, entry.focus_force)
    root.mainloop()
    return result.pop() if result else None
