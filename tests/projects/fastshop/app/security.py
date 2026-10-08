from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

oauth2 = OAuth2PasswordBearer(tokenUrl='/auth/token')
TOKENS = {'secret-token-alice': 'alice'}


def current_user(token: str = Depends(oauth2)) -> str:
    user = TOKENS.get(token)
    if user is None:
        raise HTTPException(status_code=401, detail='invalid token')
    return user
