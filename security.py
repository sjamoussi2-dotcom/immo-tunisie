# -*- coding: utf-8 -*-
import hashlib
import os
import re

_PBKDF2_ITERATIONS = 260000


def generate_password_hash(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2$sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def check_password_hash(stored, password):
    try:
        _, algo, iterations, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, int(iterations))
        return dk.hex() == hash_hex
    except Exception:
        return False


_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def secure_filename(filename):
    filename = os.path.basename(filename).strip().replace(" ", "_")
    filename = _FILENAME_RE.sub("", filename)
    return filename or "file"
