from datetime import date
from enum import Enum
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from ..security import current_user

router = APIRouter(prefix='/items', tags=['items'])


class Size(str, Enum):
    small = 'S'
    medium = 'M'
    large = 'L'


class Dimensions(BaseModel):
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ItemIn(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    price: float = Field(ge=0)
    size: Size = Size.medium
    tags: List[str] = []
    dimensions: Optional[Dimensions] = None
    available_from: Optional[date] = None


class ItemOut(ItemIn):
    id: int


ITEMS = {}


@router.get('/', response_model=List[ItemOut])
def list_items(q: Optional[str] = None, limit: int = Query(10, le=100), size: Optional[Size] = None):
    return list(ITEMS.values())[:limit]


@router.post('/', response_model=ItemOut, status_code=201)
def create_item(item: ItemIn, user: str = Depends(current_user)):
    item_id = len(ITEMS) + 1
    ITEMS[item_id] = {'id': item_id, **(item.model_dump() if hasattr(item, 'model_dump') else item.dict())}
    return ITEMS[item_id]


@router.get('/{item_id}', response_model=ItemOut)
def get_item(item_id: int):
    return ITEMS.get(item_id) or {'id': item_id, 'name': 'x', 'price': 0}


@router.patch('/{item_id}')
def rename_item(item_id: int, name: str = Form(...), user: str = Depends(current_user)):
    return {'id': item_id, 'name': name}


@router.post('/{item_id}/image')
def upload_image(item_id: int, image: UploadFile = File(...), caption: str = Form(''),
                 user: str = Depends(current_user)):
    return {'id': item_id, 'filename': image.filename, 'caption': caption}


@router.delete('/{item_id}', status_code=204)
def delete_item(item_id: int, user: str = Depends(current_user)):
    ITEMS.pop(item_id, None)


@router.get('/by-ref/{ref}')
def by_ref(ref: UUID):
    return {'ref': str(ref)}
