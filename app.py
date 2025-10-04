import io
import threading
import time
 
import cv2
import numpy as np
import smbus
from flask import Flask, Response, render_template, request
 
# Import the new Picam2 library
from picamera2.picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
 
# --- Global Initializations ---
 
# Initialize Flask app
app = Flask(__name__)
 
# --- Picam2 Setup ---
# Initialize camera
picamera2 = Picamera2()
# Configure for video streaming and add a low-resolution stream for sharpness analysis
video_config = picamera2.create_video_configuration(
    main={"size": (640, 480)},
    lores={"size": (320, 240), "format": "YUV420"}
)
picamera2.configure(video_config)
 
# Custom output class for streaming to multiple clients
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()
 
    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
 
output = StreamingOutput()
 
# Initialize I2C bus
# Your new script correctly identifies bus 6. For other Pi models it might be 0, 1, or 7.
bus = smbus.SMBus(6)
 
# --- State Variables ---
autofocus_thread = None
autofocus_status = "idle"  # Can be "idle", "running", "finished"
current_focus_value = 512  # Start with a central value
camera_is_active = False  # New state for camera on/off
placeholder_frame = None  # To store the "Camera Off" image


# --- Helper function to create a placeholder image ---
def create_placeholder_frame():
    """Creates a black image with 'Camera Off' text."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    text = "Camera Off"
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size = cv2.getTextSize(text, font, 2, 3)[0]
    text_x = (img.shape[1] - text_size[0]) // 2
    text_y = (img.shape[0] + text_size[1]) // 2
    cv2.putText(img, text, (text_x, text_y), font, 2, (255, 255, 255), 3)
    is_success, buffer = cv2.imencode(".jpg", img)
    return buffer.tobytes()


# --- Arducam Focus and Sharpness Logic (Adapted from your script) ---
 
def set_focus(val):
    """Sets the focus of the camera lens using I2C."""
    global current_focus_value
    # The Arducam motor controller takes a 10-bit value (0-1023).
    # This value is sent as two bytes over I2C.
    value = (val << 4) & 0x3FF0
    data1 = (value >> 8) & 0x3F
    data2 = value & 0xF0
    try:
        bus.write_byte_data(0x0C, data1, data2)
        current_focus_value = val  # Store the last set value
    except Exception as e:
        print(f"Error setting focus: {e}")
 
def calculate_sharpness():
    """Captures an image and calculates its sharpness (Laplacian variance)."""
    # Capture from the low-resolution 'lores' stream for speed.
    # The 'lores' stream is in YUV format, so the first channel is the grayscale 'Y' channel.
    # This avoids a color conversion and is very fast.
    yuv_image = picamera2.capture_array("lores")
    img_gray = yuv_image[:, :, 0]
    # Calculate the variance of the Laplacian
    return cv2.Laplacian(img_gray, cv2.CV_64F).var()
 
def run_autofocus_thread():
    """The autofocus routine that runs in a background thread."""
    global autofocus_status, current_focus_value
    autofocus_status = "running"
    print("Starting autofocus...")
    try:
        max_sharpness = 0.0
        best_focus = current_focus_value
         
        # Scan through a range of focus values
        for f in range(50, 1001, 25):
            set_focus(f)
            time.sleep(0.05)  # Let the lens settle
            
            sharpness = calculate_sharpness()
            print(f"Testing focus {f}, sharpness: {sharpness:.2f}")

            if sharpness > max_sharpness:
                max_sharpness = sharpness
                best_focus = f

        print(f"Autofocus finished. Best focus: {best_focus} (Sharpness: {max_sharpness:.2f})")
        set_focus(best_focus)
    except Exception as e:
        print(f"!!! AUTOFOCUS ERROR: {e}")
    finally:
        # This is crucial to ensure the UI is always unlocked
        autofocus_status = "finished"
        print("Autofocus routine complete.")
 
 
# --- Flask Web Application Routes ---
 
@app.route('/')
def index():
    """Renders the main web page."""
    return render_template('index.html')
 
@app.route('/placeholder.jpg')
def placeholder():
    """Serves the placeholder image."""
    global placeholder_frame
    if placeholder_frame is None:
        placeholder_frame = create_placeholder_frame()
    return Response(placeholder_frame, mimetype='image/jpeg')

def gen_frames():
    """Video streaming generator function."""
    global camera_is_active
    while camera_is_active:  # Loop only while camera is supposed to be active
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
 
@app.route('/video_feed')
def video_feed():
    """Video streaming route."""
    if not camera_is_active:
        return Response(status=204)  # No Content, browser will do nothing
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/set_focus', methods=['POST'])
def handle_set_focus():
    """Route to set focus manually from the slider."""
    focus_val = int(request.form['focus'])
    if 0 <= focus_val <= 1023:
        set_focus(focus_val)
        return {"status": "success", "value": focus_val}
    return {"status": "error", "message": "Invalid focus value"}, 400
 
@app.route('/start_camera', methods=['POST'])
def start_camera():
    """Starts the camera recording."""
    global camera_is_active
    if not camera_is_active:
        print("Starting camera...")
        picamera2.start_recording(JpegEncoder(), FileOutput(output))
        camera_is_active = True
        # Give camera time to start and produce a frame
        time.sleep(1)
    return {"status": "success", "message": "Camera started."}

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    """Stops the camera recording."""
    global camera_is_active
    if camera_is_active:
        print("Stopping camera...")
        picamera2.stop_recording()
        camera_is_active = False
    return {"status": "success", "message": "Camera stopped."}

@app.route('/autofocus', methods=['POST'])
def handle_autofocus():
    """Route to trigger the autofocus routine."""
    global autofocus_thread
    if autofocus_thread is None or not autofocus_thread.is_alive():
        autofocus_thread = threading.Thread(target=run_autofocus_thread)
        autofocus_thread.start()
        return {"status": "success", "message": "Autofocus started."}
    return {"status": "error", "message": "Autofocus is already running."}, 409
 
@app.route('/get_status')
def get_status():
    """Route to get the current status of autofocus and focus value."""
    global autofocus_status
    # If the thread finished, reset status to idle for the next run
    status_to_send = autofocus_status
    if autofocus_status == "finished":
        autofocus_status = "idle"
    
    return {
        "autofocus_status": status_to_send,
        "focus_value": current_focus_value,
        "camera_is_active": camera_is_active  # Add camera status to the response
    }
 
 
if __name__ == '__main__':
    try:
        # Set the initial focus
        set_focus(current_focus_value)
        # Run the Flask app
        # threaded=True is essential for handling background tasks and streaming
        app.run(host='0.0.0.0', port=5000, threaded=True)
    finally:
        # Ensure camera is stopped on exit if it was running
        if camera_is_active:
            print("Stopping camera on exit...")
            picamera2.stop_recording()