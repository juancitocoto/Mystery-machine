"""
storage.py
----------
Handles everything related to safely saving uploaded audio files.
Keeping this logic separate from main.py means your upload rules
live in one testable place.
"""

import os
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException

from app.config import MAX_FILE_SIZE_BYTES, ALLOWED_EXTENSIONS, UPLOAD_DIR

# Make sure the upload folder exists when the app starts.
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


def validate_extension(filename: str) -> str:
    """
    Check the file extension against our whitelist.
    Returns the (lowercased) extension if valid, otherwise raises an error.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )
    return ext


async def save_upload_securely(file: UploadFile) -> str:
    """
    Validates and saves an uploaded audio file to disk.
    Returns the path where it was saved.

    Security notes (why we do things this way):
    - We NEVER use the client's original filename for storage, because a
      filename like "../../etc/passwd.mp3" could be used to try to write
      outside our intended folder (a "path traversal" attack). Instead we
      generate our own random filename (a UUID) and only keep the extension.
    - We check the file size as we stream it to disk, and stop early if it's
      too big, instead of trusting a Content-Length header (which can lie).
    """
    ext = validate_extension(file.filename)

    # Generate a safe, unpredictable filename. We never trust user input here.
    safe_filename = f"{uuid.uuid4().hex}{ext}"
    destination_path = os.path.join(UPLOAD_DIR, safe_filename)

    total_bytes_written = 0
    chunk_size = 1024 * 1024  # read/write 1 MB at a time, not all at once

    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_bytes_written += len(chunk)

            if total_bytes_written > MAX_FILE_SIZE_BYTES:
                # Stop immediately, delete the partial file, and reject.
                out_file.close()
                os.remove(destination_path)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max size is {MAX_FILE_SIZE_BYTES // (1024*1024)} MB.",
                )

            out_file.write(chunk)

    return destination_path
