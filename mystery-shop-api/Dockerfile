# syntax=docker/dockerfile:1

# --- Base image ---------------------------------------------------------
# "slim" is a much smaller Python image than the default (no compilers,
# docs, etc. that we don't need at runtime) - faster to pull and deploy.
FROM python:3.11-slim

# Don't buffer stdout/stderr - so `docker logs` shows output immediately
# instead of holding it in a buffer, and don't write .pyc files - they're
# just clutter in a throwaway container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Everything from here on happens inside /app in the container.
WORKDIR /app

# --- Install dependencies first -----------------------------------------
# Copying just requirements.txt (not the whole app) and installing before
# copying the rest of the code means Docker can reuse this layer on
# rebuilds as long as requirements.txt hasn't changed - so changing a
# line of app code doesn't force a full reinstall every time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy the application code -------------------------------------------
# Only app/ is copied in - tests/, conftest.py, venv/, .git/ etc. are
# excluded via .dockerignore, so the image stays small and doesn't ship
# test-only dependencies or your local venv.
COPY app ./app

# Uvicorn will listen on this port inside the container. This line is
# documentation for humans/tools (like `docker inspect`) - it doesn't
# actually publish the port; that happens with `-p` on `docker run` or
# the "ports:" section in docker-compose.yml.
EXPOSE 8000

# --host 0.0.0.0 is required so the server accepts connections from
# outside the container (e.g. your browser on the host machine) - by
# default uvicorn only listens on localhost *inside* the container,
# which nothing outside it can reach.
#
# --port ${PORT:-8000}: most hosting platforms (Railway, Render, Fly.io,
# etc.) pick their OWN port at runtime and tell your container which one
# to use via a PORT environment variable - your app has to listen on
# THAT port, not a hardcoded one, or the platform's health check can't
# reach it and the deploy fails. ${PORT:-8000} means "use $PORT if the
# platform set one, otherwise fall back to 8000" - so this same
# Dockerfile still works unchanged for local `docker run`/`docker
# compose up`, where nothing sets PORT.
#
# This has to be the SHELL form of CMD (a plain string, not a ["a",
# "b"] list) for ${PORT:-8000} to actually get expanded - the list form
# runs the command directly without a shell, so that syntax would be
# passed to uvicorn literally instead of being substituted.
#
# `exec` in front of uvicorn matters: without it, the shell (/bin/sh -c
# "...") stays running as PID 1 and uvicorn runs as its CHILD process -
# when Docker/Railway sends SIGTERM to stop the container, it goes to
# PID 1 (the shell), which doesn't forward it on, so uvicorn just gets
# killed abruptly once the shutdown timeout expires instead of shutting
# down gracefully. `exec` replaces the shell process with uvicorn
# itself, so uvicorn IS PID 1 and receives that SIGTERM directly. (This
# was verified directly - a minimal container with `exec` correctly
# printed "got SIGTERM" on `docker stop`; without `exec`, the shell eats
# the signal and the process only dies once Docker's kill-timeout hits.)
#
# NOTE: `docker build` may print a "JSONArgsRecommended" warning here.
# That warning is a generic nudge to use the CMD ["a","b"] list form for
# signal-handling reasons - it doesn't know about `exec`, which already
# solves the exact problem it's warning about. This is expected and can
# be ignored.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
