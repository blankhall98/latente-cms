import logging

ISO_FMT = "%Y-%m-%dT%H:%M:%S%z"

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt=ISO_FMT,
    )
