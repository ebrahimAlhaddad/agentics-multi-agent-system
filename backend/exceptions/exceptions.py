class ExternalServiceException(Exception):
    """Exception raised for errors in the external service."""
    def __init__(self, message: str, service_name: str):
        self.message = f"External Service Error: {service_name}: {message}"
        super().__init__(self.message)

class ServiceLayerException(Exception):
    """Exception raised for errors in the service layer."""
    def __init__(self, message: str, service_name: str):
        self.message = f"Service Layer Error: {service_name}: {message}"
        super().__init__(self.message)

class NotFoundException(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)