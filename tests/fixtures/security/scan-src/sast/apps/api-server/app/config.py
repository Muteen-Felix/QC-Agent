"""Đọc cấu hình đã cache bằng pickle (không tin cậy nếu file do bên ngoài ghi)."""
import pickle


def load_cached_config(path: str):
    with open(path, "rb") as handle:
        return pickle.load(handle)
