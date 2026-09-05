"""
Fly above water and highlight fish moving below, in real time.

Usage:
    # Test detection on your webcam first (no drone needed):
    python main.py --source 0

    # Test detection on a saved video of water:
    python main.py --source path/to/video.mp4

    # Fly a real Tello drone and detect fish live:
    python main.py --source tello

Keyboard controls (only active with --source tello):
    t = takeoff       l = land
    w/s = forward/back    a/d = left/right
    up/down arrows = rise/descend
    left/right arrows = rotate (yaw)
    q = quit (lands the drone first if it's flying)
"""

import argparse

import cv2

from drone_controller import TelloController, VideoSource
from fish_detector import FishDetector

SPEED = 50  # 0-100, how fast the drone moves per key press


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", default="0",
        help='"tello" for a real drone, or a webcam index / video file path',
    )
    parser.add_argument(
        "--record", default=None,
        help="optional path to save the annotated video, e.g. out.mp4",
    )
    return parser.parse_args()


def make_video_source(source_arg):
    if source_arg == "tello":
        return TelloController()
    # argparse gives us a string; a webcam index needs to be an int.
    source = int(source_arg) if source_arg.isdigit() else source_arg
    return VideoSource(source)


def handle_key(key, video_source):
    """Only does anything when video_source is an actual TelloController."""
    if not isinstance(video_source, TelloController):
        return

    if key == ord("t"):
        video_source.takeoff()
    elif key == ord("l"):
        video_source.land()
    elif key == ord("w"):
        video_source.move(0, SPEED, 0, 0)
    elif key == ord("s"):
        video_source.move(0, -SPEED, 0, 0)
    elif key == ord("a"):
        video_source.move(-SPEED, 0, 0, 0)
    elif key == ord("d"):
        video_source.move(SPEED, 0, 0, 0)
    elif key == 82:  # up arrow
        video_source.move(0, 0, SPEED, 0)
    elif key == 84:  # down arrow
        video_source.move(0, 0, -SPEED, 0)
    elif key == 81:  # left arrow
        video_source.move(0, 0, 0, -SPEED)
    elif key == 83:  # right arrow
        video_source.move(0, 0, 0, SPEED)
    else:
        video_source.move(0, 0, 0, 0)  # no key held: stop drifting


def main():
    args = parse_args()
    video_source = make_video_source(args.source)
    detector = FishDetector()
    writer = None

    try:
        while True:
            frame = video_source.read_frame()
            if frame is None:
                continue

            boxes = detector.detect(frame)
            annotated = detector.draw_boxes(frame, boxes)
            cv2.putText(
                annotated, f"fish detected: {len(boxes)}", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
            )

            if args.record:
                if writer is None:
                    h, w = annotated.shape[:2]
                    writer = cv2.VideoWriter(
                        args.record, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h)
                    )
                writer.write(annotated)

            cv2.imshow("Drone fish detection", annotated)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            handle_key(key, video_source)
    finally:
        video_source.close()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
