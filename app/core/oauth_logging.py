"""Redact OAuth query parameters before standard HTTP/access log formatting."""
import logging
import re


class OAuthQueryFilter(logging.Filter):
    def filter(self, record):
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if str(args[2]).split("?", 1)[0].startswith("/auth/"):
                args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        else:
            message = record.getMessage()
            record.msg = re.sub(r'(/auth/[^\s?"#]+)\?[^\s"]*', r'\1?[redacted]', message)
            record.args = ()
        return True


def install_oauth_log_filter():
    for name in ("uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, OAuthQueryFilter) for f in logger.filters):
            logger.addFilter(OAuthQueryFilter())
