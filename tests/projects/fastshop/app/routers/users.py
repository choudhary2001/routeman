from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, EmailStr

from ..security import current_user

router = APIRouter(prefix='/users', tags=['users'])


class SignUp(BaseModel):
    email: EmailStr
    password: str
    full_name: str


@router.post('/signup', status_code=201)
def signup(body: SignUp, x_referral: str = Header(default='')):
    return {'email': body.email, 'referral': x_referral}


@router.get('/me')
def me(user: str = Depends(current_user)):
    return {'user': user}
