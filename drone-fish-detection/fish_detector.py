"""
Finds fish-shaped blobs in a video frame taken from above the water.

How it works (in plain terms):
A drone hovering over water sees a mostly still background (the water
surface/bed) with fish moving around on top of it. Instead of trying to
recognize what a fish "looks like", we just watch what CHANGES between
frames. Anything that moves and is roughly fish-sized/fish-shaped gets
flagged as a possible fish. This needs no training data, so it's a good
starting point before you try anything fancier like a trained YOLO model.
"""

import cv2
import numpy as np


class FishDetector:
    def __init__(self, min_area=150, max_area=15000):
        # Ignore blobs smaller/larger than a plausible fish, so we don't
        # flag ripples (too small) or a whole shadow of the drone (too big).
        self.min_area = min_area
        self.max_area = max_area

        # Learns what the "empty water" looks like and reports anything
        # that doesn't match as foreground (a moving fish).
        self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=25, detectShadows=False
        )

    def detect(self, frame):
        """Returns a list of (x, y, w, h) boxes around likely fish."""
        mask = self.background_subtractor.apply(frame)

        # Clean up the raw mask: fill small holes, remove single-pixel noise.
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if self.min_area <= area <= self.max_area:
                boxes.append(cv2.boundingRect(contour))

        return boxes

    @staticmethod
    def draw_boxes(frame, boxes):
        """Draws a green box + label around each detection, for display."""
        annotated = frame.copy()
        for (x, y, w, h) in boxes:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                annotated, "fish?", (x, max(y - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
            )
        return annotated
