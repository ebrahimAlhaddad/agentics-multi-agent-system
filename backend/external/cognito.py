from fastapi import HTTPException, status, Request
import boto3
from external.base import ExternalService
from settings import settings
from logger import logger
from exceptions.exceptions import ExternalServiceException
from models.user import CognitoUser

class CognitoService(ExternalService):
    def __init__(self):
        self.client = None

    async def startup(self):
        """Initialize the Cognito client."""
        logger.info("Starting up Cognito Service")
        try:
            if settings.DISABLE_AUTH:
                logger.info("Cognito Service disabled via environment variable")
                self.client = None
            else:
                self.client = boto3.client('cognito-idp', region_name=settings.DEFAULT_AWS_REGION)
            logger.info("Cognito Service started successfully")
        except Exception as e:
            msg = f"Failed to initialize Cognito client: {str(e)}"
            logger.error(msg)
            raise ExternalServiceException(msg, "CognitoService")

    async def sanity_check(self):
        """Verify the Cognito service is working correctly."""
        logger.info("Sanity checking Cognito Service")
        if settings.DISABLE_AUTH:
            logger.info("Cognito Service disabled via environment variable")
            return
        try:
            response = self.client.list_user_pools(MaxResults=1)
            logger.info("Cognito Service sanity check completed")
        except Exception as e:
            msg = f"Cognito Service sanity check failed: {str(e)}"
            logger.error(msg)
            raise ExternalServiceException(msg, "CognitoService")

    async def get_user(self, request: Request):
        """Retrieve user attributes using the provided access token."""
        headers = request.headers
        if settings.DISABLE_AUTH:
            logger.info("Cognito Service disabled via environment variable")
            return CognitoUser(Username=f"dummy_user")
        logger.info("Fetching user data from Cognito")
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
        try:
            access_token = auth_header[len("Bearer "):].strip()
            response = self.client.get_user(AccessToken=access_token)
            return CognitoUser(**response)
        except self.client.exceptions.NotAuthorizedException:
            msg = "The access token is invalid or expired."
            logger.error(msg)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)
        except Exception as e:
            msg = f"Failed to fetch user data: {str(e)}"
            logger.error(msg)
            raise ExternalServiceException(msg, "CognitoService")

    async def shutdown(self):
        """Clean up resources."""
        logger.info("Shutting down Cognito Service")
        # No cleanup needed for the Cognito client
        logger.info("Cognito Service shutdown completed")

# Provide service as singleton
cognito_service = CognitoService()
