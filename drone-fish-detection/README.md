# Drone Fish Detection

A starter project for flying a drone above water and spotting fish moving
underneath, using your camera feed and OpenCV. No AI training data
required — it works by noticing motion, which is simple enough to
understand and tweak, and a good base to build on later (e.g. swapping in
a trained model like YOLO if you want smarter detection).

## How it works

- `fish_detector.py` — looks at the video frame-by-frame and flags
  anything that's moving and roughly fish-sized as a possible fish
  (background subtraction + contour filtering). This is the "brain".
- `drone_controller.py` — gets video frames, either from your webcam/a
  saved video file, or from a real DJI Tello drone. Also has the
  takeoff/land/move commands for the Tello.
- `main.py` — the program you actually run. Reads frames, runs the
  detector, draws boxes on screen, and (if flying a real Tello) lets you
  control it with the keyboard.

## Setup

```bash
pip install -r requirements.txt
```

You only need `djitellopy` if you're flying a real Tello drone — it's
listed so it's ready when you get there.

## Step 1: test detection with no drone at all

This is the important first step. Point your webcam at a fish tank, or
find a video online of fish seen from above water, and run:

```bash
python main.py --source 0                    # webcam
python main.py --source path/to/video.mp4     # a video file
```

A window pops up showing the video with green boxes around anything
detected as moving. Tune `min_area` / `max_area` in `fish_detector.py`
if it's flagging too much noise (ripples, glare) or missing real fish —
those numbers are in pixels and depend on how far the camera is from
the water.

Press `q` to quit.

## Step 2: fly a real drone

This project is written for the **DJI Tello** because it's the cheapest
drone with a proper Python SDK and a downward-ish camera — good for
learning. Connect to its Wi-Fi network, then run:

```bash
python main.py --source tello
```

Keyboard controls:

| Key | Action |
|---|---|
| `t` | takeoff |
| `l` | land |
| `w` / `s` | forward / backward |
| `a` / `d` | left / right |
| ↑ / ↓ | rise / descend |
| ← / → | rotate left / right |
| `q` | quit (lands first if still flying) |

Add `--record out.mp4` to any run to save the annotated video to disk.

### Using a different drone

If you have a drone with a different SDK (DJI's newer Mobile SDK,
ArduPilot/PX4 via MAVSDK, etc.), you don't need to touch
`fish_detector.py` or `main.py` — just write a new class in
`drone_controller.py` with the same two methods every source needs:
`read_frame()` (returns a frame or `None`) and `close()`. Add
takeoff/land/move if you want keyboard control too.

## Tips specific to water

Water is genuinely harder than plain ground footage:

- **Glare** is the biggest problem — reflected sky/sun on the surface
  hides everything underneath and often gets misdetected as movement.
  A polarizing filter on the camera cuts a lot of this. Overcast days
  or shooting when the sun is high (less glancing reflection) also help.
- **Refraction** bends and shifts what you see underwater, so a fish's
  apparent position isn't its real position — don't rely on this for
  precise GPS-style location, only for "there's something there".
- **Altitude matters**: fly consistently at one height for a session so
  `min_area`/`max_area` (which are in pixels) stay meaningful. Going
  much higher shrinks fish to a few pixels and hurts detection.
- **Hold still when possible** — the motion-detection approach assumes
  the background (water) is roughly steady between frames. A hovering
  drone works; fast panning will flag the whole frame as "motion".

## Safety and rules

- Check local regulations before flying over water, especially near
  wildlife, swimmers, or protected areas.
- Keep the drone within visual line of sight and don't fly low enough
  that a rogue wave or gust could dunk it.
- If you lose the video feed or control, land immediately rather than
  troubleshooting mid-flight.
