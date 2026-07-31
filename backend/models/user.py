from typing import List, Optional, Dict, Any
from pydantic import BaseModel

class UserAttribute(BaseModel):
    Name: str
    Value: str

class ResponseMetadata(BaseModel):
    RequestId: Optional[str]
    HTTPStatusCode: Optional[int]
    HTTPHeaders: Optional[Dict[str, Any]]
    RetryAttempts: Optional[int]

class CognitoUser(BaseModel):
    Username: str
    UserAttributes: Optional[List[UserAttribute]] = None
    MFAOptions: Optional[List[Any]] = None
    PreferredMfaSetting: Optional[str] = None
    UserMFASettingList: Optional[List[str]] = None
    # ResponseMetadata: Optional[ResponseMetadata] = None

    class Config:
        extra = "allow"
