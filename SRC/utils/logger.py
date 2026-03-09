import sys
from pathlib import Path
from loguru import logger

_registered_sessions: set = set()

# In dashboard mode stdout is suppressed and logs are piped into the Rich TUI sink.
# ai_log and trade_log are separate loguru instances with their own rotating files.
def setup_logger(
    log_dir: str = "DATA/logs",
    level: str = "INFO",
    dashboard_mode: bool = False,
) -> "logger":

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger.remove()

    if not dashboard_mode:

        logger.add(
            sys.stdout,
            level=level,
            colorize=True,
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level:<8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
                "{message}"
            ),
        )
    else:

        from .dashboard import log_sink
        ui_level = level if level in ("WARNING", "ERROR", "CRITICAL") else "INFO"
        logger.add(
            log_sink,
            level=ui_level,
            format="{message}",
        )

    logger.add(
        f"{log_dir}/system_{{time:YYYY-MM-DD}}.log",
        level="DEBUG",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    )

    return logger

def add_session_logger(log_dir: str, account_id: str) -> None:

    if not account_id or account_id in _registered_sessions:
        return
    _registered_sessions.add(account_id)

    session_dir = Path(log_dir) / account_id
    session_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(session_dir / "bot_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
        filter=lambda record, _aid=account_id: record["extra"].get("account_id") == _aid,
    )

    logger.add(
        str(session_dir / "ai_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        filter=lambda record, _aid=account_id: (
            record["extra"].get("ai_log") is True
            and record["extra"].get("account_id") == _aid
        ),
    )

log = logger

ai_log = logger.bind(ai_log=True)
