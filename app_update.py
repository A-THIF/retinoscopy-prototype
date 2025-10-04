import io
import threading
import time
import os
import cv2
import numpy as np
import smbus
from flask import Flask, Response, request, send_file

from picamera2.picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# =======================
# Global Initializations
# =======================
app = Flask(__name__)

picamera2 = Picamera2()

# Low-res video config (for live streaming)
lowres_config = picamera2.create_video_configuration(
    main={"size": (640, 480)},
    lores={"size": (320, 240), "format": "YUV420"}
)

# High-res capture config
highres_config = picamera2.create_still_configuration(main={"size": (4056,3040)})

picamera2.configure(lowres_config)

# Streaming output
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

output = StreamingOutput()

# I2C bus for focus control
bus = smbus.SMBus(6)

# =======================
# State Variables
# =======================
autofocus_thread = None
autofocus_status = "idle"
current_focus_value = 512
camera_is_active = False
placeholder_frame = None

# =======================
# Helper Functions
# =======================
def create_placeholder_frame():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    text = "Camera Off"
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size = cv2.getTextSize(text, font, 2, 3)[0]
    text_x = (img.shape[1] - text_size[0]) // 2
    text_y = (img.shape[0] + text_size[1]) // 2
    cv2.putText(img, text, (text_x, text_y), font, 2, (255, 255, 255), 3)
    is_success, buffer = cv2.imencode(".jpg", img)
    return buffer.tobytes()

def set_focus(val):
    global current_focus_value
    value = (val << 4) & 0x3FF0
    data1 = (value >> 8) & 0x3F
    data2 = value & 0xF0
    try:
        bus.write_byte_data(0x0C, data1, data2)
        current_focus_value = val
    except Exception as e:
        print(f"Error setting focus: {e}")

def calculate_sharpness():
    img = picamera2.capture_array("lores")
    if img.ndim == 3:
        img_gray = img[:, :, 0]  # extract luminance
    else:
        img_gray = img  # already grayscale
    return cv2.Laplacian(img_gray, cv2.CV_64F).var()

def calculate_sharpness_roi(center_x, center_y, size=50):
    img = picamera2.capture_array("lores")
    if img.ndim == 3:
        img_gray = img[:, :, 0]  # luminance
    else:
        img_gray = img
    h, w = img_gray.shape

    x1 = max(0, center_x - size // 2)
    y1 = max(0, center_y - size // 2)
    x2 = min(w, center_x + size // 2)
    y2 = min(h, center_y + size // 2)

    roi = img_gray[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0
    return cv2.Laplacian(roi, cv2.CV_64F).var()


def run_autofocus_roi_thread(center_x, center_y):
    global autofocus_status, current_focus_value
    autofocus_status = "running"
    try:
        max_sharpness = 0.0
        best_focus = current_focus_value
        for f in range(200, 601, 20):
            set_focus(f)
            time.sleep(0.15)  # allow lens to settle
            sharpness = calculate_sharpness_roi(center_x, center_y)
            print(f"Focus {f}, Sharpness {sharpness:.2f}")
            if sharpness > max_sharpness:
                max_sharpness = sharpness
                best_focus = f
        set_focus(best_focus)
        print(f"ROI Autofocus done → best={best_focus}, sharpness={max_sharpness:.2f}")
        current_focus_value = best_focus
    finally:
        autofocus_status = "finished"


def run_autofocus_thread():
    global autofocus_status, current_focus_value
    autofocus_status = "running"
    try:
        max_sharpness = 0.0
        best_focus = current_focus_value
        for f in range(200, 601, 20):  # test this smaller range first
            set_focus(f)
            time.sleep(0.15)  # increased settle time for lens motor
            sharpness = calculate_sharpness()
            print(f"Focus {f}, Sharpness {sharpness:.2f}")
            if sharpness > max_sharpness:
                max_sharpness = sharpness
                best_focus = f
        set_focus(best_focus)
        print(f"Autofocus finished → best focus = {best_focus} with sharpness = {max_sharpness:.2f}")
    finally:
        autofocus_status = "finished"

def gen_frames():
    global camera_is_active
    while camera_is_active:
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# =======================
# Flask Routes
# =======================
@app.route('/')
def index():
    return """
<!doctype html>
<html>
<head>
<title>Raspberry Pi Camera Control</title>
<style>
body { font-family: sans-serif; text-align: center; background: #282c34; color: white; padding:20px;}
h1{color:#61dafb;} img{border:2px solid #61dafb;margin-top:20px;max-width:90%;height:auto;background:#000;}
.controls{margin:20px auto;padding:20px;max-width:640px;background:#3a4049;border-radius:8px;}
.slider-container{display:flex;align-items:center;justify-content:center;gap:15px;}
button{padding:10px 20px;font-size:16px;cursor:pointer;background:#61dafb;color:#282c34;border:none;border-radius:5px;margin-top:15px;font-weight:bold;}
button:hover:not(:disabled){background:#88eaff;}
button:disabled{background:#555;color:#999;cursor:not-allowed;}
#status{margin-top:10px;font-style:italic;color:#ccc;height:20px;}
</style>
</head>
<body>
<h1>Raspberry Pi Camera Control</h1>
<img id="stream" src="/placeholder.jpg" width="640" height="480">
<div class="controls">
<button id="start-camera-btn">Start Camera</button>
<button id="stop-camera-btn" disabled>Stop Camera</button>
<button id="capture-btn" disabled>Capture Image</button>
<div class="slider-container">
<label for="focus">Focus:</label>
<input type="range" id="focus" min="0" max="1023" step="1" disabled>
<span id="focus-value">...</span>
</div>
<button id="autofocus-btn" disabled>Run Autofocus</button>
<p id="status">Status: Initializing...</p>
</div>
<script>
const startBtn = document.getElementById('start-camera-btn');
const stopBtn = document.getElementById('stop-camera-btn');
const captureBtn = document.getElementById('capture-btn');
const autofocusBtn = document.getElementById('autofocus-btn');
const focusSlider = document.getElementById('focus');
const focusValue = document.getElementById('focus-value');
const statusP = document.getElementById('status');
const streamImg = document.getElementById('stream');

startBtn.onclick = async () => {
    statusP.textContent = 'Status: Starting camera...';
    startBtn.disabled = true;
    try {
        await fetch('/start_camera', { method: 'POST' });
        streamImg.src = '/video_feed?' + new Date().getTime();
        stopBtn.disabled = false;
        captureBtn.disabled = false;
        autofocusBtn.disabled = false;
        focusSlider.disabled = false;
        statusP.textContent = 'Status: Camera On';
    } catch (e) {
        console.error(e);
        statusP.textContent = 'Error starting camera.';
        startBtn.disabled = false;
    }
};

stopBtn.onclick = async () => {
    statusP.textContent = 'Status: Stopping camera...';
    stopBtn.disabled = true;
    captureBtn.disabled = true;
    autofocusBtn.disabled = true;
    focusSlider.disabled = true;
    try {
        await fetch('/stop_camera', { method: 'POST' });
        streamImg.src = '/placeholder.jpg';
        startBtn.disabled = false;
        statusP.textContent = 'Status: Camera Off';
    } catch (e) {
        console.error(e);
        statusP.textContent = 'Error stopping camera.';
        stopBtn.disabled = false;
    }
};

captureBtn.onclick = async () => {
    statusP.textContent = 'Capturing high-res image...';
    try {
        const response = await fetch('/capture_image', { method: 'POST' });
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'capture.jpg';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        statusP.textContent = 'Capture complete!';
    } catch (e) {
        console.error(e);
        statusP.textContent = 'Capture failed';
    }
};

focusSlider.oninput = () => focusValue.textContent = focusSlider.value;
focusSlider.onchange = () => fetch('/set_focus', { method: 'POST', body: new URLSearchParams({ 'focus': focusSlider.value }) });

autofocusBtn.onclick = async () => {
    await fetch('/autofocus', { method: 'POST' });
};

// Tap-to-Focus: handle click on preview image
streamImg.addEventListener('click', async (event) => {
    const rect = streamImg.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;

    // Scale click coordinates to camera low-res frame (320x240)
    const scaleX = 320 / streamImg.clientWidth;
    const scaleY = 240 / streamImg.clientHeight;
    const x = Math.floor(clickX * scaleX);
    const y = Math.floor(clickY * scaleY);

    // Optional: display a focus box overlay here (not implemented)

    try {
        const response = await fetch('/tap_focus', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: x, y: y })
        });
        const result = await response.json();
        if (result.status === "success") {
            console.log("ROI Autofocus started at", x, y);
            statusP.textContent = `Status: Autofocus started at (${x}, ${y})`;
        } else {
            console.error("Autofocus error:", result.message);
            statusP.textContent = `Status: Autofocus error: ${result.message}`;
        }
    } catch (e) {
        console.error("Network error:", e);
        statusP.textContent = 'Status: Network error during autofocus';
    }
});

// Periodically update focus slider based on backend status
setInterval(async () => {
    try {
        const data = await (await fetch('/get_status')).json();
        if (document.activeElement !== focusSlider) {
            focusSlider.value = data.focus_value;
            focusValue.textContent = data.focus_value;
        }
    } catch (e) {
        // silently ignore errors
    }
}, 1000);
</script>
</body>
</html>
"""

@app.route('/placeholder.jpg')
def placeholder():
    global placeholder_frame
    if placeholder_frame is None:
        placeholder_frame = create_placeholder_frame()
    return Response(placeholder_frame, mimetype='image/jpeg')

@app.route('/video_feed')
def video_feed():
    if not camera_is_active: return Response(status=204)
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start_camera', methods=['POST'])
def start_camera():
    global camera_is_active
    if not camera_is_active:
        picamera2.start_recording(JpegEncoder(), FileOutput(output))
        camera_is_active=True
        time.sleep(1)
    return {"status":"success"}

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    global camera_is_active
    if camera_is_active:
        picamera2.stop_recording()
        camera_is_active=False
    return {"status":"success"}

@app.route('/capture_image', methods=['POST'])
def capture_image():
    global camera_is_active
    temp_filename = f"capture_{int(time.time())}.jpg"
    try:
        # Stop low-res streaming
        if camera_is_active:
            picamera2.stop_recording()
            camera_is_active=False

        # High-res capture
        picamera2.configure(highres_config)
        picamera2.start()
        time.sleep(0.5)
        picamera2.capture_file(temp_filename)
        picamera2.stop()

        # Back to low-res streaming
        picamera2.configure(lowres_config)
        picamera2.start_recording(JpegEncoder(), FileOutput(output))
        camera_is_active=True

    except Exception as e:
        return {"status":"error","message":str(e)},500

    return send_file(temp_filename, mimetype='image/jpeg', as_attachment=True)

@app.route('/set_focus', methods=['POST'])
def handle_set_focus():
    val = int(request.form['focus'])
    if 0 <= val <= 1023:
        set_focus(val)
        return {"status":"success","value":val}
    return {"status":"error","message":"Invalid focus"},400

@app.route('/autofocus', methods=['POST'])
def handle_autofocus():
    global autofocus_thread
    if autofocus_thread is None or not autofocus_thread.is_alive():
        autofocus_thread=threading.Thread(target=run_autofocus_thread)
        autofocus_thread.start()
        return {"status":"success"}
    return {"status":"error","message":"Already running"},409

@app.route('/tap_focus', methods=['POST'])
def tap_focus():
    global autofocus_thread
    if autofocus_thread is None or not autofocus_thread.is_alive():
        data = request.json
        if not data or 'x' not in data or 'y' not in data:
            return {"status": "error", "message": "Missing coordinates"}, 400
        x = int(data['x'])
        y = int(data['y'])
        autofocus_thread = threading.Thread(target=run_autofocus_roi_thread, args=(x, y))
        autofocus_thread.start()
        return {"status": "success"}
    else:
        return {"status": "error", "message": "Autofocus already running"}, 409


@app.route('/get_status')
def get_status():
    global autofocus_status
    status_to_send = autofocus_status
    if autofocus_status=="finished": autofocus_status="idle"
    return {"autofocus_status":status_to_send,"focus_value":current_focus_value,"camera_is_active":camera_is_active}

# =======================
# Main
# =======================
if __name__=="__main__":
    try:
        set_focus(current_focus_value)
        app.run(host='0.0.0.0', port=5000, threaded=True)
    finally:
        if camera_is_active: picamera2.stop_recording()
