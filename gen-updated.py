from flask import Flask, Response, render_template_string, request, jsonify, send_file, url_for
import cv2
import os
import time
import atexit
from threading import Lock
from datetime import datetime

from picamera2 import Picamera2

APP_VERSION = "v1.0.8 - 2025-07-10"
app = Flask(__name__)

picam2 = None
camera_lock = Lock()
camcall = False

# ----------- Camera Initialization and Cleanup -----------
def initialize_camera():
    global picam2
    if picam2 is None:
        print("Initializing camera...")
        picam2 = Picamera2()
        video_config = picam2.create_video_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 24}
        )
        picam2.configure(video_config)
        picam2.set_controls({
            "Brightness": 0.1,
            "Saturation": 1.0,
            "Sharpness": 1.0
        })
        picam2.start()
        time.sleep(2)
        print("Camera initialized.")

def cleanup():
    global picam2
    if picam2 is not None:
        print("Cleaning up camera.")
        picam2.close()

atexit.register(cleanup)
@app.before_first_request
def before_first_request():
    initialize_camera()

# ----------- Video Streaming -----------
def generate_frames():
    global picam2
    while True:
        start = time.time()
        with camera_lock:
            if picam2:
                frame = picam2.capture_array()
                if frame is not None:
                    frame = cv2.flip(frame, 0)
                    ret, buffer = cv2.imencode('.jpg', frame)
                    frame = buffer.tobytes()
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        elapsed = time.time() - start
        time.sleep(max(0, 0.1 - elapsed))


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ----------- Web UI -----------
@app.route('/')
def index():
    html_template = """
<!doctype html>
<html lang="en">
<head>
  <title>Retinoscopy Camera Control</title>
  <script>
    // Debounce for focus
    let focusTimeout;
    function adjustFocus() {
      clearTimeout(focusTimeout);
      const v = document.getElementById('focusSlider').value;
      document.getElementById('focusInput').value = v;
      focusTimeout = setTimeout(() => sendFocus(v), 150);
    }
    function syncSliderAndInput() {
      document.getElementById('focusInput').value = document.getElementById('focusSlider').value;
    }
    function syncInputAndSlider() {
      document.getElementById('focusSlider').value = document.getElementById('focusInput').value;
    }
    function sendFocus(value) {
      fetch('/adjust_focus', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ focus: value })
      })
      .then(r => r.json()).then(data => console.log(data.message)).catch(console.error);
    }

    // Debounce for zoom
    let zoomTimeout;
    function adjustZoom() {
      clearTimeout(zoomTimeout);
      const v = document.getElementById('zoomSlider').value;
      document.getElementById('zoomInput').value = v;
      zoomTimeout = setTimeout(() => sendZoom(v), 150);
    }
    function syncZoomSliderAndInput() {
      document.getElementById('zoomInput').value = document.getElementById('zoomSlider').value;
    }
    function syncZoomInputAndSlider() {
      document.getElementById('zoomSlider').value = document.getElementById('zoomInput').value;
    }
    function sendZoom(value) {
      fetch('/adjust_zoom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zoom: value })
      })
      .then(r => r.json()).then(data => console.log(data.message)).catch(console.error);
    }

    function shutdown_rpi() {
      fetch('/shutdown_pi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      })
      .then(response => response.json())
      .then(data => {
        alert("Shutdown command sent. The Pi will power off.");
        console.log(data.message);
      })
      .catch(error => {
        alert("Shutdown command sent. The Pi may power off shortly.");
        console.error('Error:', error);
      });
    }

    function adjustInitialFocusZoom() {
      adjustFocus();
      adjustZoom();
    }

    function captureImage() {
      fetch('/capture_image', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
          if (data.message) {
            alert(data.message);
          } else if (data.error) {
            alert("Capture Error: " + data.error);
          } else {
            alert("Unknown error during capture.");
          }
          if (data.url) {
            const link = document.createElement('a');
            link.href = data.url;
            link.download = data.url.split('/').pop();
            link.click();
          }
        })
        .catch(error => alert("Error capturing image:\\n" + error.message));
    }
  </script>
</head>
<body onload="adjustInitialFocusZoom()">
  <h1>Retinoscopy Camera Control</h1>
  <p style="color:gray;">Running Version: {{ version }}</p>
  <img src="{{ url_for('video_feed') }}" width="100%">
  <div>
    <label for="focusSlider">Focus (Slider):</label>
    <input type="range" id="focusSlider" min="0" max="1500" value="723" step="1"
      oninput="syncSliderAndInput();adjustFocus();">
  </div>
  <div>
    <label for="focusInput">Focus (Input):</label>
    <input type="number" id="focusInput" min="0" max="1500" value="723" step="1"
      oninput="syncInputAndSlider();adjustFocus();">
  </div>
  <br>
  <div>
    <label for="zoomSlider">Zoom (Slider):</label>
    <input type="range" id="zoomSlider" min="1.0" max="4.0" value="1.9" step="0.1"
      oninput="syncZoomSliderAndInput();adjustZoom();">
  </div>
  <div>
    <label for="zoomInput">Zoom (Input):</label>
    <input type="number" id="zoomInput" min="1.0" max="4.0" value="1.9" step="0.1"
      oninput="syncZoomInputAndSlider();adjustZoom();">
  </div>
  <div>
    <input type="button" value="Shutdown" onclick="shutdown_rpi()" />
  </div>
  <br>
  <div>
    <input type="button" value="📸 Capture High-Res Image" onclick="captureImage()" />
  </div>
</body>
</html>
"""
    return render_template_string(html_template, version=APP_VERSION)

# ----------- Focus and Zoom Controls -----------
def set_focus(lens_position):
    global picam2
    if picam2:
        picam2.set_controls({"AfMode": 0, "LensPosition": lens_position})
        print(f"Focus set to {lens_position}")
        return True
    return False

@app.route('/adjust_focus', methods=['POST'])
def adjust_focus():
    global camcall
    data = request.get_json()
    focus_value = float(data.get("focus", 10))
    if camcall:
        print("Focus adjustment already in progress.")
        return jsonify({"error": "Focus adjustment in progress"}), 429
    try:
        camcall = True
        if picam2:
            set_focus_result = set_focus(focus_value / 100)
            camcall = False
            if set_focus_result:
                return jsonify({"message": f"Focus adjusted to {focus_value / 100}"}), 200
            else:
                return jsonify({"error": "Failed to adjust focus"}), 500
        else:
            camcall = False
            return jsonify({"error": "Camera not initialized"}), 500
    except Exception as e:
        camcall = False
        print(f"Error adjusting focus: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/adjust_zoom', methods=['POST'])
def adjust_zoom():
    data = request.get_json()
    zoom_value = float(data.get("zoom", 1))
    if not picam2:
        return jsonify({"error": "Camera not initialized"}), 500
    try:
        main_size = picam2.sensor_resolution
        width, height = main_size[0], main_size[1]
        crop_width = int(width / zoom_value)
        crop_height = int(height / zoom_value)
        x_offset = (width - crop_width) // 2
        y_offset = (height - crop_height) // 2
        crop_box = (x_offset, y_offset, crop_width, crop_height)
        picam2.set_controls({"ScalerCrop": crop_box})
        print(f"Zoom set to {zoom_value}x")
        return jsonify({"message": f"Zoom adjusted to {zoom_value}x"}), 200
    except Exception as e:
        print(f"Error adjusting zoom: {e}")
        return jsonify({"error": str(e)}), 500

# ----------- High-Resolution Capture -----------
@app.route('/capture_image', methods=['POST'])
def capture_image():
    global picam2
    try:
        print("Switching to high-res for image capture...")
        with camera_lock:
            picam2.stop()
            high_res_config = picam2.create_still_configuration(
                main={"size": (6944, 5200), "format": "RGB888"}
            )
            picam2.configure(high_res_config)
            picam2.start()
            time.sleep(1.5)
            frame = picam2.capture_array()
            h, w = frame.shape[:2]
            crop_h, crop_w = int(h * 0.5), int(w * 0.5)
            start_y = (h - crop_h) // 2
            start_x = (w - crop_w) // 2
            cropped = frame[start_y:start_y+crop_h, start_x:start_x+crop_w]
            save_folder = "static/captures"
            os.makedirs(save_folder, exist_ok=True)
            filename = f"captured_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            filepath = os.path.join(save_folder, filename)
            # Save cropped or full frame as needed
            cv2.imwrite(filepath, cropped)
            print(f"Image saved: {filepath}")
            # Reconfigure back to video
            picam2.stop()
            video_config = picam2.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"},
                controls={"FrameRate": 24}
            )
            picam2.configure(video_config)
            picam2.start()
        return jsonify({
            "message": "Image captured successfully ✅",
            "url": f"/static/captures/{filename}"
        }), 200
    except Exception as e:
        print(f"Error capturing image: {e}")
        return jsonify({"error": str(e)}), 500

# ----------- Shutdown Raspberry Pi -----------
@app.route('/shutdown_pi', methods=['POST'])
def shutdown_pi():
    try:
        print("Shutting down the Raspberry Pi...")
        os.system("sudo shutdown now")
        return jsonify({"message": "Shutdown command issued"}), 200
    except Exception as e:
        print(f"Error shutting down: {e}")
        return jsonify({"error": str(e)}), 500

# ----------- Version Endpoint -----------
@app.route('/version')
def get_version():
    return jsonify({"version": APP_VERSION})

# ----------- Main Entrypoint -----------
if __name__ == '__main__':
    os.makedirs("static/captures", exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)