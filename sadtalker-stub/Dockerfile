FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.111.0 uvicorn==0.30.1
COPY stub_server.py .
COPY baked.mp4 .
EXPOSE 8005
CMD ["uvicorn", "stub_server:app", "--host", "0.0.0.0", "--port", "8005"]
