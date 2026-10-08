from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter(prefix='/auth', tags=['auth'])


@router.post('/token')
def login(form: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 password login."""
    if form.username != 'alice' or form.password != 'wonderland':
        raise HTTPException(status_code=400, detail='bad credentials')
    return {'access_token': 'secret-token-alice', 'token_type': 'bearer'}
