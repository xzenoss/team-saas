FROM python:3.12-slim

WORKDIR /app
COPY server.py ./server.py
COPY static ./static

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]

