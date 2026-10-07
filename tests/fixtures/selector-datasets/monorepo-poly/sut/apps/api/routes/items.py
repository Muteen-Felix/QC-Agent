from fastapi import APIRouter

router = APIRouter(prefix="/items")


@router.get("/{item_id}")
def get_item(item_id: int):
    return {"id": item_id, "name": f"item-{item_id}"}
