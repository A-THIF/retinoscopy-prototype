from flask import Flask, Response, render_template_string, request, jsonify
import cv2

import time
import atexit
#from picamera2 import Picamera2, Controls

from picamera2 import Picamera2
from libcamera import ControlId


app = Flask(__name__)

# Initialize the camera globally, only once
picam2 = None
camcall = False


import os



def initialize_camera():
    global picam2
    if picam2 is None:
        picam2 = Picamera2()

        # Specify a valid configuration for video (e.g., set resolution and pixel format)
        #video_config = picam2.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"})
       
          # Set a lower resolution and FPS to reduce CPU/GPU load
        video_config = picam2.create_video_configuration(
            main={"size": (640, 480), "format": "RGB888"},  # Lower resolution
            controls={"FrameRate": 24}  # Reduce frame rate to 10 FPS
        )

        # Configure the camera for video streaming
        picam2.configure(video_config)

        #from picamera2 import controls
       # print(dir(controls.Controls))  # Check available controls in the Controls 

        available_controls = picam2.controls.get_libcamera_controls()
        print(available_controls)
    
        #picam2.set_controls({"ExposureTime": 10000})  # Exposure time in microseconds
        picam2.set_controls({"Brightness": 0.1})  # 0.0 to 1.0
        #picam2.set_controls({"Contrast": 0.9})  # Default, 0.0 is lower contrast, 1.0 is standard
        picam2.set_controls({"Saturation": 1.0})  # 0.0 to 1.0 (0 is grayscale, 1 is normal)
        picam2.set_controls({"Sharpness": 1.0})  # Default value
        #picam2.set_controls({"Iso": 400})  # ISO 400, for example
        #picam2.set_controls({"Gamma": 1.0})  # Standard gamma

        # Start camera streaming
        picam2.start()

        # Initial sleep to allow camera settings to stabilize
        time.sleep(2)

def cleanup():
    """Ensure the camera is properly closed when the application exits."""
    global picam2
    if picam2 is not None:
        picam2.close()

atexit.register(cleanup)  # Register cleanup to run when app shuts down

@app.before_first_request
def before_first_request():
    """Initialize the camera before the first request."""
    initialize_camera()

def generate_frames():
    while True:
      start = time.time()
        # Capture a frame from the camera
        frame = picam2.capture_array()

        if frame is None:
            continue
        frame = cv2.flip(frame, 0)  # 0 means flipping around the x-axis (vertical flip)

        # Convert frame to JPEG
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()

        # Serve the frame in the MJPEG format
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
        elapsed = time.time() - start
        sleep_time = max(0,0.1 - elapsed) #10 fps
        time.sleep(sleep_time)

@app.route('/')
def index():
    # HTML template with slider and numeric input for focus adjustment
    html_template = """
<!doctype html>
<html lang="en">
  <head>
    <title>Camera Stream with Focus Adjustment</title>
    <script>
      function adjustFocus() {
        const sliderValue = document.getElementById('focusSlider').value;
        const inputValue = document.getElementById('focusInput').value;

        // Ensure the input and slider values are synchronized
        if (sliderValue !== inputValue) {
          document.getElementById('focusInput').value = sliderValue;
        }

        sendFocus(sliderValue);
      }

      function syncSliderAndInput() {
        const sliderValue = document.getElementById('focusSlider').value;
        document.getElementById('focusInput').value = sliderValue;
      }

      function syncInputAndSlider() {
        const inputValue = document.getElementById('focusInput').value;
        document.getElementById('focusSlider').value = inputValue;
      }

      function sendFocus(value) {
        fetch('/adjust_focus', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ focus: value })
        })
        .then(response => response.json())
        .then(data => console.log(data.message))
        .catch(error => console.error('Error:', error));
      }

      function adjustZoom() {
        const sliderValue = document.getElementById('zoomSlider').value;
        const inputValue = document.getElementById('zoomInput').value;

        // Ensure the input and slider values are synchronized
        if (sliderValue !== inputValue) {
          document.getElementById('zoomInput').value = sliderValue;
        }

        sendZoom(sliderValue);
      }

      function syncZoomSliderAndInput() {
        const sliderValue = document.getElementById('zoomSlider').value;
        document.getElementById('zoomInput').value = sliderValue;
      }

      function syncZoomInputAndSlider() {
        const inputValue = document.getElementById('zoomInput').value;
        document.getElementById('zoomSlider').value = inputValue;
      }

      function sendZoom(value) {
        fetch('/adjust_zoom', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ zoom: value })
        })
        .then(response => response.json())
        .then(data => console.log(data.message))
        .catch(error => console.error('Error:', error));
      }

      function shutdown_rpi(){
        fetch('/shutdown_pi', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ zoom: 0 })
        })
        .then(response => response.json())
        .then(data => console.log(data.message))
        .catch(error => console.error('Error:', error));
      }

      function adjustInitialFocusZoom(){
        adjustFocus();
        adjustZoom();
      }

      function captureImage() {
    fetch('/capture_image', {
      method: 'POST'
    })
    .then(response => {
      if (!response.ok) {
        return response.text().then(text => { throw new Error(text); });
      }
      return response.json();
    })
    .then(data => {
      alert(data.message);

      if (data.url) {
        const link = document.createElement('a');
        link.href = data.url;
        link.download = data.url.split('/').pop(); // get filename only
        link.click();  // 🔻 Trigger browser download
      }
    })
    .catch(error => alert("⚠️ Error capturing image:\n" + error.message));
  }

    </script>
  </head>
  <body onload="adjustInitialFocusZoom()">
    <h1>Camera Stream with Focus Adjustment</h1>
    <img src="{{ url_for('video_feed') }}" width="100%">
    <div>
      <label for="focusSlider">Focus (Slider):</label>
      <input
        type="range"
        id="focusSlider"
        min="0"
        max="1500"
        value="723"
        step="1"
        oninput="syncSliderAndInput();adjustFocus();"
      >
    </div>
    <div>
      <label for="focusInput">Focus (Input):</label>
      <input
        type="number"
        id="focusInput"
        min="0"
        max="1500"
        value="723"
        step="1"
        oninput="syncInputAndSlider();adjustFocus();"
      >
    </div>

    <br><br>

    <div>
      <label for="zoomSlider">Zoom (Slider):</label>
      <input
        type="range"
        id="zoomSlider"
        min="1.0"
        max="4.0"
        value="1.9"
        step="0.1"
        oninput="syncZoomSliderAndInput();adjustZoom();"
      >
    </div>
    <div>
      <label for="zoomInput">Zoom (Input):</label>
      <input
        type="number"
        id="zoomInput"
        min="1.0"
        max="4.0"
        value="1.9"
        step="0.1"
        oninput="syncZoomInputAndSlider();adjustZoom();"
      >
    </div>

    <div>
      <input type="button" value="Shutdown" onClick="shutdown_rpi()" />
    </div>

    <br><br>

    <div>
      <input type="button" value="📸 Capture High-Res Image" onclick="captureImage()" />
    </div>
  </body>
</html>
"""

    return render_template_string(html_template)

@app.route('/video_feed')
def video_feed():
    # Video streaming route
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


import subprocess

'''

def set_focus(lens_position):
 
    try:
        # Ensure the lens position is within the valid range (e.g., 0-10 or as per your camera specs)
        if not (0 <= lens_position <= 10):
            raise ValueError("Lens position must be between 0 and 10.")

        # Construct the libcamera-vid command for focus adjustment
        command = [
            "libcamera-vid",
            "-t", "0",  # No time limit for operation
            "--lens-position", str(lens_position),
            "-o", "/dev/null"  # No output file, just adjust focus
        ]

        # Execute the command
        subprocess.run(command, check=True)
        print(f"Focus adjusted to lens position: {lens_position}")
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
    except ValueError as ve:
        print(f"Invalid input: {ve}")




'''


from threading import Lock
import time
import subprocess
from flask import Flask, request, jsonify



camera_lock = Lock()
camcall = False
picam2 = None  # Assuming this is initialized elsewhere in your code.

def safely_access_camera():
    with camera_lock:
        try:
            if picam2:
                picam2.stop()
                print("Picamera2 stopped successfully.")
        except Exception as e:
            print(f"Error stopping Picamera2: {e}")
        time.sleep(2)  # Ensure full release of camera



def run_libcamera_vid():
    # Start the libcamera-vid process
    process = subprocess.Popen(['/usr/bin/libcamera-vid', '-t', '500', '--lens-position', '6.0', '--nopreview', '-o', '/dev/null'])
    
    # Wait for the process to complete
    process.communicate()

    if process.returncode != 0:
        print(f"libcamera-vid failed with exit code {process.returncode}")



def kill_libcamera_processes():
    # Kill all libcamera and picamera processes
    subprocess.call("sudo killall -9 libcamera", shell=True)
    subprocess.call("sudo killall -9 picamera2", shell=True)
    subprocess.call("sudo killall -9 libcamera-vid", shell=True)
    subprocess.call("sudo killall -9 libcamera-still", shell=True)


def set_focus(lens_position):
    # Set the lens position for manual focus (0 to 15 is the typical range for manual adjustment)
    picam2.set_controls({"AfMode": 0, "LensPosition": lens_position})  # Close focus
    print("setting focus for",lens_position)
    #time.sleep(5)
    #picam2.set_controls({"AfMode": 0, "LensPosition": 6})  # Further focus
    return True








def set_focus_old(lens_position):
    global camcall
    if camcall:
        print("Focus adjustment already in progress.")
        return False

    camcall = True
    safely_access_camera()

    try:
        # Validate lens position
        if not (0 <= lens_position <= 10):
            raise ValueError("Lens position must be between 0 and 10.")

        # Kill any lingering libcamera-vid processes
        # Kill any lingering camera-related processes
        kill_libcamera_processes()
        print("Killed existing camera processes.")

        # Ensure the camera subsystem has time to release resources
        time.sleep(3)  # Increased delay to ensure release of resources
        print("killed libcamera existing run")

        
        run_libcamera_vid()

        '''
        # Run libcamera-vid command with --nopreview
        command = [
            "/usr/bin/libcamera-vid",  # Replace with the actual path
            "-t", "500",  # Duration in milliseconds
            "--lens-position", str(lens_position),
            "--nopreview",    # Disable the preview window
            "-o", "/dev/null"  # Output discarded
        ]
    
        print(f"Running command: {' '.join(command)}")
    
        # Run the command and capture both stdout and stderr
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        # Check if the command was successful
        if result.returncode != 0:
            # If there was an error, print stderr and the output from the command
            print(f"Error executing libcamera-vid: {result.stderr}")
            print(f"Command output: {result.stdout}")
            return False
        
        # If the command was successful, print the output
        '''
        print(f"Focus adjusted to lens position: {lens_position}")
        #print(f"Command output: {result.stdout}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
        print(f"stderr: {e.stderr}")
        return False
    
    finally:
        # Optional restart of Picamera2 (depending on use case)
        if picam2:
            picam2.start()
            print("Picamera2 restarted.")
        
        # Reset the camcall flag
        camcall = False

    return True





@app.route('/adjust_zoom', methods=['POST'])
def adjust_zoom():
    try:
        data = request.get_json()
        zoom_value = float(data.get("zoom", 1))  # Default zoom to 1 if none provided

        if not picam2:
            return jsonify({"error": "Camera not initialized"}), 500

        # Define the zoom as a fractional value (e.g., 1.0 for no zoom, 2.0 for 2x zoom)
        zoom_factor = zoom_value

        # Get the camera's sensor resolution
        main_size = picam2.sensor_resolution
        width, height = main_size[0], main_size[1]

        # Calculate the crop box
        crop_width = int(width / zoom_factor)
        crop_height = int(height / zoom_factor)
        x_offset = (width - crop_width) // 2
        y_offset = (height - crop_height) // 2

        crop_box = (x_offset, y_offset, crop_width, crop_height)

        # Update the camera configuration to apply the crop
        picam2.set_controls({"ScalerCrop": crop_box})

        return jsonify({"message": f"Zoom adjusted to {zoom_value}x"}), 200
    except Exception as e:
        print(f"Error adjusting zoom: {e}")
        return jsonify({"error": str(e)}), 500






@app.route('/adjust_focus', methods=['POST'])
def adjust_focus():
    global camcall

    data = request.get_json()
    focus_value = float(data.get("focus", 10))  # Default focus to 10 if none provided

    if camcall:
            print("Focus adjustment already in progress.")
            return jsonify({"error": "Focus adjustment in progress"}), 429

    try:
        if picam2:
            #safely_access_camera()
            set_focus_result = set_focus(focus_value / 100)
            if set_focus_result:
                return jsonify({"message": f"Focus adjusted to {focus_value / 100}"}), 200
            else:
                return jsonify({"error": "Failed to adjust focus"}), 500
        else:
            return jsonify({"error": "Camera not initialized"}), 500
    except Exception as e:
            print(f"Error adjusting focus: {e}")
            return jsonify({"error": str(e)}), 500






@app.route('/shutdown_pi', methods=['POST'])
def shutdown_pi():
    try:
        data = request.get_json()
        zoom_value = float(data.get("zoom", 1))  # Default zoom to 1 if none pr>
        # Shutdown the Raspberry Pi.
        print("Shutting down the Raspberry Pi...")
        os.system("sudo shutdown now")
    except Exception as e:
        print(f"Error stopping Picamera2: {e}")




if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


from datetime import datetime
from flask import send_file  # already imported Flask etc.

@app.route('/capture_image', methods=['POST'])
def capture_image():
    global picam2

    try:
        print("⚙️ Switching to high-res for image capture...")

        with camera_lock:  # Ensure thread-safe access
            picam2.stop()

            # High-res capture config
            high_res_config = picam2.create_still_configuration(
                main={"size": (9152, 6944), "format": "RGB888"}  # Max res
            )
            picam2.configure(high_res_config)
            picam2.start()
            time.sleep(1.5)  # Stabilize

            # Capture
            frame = picam2.capture_array()

            # ✅ Save in accessible static folder
            save_folder = "static/captures"
            os.makedirs(save_folder, exist_ok=True)

            filename = f"captured_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            filepath = os.path.join(save_folder, filename)

            cv2.imwrite(filepath, frame)
            print(f"✅ Image saved: {filepath}")

            # Reconfigure back to video stream
            video_config = picam2.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"},
                controls={"FrameRate": 24}
            )
            picam2.configure(video_config)
            picam2.start()

            # ✅ Return relative URL so browser can download
            return jsonify({
                "message": "Image captured successfully ✅",
                "url": f"/static/captures/{filename}"
            }), 200

    except Exception as e:
        print("❌ Error capturing image:", e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
