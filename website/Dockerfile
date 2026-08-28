FROM python:3.12-slim
WORKDIR /app
COPY index.html .
CMD ["sh", "-c", "python -m http.server ${PORT:-8080}"]
