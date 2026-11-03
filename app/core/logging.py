import logging


class RedactLoginCallback(logging.Filter):
    def filter(self, record):
        # Uvicorn's access logger includes the query string in the request target.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            if isinstance(target, str) and target.split("?", 1)[0] == "/auth/callback":
                record.args = (address, method, "/auth/callback", version, status)
        return True


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactLoginCallback) for item in access.filters):
        access.addFilter(RedactLoginCallback())

