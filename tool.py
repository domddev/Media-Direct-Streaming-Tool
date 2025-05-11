import os
import shutil
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta
import customtkinter as ctk
from tkinter import filedialog

SETTINGS_FILE = "settings.json"
HISTORY_FOLDER = "history"
FFMPEG_PATH = os.path.join(os.getcwd(), ".venv", "ffmpeg", "bin", "ffmpeg.exe")  # Adjust path as needed

os.makedirs(HISTORY_FOLDER, exist_ok=True)

# Globals
ffmpeg_process = None
is_streaming = False

def get_video_duration(file_path):
    try:
        result = subprocess.run([
            FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return float(result.stdout)
    except Exception as e:
        print(f"Failed to get video duration: {e}")
        return 180  # fallback: 3 minutes

def image_to_video(image_path, output_path, duration=5):
    try:
        subprocess.run([
            FFMPEG_PATH,
            "-loop", "1",
            "-i", image_path,
            "-c:v", "libx264",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1280:720",  # Resize if needed
            "-y",  # Overwrite output
            output_path
        ], check=True)
        return output_path
    except Exception as e:
        print(f"Error creating video from image: {e}")
        return None


def load_settings():
    if os.path.exists(SETTINGS_FILE) and os.path.getsize(SETTINGS_FILE) > 0:
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("Warning: settings.json is corrupted. Using defaults.")
    return {}

def save_settings():
    settings = {
        'stream_key': stream_key_entry.get(),
        'countdown_enabled': countdown_var.get(),
        'waiting_image_enabled': waiting_image_var.get(),
        'video_file': file_path.get(),
        'countdown_file': countdown_file.get(),
        'waiting_image_file': waiting_image_file.get(),
        'stream_start_time': stream_time_entry.get()  # Save the stream start time
    }
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def handle_file_upload(var, label, file_type):
    file = filedialog.askopenfilename(title=f"Select {file_type}")
    if not file:
        return
    filename = os.path.basename(file)
    target = os.path.join(os.getcwd(), filename)

    prev_path = var.get()
    if os.path.exists(prev_path) and os.path.abspath(prev_path) != os.path.abspath(target):
        shutil.move(prev_path, os.path.join(HISTORY_FOLDER, os.path.basename(prev_path)))

    shutil.copy(file, target)
    var.set(target)
    label.configure(text=filename)
    save_settings()

def select_video():
    file = filedialog.askopenfilename(filetypes=[("MP4 Files", "*.mp4")])
    if file:
        file_path.set(file)
        video_label.configure(text=os.path.basename(file))
        save_settings()

def build_ffmpeg_cmd(input_file, stream_key, loop=False):
    cmd = [FFMPEG_PATH]  # use full path here
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += [
        "-re", "-i", input_file,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-maxrate", "6000k",
        "-bufsize", "6000k",
        "-pix_fmt", "yuv420p",
        "-g", "120",
        "-c:a", "aac",
        "-b:a", "160k",
        "-f", "flv",
        f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    ]
    return cmd

def wait_for_start_time(start_time):
    current_time = datetime.now()
    wait_time = start_time - current_time
    if wait_time > timedelta(0):  # if start_time is in the future
        print(f"Waiting for {wait_time} to start stream.")
        time.sleep(wait_time.total_seconds())

def run_ffmpeg_sequence():
    global ffmpeg_process, is_streaming

    OUTPUT_FOLDER = os.path.join(os.getcwd(), "output")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    save_settings()
    stream_key = stream_key_entry.get()
    video = file_path.get()
    countdown = countdown_file.get()
    waiting_image = waiting_image_file.get()
    stream_start_time = stream_time_entry.get()

    if not stream_key or not video:
        print("Missing stream key or video.")
        return

    def move_to_output(file_path):
        if file_path and os.path.exists(file_path):
            base_name = os.path.basename(file_path)
            target_path = os.path.join(OUTPUT_FOLDER, base_name)
            if os.path.abspath(file_path) != os.path.abspath(target_path):
                shutil.copy(file_path, target_path)
                print(f"Moved {base_name} to output folder.")
            return target_path
        return None

    try:
        # Ensure all inputs are inside the output folder
        video = move_to_output(video)
        countdown = move_to_output(countdown)
        waiting_image = move_to_output(waiting_image)

        # Convert stream start time
        stream_start_time_obj = datetime.strptime(stream_start_time, "%H:%M")
        now = datetime.now()
        stream_start_datetime = now.replace(hour=stream_start_time_obj.hour, minute=stream_start_time_obj.minute, second=0, microsecond=0)
        if stream_start_datetime <= now:
            stream_start_datetime += timedelta(days=1)

        # Get countdown duration
        countdown_duration = 0
        if countdown_var.get() and countdown:
            result = subprocess.run(
                [FFMPEG_PATH, "-i", countdown, "-hide_banner"],
                stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            for line in result.stderr.splitlines():
                if "Duration" in line:
                    time_str = line.split("Duration:")[1].split(",")[0].strip()
                    h, m, s = map(float, time_str.split(":"))
                    countdown_duration = int(h * 3600 + m * 60 + s)
                    break

        countdown_start_time = stream_start_datetime - timedelta(seconds=countdown_duration)

        # Convert image to waiting video (5s)
        waiting_video = None
        if waiting_image_var.get() and waiting_image:
            waiting_video = os.path.join(OUTPUT_FOLDER, "waiting_loop.mp4")
            print("Creating short video from waiting image...")
            subprocess.run([
            FFMPEG_PATH,
                "-loop", "1",
                "-i", waiting_image,
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-shortest",
                "-t", "5",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=1280:720",
                "-y",
                waiting_video
            ], check=True)



        # Start streaming waiting video loop
        if waiting_video and os.path.exists(waiting_video):
            print("Streaming waiting video loop...")
            waiting_cmd = build_ffmpeg_cmd(waiting_video, stream_key, loop=True)
            ffmpeg_process = subprocess.Popen(waiting_cmd)

        # Wait for countdown time
        wait_seconds = (countdown_start_time - datetime.now()).total_seconds()
        if wait_seconds > 0:
            print(f"Waiting {wait_seconds:.1f} seconds before switching to countdown...")
            time.sleep(wait_seconds)

        # Stop waiting video stream
        if ffmpeg_process:
            ffmpeg_process.terminate()
            ffmpeg_process.wait()
            ffmpeg_process = None

        # Stream countdown
        if countdown_var.get() and countdown:
            print("Streaming countdown...")
            countdown_cmd = build_ffmpeg_cmd(countdown, stream_key)
            ffmpeg_process = subprocess.Popen(countdown_cmd)
            ffmpeg_process.wait()

        # Stream main video
        print("Streaming main video...")
        main_cmd = build_ffmpeg_cmd(video, stream_key)
        ffmpeg_process = subprocess.Popen(main_cmd)
        ffmpeg_process.wait()

    except Exception as e:
        print(f"Streaming error: {e}")
    finally:
        is_streaming = False
        stream_button.configure(text="Start Stream")

def toggle_stream():
    global is_streaming, ffmpeg_process

    if not is_streaming:
        is_streaming = True
        stream_button.configure(text="Stop Stream")
        threading.Thread(target=run_ffmpeg_sequence, daemon=True).start()
    else:
        if ffmpeg_process:
            ffmpeg_process.terminate()
        is_streaming = False
        stream_button.configure(text="Start Stream")

# GUI Setup
ctk.set_appearance_mode("dark")
app = ctk.CTk()
app.title("LIMDST - Limitless Interactives Media Direct Streaming Tool")
app.geometry("600x500")

settings = load_settings()

file_path = ctk.StringVar(value=settings.get("video_file", ""))
countdown_file = ctk.StringVar(value=settings.get("countdown_file", ""))
waiting_image_file = ctk.StringVar(value=settings.get("waiting_image_file", ""))
stream_time_var = ctk.StringVar(value=settings.get("stream_start_time", "12:00"))

countdown_var = ctk.BooleanVar(value=settings.get("countdown_enabled", False))
waiting_image_var = ctk.BooleanVar(value=settings.get("waiting_image_enabled", False))

# Widgets
ctk.CTkLabel(app, text="Stream Key").pack()
stream_key_entry = ctk.CTkEntry(app, width=400)
stream_key_entry.insert(0, settings.get("stream_key", ""))
stream_key_entry.pack(pady=10)

ctk.CTkButton(app, text="Select MP4 File", command=select_video).pack()
video_label = ctk.CTkLabel(app, text=os.path.basename(file_path.get()))
video_label.pack()

# Countdown Upload
countdown_frame = ctk.CTkFrame(app)
countdown_frame.pack(pady=5, fill="x", padx=20)
ctk.CTkCheckBox(countdown_frame, text="Enable Countdown", variable=countdown_var).pack(side="left")
countdown_upload_label = ctk.CTkLabel(countdown_frame, text=os.path.basename(countdown_file.get()) or "No file")
countdown_upload_label.pack(side="left", padx=10)
ctk.CTkButton(countdown_frame, text="Upload", command=lambda: handle_file_upload(countdown_file, countdown_upload_label, "countdown video")).pack(side="right")

# Waiting Image Upload
waiting_frame = ctk.CTkFrame(app)
waiting_frame.pack(pady=5, fill="x", padx=20)
ctk.CTkCheckBox(waiting_frame, text="Enable Waiting Image", variable=waiting_image_var).pack(side="left")
waiting_upload_label = ctk.CTkLabel(waiting_frame, text=os.path.basename(waiting_image_file.get()) or "No file")
waiting_upload_label.pack(side="left", padx=10)
ctk.CTkButton(waiting_frame, text="Upload", command=lambda: handle_file_upload(waiting_image_file, waiting_upload_label, "waiting image")).pack(side="right")

# Stream Start Time
ctk.CTkLabel(app, text="Stream Start Time (HH:MM)").pack(pady=5)
stream_time_entry = ctk.CTkEntry(app, width=100, textvariable=stream_time_var)
stream_time_entry.pack(pady=5)

# Stream Button
stream_button = ctk.CTkButton(app, text="Start Stream", command=toggle_stream)
stream_button.pack(pady=20)

app.mainloop()
