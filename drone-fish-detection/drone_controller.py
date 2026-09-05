"""
Wraps a video source so main.py can treat "my webcam", "a saved video",
and "an actual Tello drone" the same way: create it, call read_frame()
in a loop, call close() when done.

Start with VideoSource (webcam or video file) to test fish detection
before ever touching a real drone. Switch to TelloController once that
looks good and you're ready to fly.
"""

import cv2


class VideoSource:
    """Webcam (source=0, 1, ...) or a video file (source="path/to.mp4")."""

    def __init__(self, source):
        self.capture = cv2.VideoCapture(source)
        if not self.capture.isOpened():
            raise RuntimeError(f"Could not open video source: {source}")

    def read_frame(self):
        ok, frame = self.capture.read()
        return frame if ok else None

    def close(self):
        self.capture.release()


class TelloController:
    """
    Controls a DJI Tello drone and reads its live camera feed.

    The Tello has no GPS and can't be told "go hover over this exact spot",
    so flight here is manual: you fly it with the keyboard (see main.py's
    key bindings) while fish detection runs on the video feed in real time.
    """

    def __init__(self):
        from djitellopy import Tello

        self.drone = Tello()
        self.drone.connect()
        print(f"Battery: {self.drone.get_battery()}%")

        self.drone.streamon()
        self.frame_reader = self.drone.get_frame_read()
        self.flying = False

    def read_frame(self):
        frame = self.frame_reader.frame
        if frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def takeoff(self):
        if not self.flying:
            self.drone.takeoff()
            self.flying = True

    def land(self):
        if self.flying:
            self.drone.land()
            self.flying = False

    def move(self, left_right, forward_back, up_down, yaw):
        """Each argument is a speed from -100 to 100."""
        self.drone.send_rc_control(left_right, forward_back, up_down, yaw)

    def close(self):
        self.land()
        self.drone.streamoff()
        self.drone.end()
