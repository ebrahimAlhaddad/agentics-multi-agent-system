import logging
from asgi_correlation_id import CorrelationIdFilter
from settings import settings


def get_request_id_handler() -> logging.Handler:    
    """
    Returns a logging handler that adds the request ID to the log messages.
    """
    cid_filter = CorrelationIdFilter(uuid_length=32)
    console_handler = logging.StreamHandler()
    console_handler.addFilter(cid_filter)
    return console_handler


# Custom formatter to add more context to error messages
class DetailedErrorFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno >= logging.ERROR:
            # For errors, include the actual error and its location
            if hasattr(record, 'exc_info') and record.exc_info:
                exc_type, exc_value, exc_traceback = record.exc_info
                if exc_traceback:
                    # Handle ExceptionGroup
                    if isinstance(exc_value, ExceptionGroup):
                        # Get the first exception from the group
                        first_exc = exc_value.exceptions[0]
                        if hasattr(first_exc, '__traceback__'):
                            tb = first_exc.__traceback__
                            while tb.tb_next:
                                tb = tb.tb_next
                            frame = tb.tb_frame
                            record.exc_text = f"{type(first_exc).__name__}: {str(first_exc)} at {frame.f_code.co_filename}:{frame.f_lineno}"
                    else:
                        # Handle regular exceptions
                        tb = exc_traceback
                        while tb.tb_next:
                            tb = tb.tb_next
                        frame = tb.tb_frame
                        record.exc_text = f"{exc_type.__name__}: {exc_value} at {frame.f_code.co_filename}:{frame.f_lineno}"
        return super().format(record)


logging.basicConfig(
    handlers=[get_request_id_handler()],
    level=settings.LOG_LEVEL,
    format='%(asctime)s: %(name)s[%(correlation_id)s] [%(levelname)s] %(filename)s:%(funcName)s - %(message)s'
)

# Set the custom formatter for the root logger
for handler in logging.getLogger().handlers:
    handler.setFormatter(DetailedErrorFormatter(handler.formatter._fmt))

# exports logger singleton
logger = logging.getLogger("agentics") 
