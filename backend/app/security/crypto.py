import base64
from dataclasses import dataclass

from cryptography.fernet import Fernet, MultiFernet


@dataclass(frozen=True)
class TokenCipher:
    key_id: str
    keys: list[str]

    def _fernet(self) -> MultiFernet:
        return MultiFernet([Fernet(key.encode()) for key in self.keys])

    def encrypt(self, token: str) -> bytes:
        return f"{self.key_id}.".encode() + self._fernet().encrypt(token.encode())

    def decrypt(self, value: bytes) -> str:
        try:
            _, ciphertext = value.split(b".", 1)
        except ValueError as exc:
            raise ValueError("invalid encrypted token format") from exc
        return self._fernet().decrypt(ciphertext).decode()

    @staticmethod
    def valid_key(key: str) -> bool:
        try:
            return len(base64.urlsafe_b64decode(key)) == 32
        except Exception:
            return False
