import os

origins = os.getenv(
    "VAHAN_API_CORS_ORIGINS",
    "http://localhost:5173",
)
socketio_origins = os.getenv("VAHAN_API_SOCKETIO_CORS_ORIGINS", "*")
port = int(os.getenv("VAHAN_API_PORT", "8000"))
