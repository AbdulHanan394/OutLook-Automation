import logging
import re

SECRET = re.compile(r"(?i)(bearer\s+|client_secret[=:]\s*|api[_-]?key[=:]\s*)[^\s,]+")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = SECRET.sub(r"\1[REDACTED]", str(record.msg))
        record.args = ()
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RedactingFilter())
