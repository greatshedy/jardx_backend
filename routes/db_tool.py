from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from starlette import status
from bson import ObjectId
import json
import uuid
import logging

from db.database import (
    user_collection, house_collection, transactions_collection,
    portfolio_collection, jard_kidz_collection, vendors_collection,
    partners_collection, products_collection, orders_collection,
    reviews_collection, notifications_collection
)

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/admin/db-tool", tags=["DB Tool"])

COLLECTIONS = {
    "users": user_collection,
    "houses": house_collection,
    "transactions": transactions_collection,
    "portfolio": portfolio_collection,
    "kidz_plans": jard_kidz_collection,
    "vendors": vendors_collection,
    "partners": partners_collection,
    "products": products_collection,
    "orders": orders_collection,
    "reviews": reviews_collection,
    "notifications": notifications_collection,
}

def get_collection(name: str):
    col = COLLECTIONS.get(name)
    if not col:
        raise HTTPException(status_code=404, detail=f"Collection '{name}' not found")
    return col

def resolve_id(col, doc_id):
    doc = col.find_one({"_id": doc_id})
    if doc:
        return doc_id
    try:
        uid = uuid.UUID(doc_id)
        doc = col.find_one({"_id": uid})
        if doc:
            return uid
    except Exception:
        pass
    try:
        oid = ObjectId(doc_id)
        doc = col.find_one({"_id": oid})
        if doc:
            return oid
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Document not found")

def serialize_doc(doc):
    if doc is None:
        return None
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, (ObjectId, uuid.UUID)):
            serialized[key] = str(value)
        elif isinstance(value, dict):
            serialized[key] = serialize_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_doc(item) if isinstance(item, dict) else str(item) if isinstance(item, ObjectId) else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized

def format_value(value, max_len=80):
    if isinstance(value, dict) or isinstance(value, list):
        text = json.dumps(value, indent=2, default=str)
        return text[:max_len] + "..." if len(text) > max_len else text
    s = str(value)
    return s[:max_len] + "..." if len(s) > max_len else s

def count_collection(col):
    try:
        return len(list(col.find({}, projection={"_id": 1})))
    except Exception:
        return "?"

@router.get("")
async def db_tool_index(request: Request):
    counts = {}
    for name, col in COLLECTIONS.items():
        counts[name] = count_collection(col)
    return request.app.state.templates.TemplateResponse(
        "db_tool/index.html",
        {"request": request, "collections": COLLECTIONS, "counts": counts}
    )

def doc_matches(doc, search):
    if not search:
        return True
    search_lower = search.lower()
    for val in doc.values():
        if search_lower in str(val).lower():
            return True
    return False

@router.get("/{collection}")
async def view_collection(
    request: Request, collection: str,
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200)
):
    col = get_collection(collection)
    all_docs = list(col.find({}))
    serialized_all = [serialize_doc(d) for d in all_docs]
    if search:
        serialized_all = [d for d in serialized_all if doc_matches(d, search)]
    total = len(serialized_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    skip = (page - 1) * per_page
    docs = serialized_all[skip:skip + per_page]
    fields = set()
    for doc in docs:
        for key in doc:
            fields.add(key)
    fields = sorted(fields)
    return request.app.state.templates.TemplateResponse(
        "db_tool/collection.html",
        {
            "request": request, "collection": collection,
            "docs": docs, "fields": fields,
            "total": total, "page": page, "per_page": per_page,
            "total_pages": total_pages, "search": search,
            "format_value": format_value
        }
    )

@router.post("/{collection}/delete-all")
async def delete_all_documents(request: Request, collection: str):
    col = get_collection(collection)
    count = count_collection(col)
    col.delete_many({})
    logger.info(f"DB-Tool: Deleted all {count} docs from {collection}")
    return RedirectResponse(
        f"/admin/db-tool/{collection}?success=All+{count}+documents+deleted",
        status_code=303
    )

@router.get("/{collection}/{doc_id}")
async def view_document(request: Request, collection: str, doc_id: str):
    col = get_collection(collection)
    real_id = resolve_id(col, doc_id)
    doc = col.find_one({"_id": real_id})
    serialized = serialize_doc(doc)
    return request.app.state.templates.TemplateResponse(
        "db_tool/document.html",
        {
            "request": request, "collection": collection,
            "doc": serialized, "doc_id": doc_id,
            "format_value": format_value
        }
    )

@router.post("/{collection}/{doc_id}")
async def update_document(request: Request, collection: str, doc_id: str):
    col = get_collection(collection)
    real_id = resolve_id(col, doc_id)
    form = await request.form()
    update_data = {}
    for key in form:
        if key != "_method" and key != "doc_id":
            raw = form[key]
            if raw == "":
                continue
            try:
                update_data[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                update_data[key] = raw
    if not update_data:
        return RedirectResponse(
            f"/admin/db-tool/{collection}/{doc_id}?error=No+fields+to+update",
            status_code=303
        )
    try:
        col.update_one({"_id": real_id}, {"$set": update_data})
        logger.info(f"DB-Tool: Updated {collection}/{doc_id}")
        return RedirectResponse(
            f"/admin/db-tool/{collection}/{doc_id}?success=Document+updated",
            status_code=303
        )
    except Exception as e:
        logger.error(f"DB-Tool: Update failed {collection}/{doc_id}: {e}")
        return RedirectResponse(
            f"/admin/db-tool/{collection}/{doc_id}?error=Update+failed:+{str(e)}",
            status_code=303
        )

@router.post("/{collection}/{doc_id}/delete")
async def delete_document(request: Request, collection: str, doc_id: str):
    col = get_collection(collection)
    real_id = resolve_id(col, doc_id)
    try:
        col.delete_one({"_id": real_id})
        logger.info(f"DB-Tool: Deleted {collection}/{doc_id}")
        return RedirectResponse(
            f"/admin/db-tool/{collection}?success=Document+deleted",
            status_code=303
        )
    except Exception as e:
        logger.error(f"DB-Tool: Delete failed {collection}/{doc_id}: {e}")
        return RedirectResponse(
            f"/admin/db-tool/{collection}/{doc_id}?error=Delete+failed:+{str(e)}",
            status_code=303
        )
