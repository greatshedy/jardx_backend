import logging
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from db.database import products_collection, orders_collection, reviews_collection, user_collection, transactions_collection, distributors_collection
from model import Product, Order, Review, OrderItem, DistributorRegistration
from utill import (
    get_token,
    get_image_url,
    send_jardproc_invoice_email,
    send_distributor_welcome_email,
    upload_to_cloudinary,
    VerifyHashed,
)
from fastapi.responses import JSONResponse
import datetime
import secrets

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/jardproc", tags=["JardProc"])

@router.get("/products")
async def get_products(category: str = "All"):
    try:
        query = {}
        if category != "All":
            query = {"category": category}
        
        products = list(products_collection.find(query))
        
        all_reviews = list(reviews_collection.find({}))
        rating_map = {}
        for r in all_reviews:
            pid = r.get("product_id")
            if pid not in rating_map:
                rating_map[pid] = {"total": 0, "sum": 0}
            rating_map[pid]["total"] += 1
            rating_map[pid]["sum"] += int(r.get("rating", 0))
        
        for p in products:
            pid = str(p["_id"])
            if pid in rating_map:
                p["rating"] = round(rating_map[pid]["sum"] / rating_map[pid]["total"], 1)
                p["reviews"] = rating_map[pid]["total"]
            else:
                p["rating"] = None
                p["reviews"] = None
            
            if isinstance(p.get("image"), list):
                p["image"] = [get_image_url(img) for img in p["image"]]
            else:
                p["image"] = get_image_url(p.get("image"))
            
        return JSONResponse({"message": "Products fetched", "data": products, "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/products/{product_id}/rating")
async def get_product_rating(product_id: str):
    try:
        reviews = list(reviews_collection.find({"product_id": product_id}))
        total = len(reviews)
        distribution = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        total_rating = 0
        for r in reviews:
            star = int(r.get("rating", 0))
            distribution[star] = distribution.get(star, 0) + 1
            total_rating += star
        average = round(total_rating / total, 1) if total > 0 else 0
        return JSONResponse({
            "data": {"average": average, "total": total, "distribution": distribution},
            "status": 200
        })
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/products/{product_id}")
async def get_product_details(product_id: str):
    try:
        product = products_collection.find_one({"_id": product_id})
        if not product:
            return JSONResponse({"message": "Product not found", "status": 404})
            
        product["_id"] = str(product["_id"])
        if isinstance(product.get("image"), list):
            product["image"] = [get_image_url(img) for img in product["image"]]
        else:
            product["image"] = get_image_url(product.get("image"))
        return JSONResponse({"message": "Product details fetched", "data": product, "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/place-order")
async def place_order(order_data: dict, background_tasks: BackgroundTasks, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        user = user_collection.find_one({"_id": user_id})
        
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})
            
        # Verify PIN
        pin = order_data.get("pin")
        stored_pin = user.get("transaction_pin")
        if not stored_pin:
            return JSONResponse({"message": "No transaction PIN set", "status": 400})
            
        try:
            if not VerifyHashed(stored_pin, str(pin)):
                return JSONResponse({"message": "Invalid transaction PIN", "status": 400})
        except Exception:
            return JSONResponse({"message": "Invalid transaction PIN", "status": 400})
            
        final_total = float(order_data.get("final_total"))
        wallet_balance = float(user.get("wallet_balance", 0))
        
        if wallet_balance < final_total:
            return JSONResponse({"message": "Insufficient wallet balance", "status": 400})
            
        # Deduct from wallet
        new_balance = wallet_balance - final_total
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
        
        # Create order
        order_dict = {
            "user_id": user_id,
            "items": order_data.get("items"),
            "total_amount": float(order_data.get("total_amount")),
            "shipping_fee": float(order_data.get("shipping_fee")),
            "final_total": final_total,
            "shipping_address": order_data.get("shipping_address"),
            "status": "Pending",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "order_id": f"ORD-{secrets.token_hex(4).upper()}"
        }
        
        orders_collection.insert_one(order_dict)
        
        # Record transaction
        transactions_collection.insert_one({
            "tx_ref": f"PURCHASE-{secrets.token_hex(4).upper()}",
            "user_id": user_id,
            "amount": final_total,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Order Purchase: {order_dict['order_id']}",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        # Send invoice email via background task
        shipping_addr = order_data.get("shipping_address", {})
        formatted_address = f"{shipping_addr.get('address')}, {shipping_addr.get('city', '')}, {shipping_addr.get('state', '')}"
        background_tasks.add_task(
            send_jardproc_invoice_email,
            user.get("email"),
            user.get("user_name", "Valued Customer"),
            order_dict["order_id"],
            final_total,
            order_data.get("items", []),
            formatted_address
        )
        
        return JSONResponse({
            "message": "Order placed successfully", 
            "status": 200, 
            "order_id": order_dict["order_id"],
            "new_balance": new_balance
        })
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

MIN_DISTRIBUTOR_QUANTITY = 100

def _normalize_category(value: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized[:-1] if normalized.endswith("s") else normalized

def _variant_label(variant: dict) -> str:
    if not variant:
        return ""
    parts = [variant.get("name") or ""]
    scale = f"{variant.get('scale_value') or ''} {variant.get('scale_unit') or ''}".strip()
    if scale:
        parts.append(scale)
    return " - ".join(part for part in parts if part)

@router.post("/register-distributor")
async def register_distributor(
    payload: DistributorRegistration,
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(get_token),
):
    try:
        user_id = user_payload.get("id")
        user = user_collection.find_one({"_id": user_id})

        if not user:
            return JSONResponse({"message": "User not found", "status": 404})

        client_ref = (payload.client_ref or "").strip()
        if client_ref:
            existing = next(
                (
                    record for record in distributors_collection.find({})
                    if str(record.get("user_id")) == str(user_id)
                    and record.get("client_ref") == client_ref
                ),
                None,
            )
            if existing:
                current_user = user_collection.find_one({"_id": user_id}) or user
                return JSONResponse({
                    "message": "Distributor registration successful",
                    "status": 200,
                    "distributor_id": existing.get("distributor_id"),
                    "order_id": existing.get("order_id"),
                    "new_balance": float(current_user.get("wallet_balance", 0) or 0),
                })

        stored_pin = user.get("transaction_pin")
        if not stored_pin:
            return JSONResponse({"message": "No transaction PIN set", "status": 400})
        try:
            if not VerifyHashed(stored_pin, str(payload.pin)):
                return JSONResponse({"message": "Invalid transaction PIN", "status": 400})
        except Exception:
            return JSONResponse({"message": "Invalid transaction PIN", "status": 400})

        full_name = (payload.fullName or "").strip()
        phone = (payload.phone or "").strip()
        address = (payload.address or "").strip()
        nin = (payload.nin or "").strip()

        if len(full_name) < 2:
            return JSONResponse({"message": "Please enter your full name", "status": 400})
        if len(phone) < 7:
            return JSONResponse({"message": "Please enter a valid phone number", "status": 400})
        if len(address) < 5:
            return JSONResponse({"message": "Please enter your full address", "status": 400})
        if not nin.isdigit() or len(nin) != 11:
            return JSONResponse({"message": "NIN must be 11 digits", "status": 400})
        if payload.quantity < MIN_DISTRIBUTOR_QUANTITY:
            return JSONResponse({
                "message": f"Minimum quantity is {MIN_DISTRIBUTOR_QUANTITY}",
                "status": 400,
            })

        product = products_collection.find_one({"_id": payload.product_id})
        if not product:
            return JSONResponse({"message": "Product not found", "status": 404})

        if _normalize_category(product.get("category")) != _normalize_category(payload.category):
            return JSONResponse({"message": "Product does not match the selected category", "status": 400})

        variants = product.get("variants") or []
        selected_variant = None
        if variants:
            if payload.variant_index is None or payload.variant_index < 0 or payload.variant_index >= len(variants):
                return JSONResponse({"message": "Please select a product variant", "status": 400})
            selected_variant = variants[payload.variant_index]

        try:
            unit_price = float(selected_variant.get("price")) if selected_variant else float(product.get("price"))
        except (TypeError, ValueError):
            return JSONResponse({"message": "Invalid product price", "status": 400})

        if unit_price <= 0:
            return JSONResponse({"message": "Invalid product price", "status": 400})

        quantity = int(payload.quantity)
        total_amount = round(unit_price * quantity, 2)
        wallet_balance = float(user.get("wallet_balance", 0) or 0)

        if wallet_balance < total_amount:
            return JSONResponse({"message": "Insufficient wallet balance", "status": 400})

        selfie_url = upload_to_cloudinary(payload.selfie, f"distributors/selfies/{user_id}")
        if not selfie_url:
            return JSONResponse({"message": "Failed to upload selfie", "status": 500})

        now = datetime.datetime.utcnow().isoformat()
        order_id = f"ORD-{secrets.token_hex(4).upper()}"
        distributor_id = f"DST-{secrets.token_hex(4).upper()}"
        variant_label = _variant_label(selected_variant)
        item_name = product.get("name") or "Product"
        if variant_label:
            item_name = f"{item_name} - {variant_label}"

        new_balance = wallet_balance - total_amount
        user_collection.update_one({"_id": user_id}, {"$set": {
            "wallet_balance": new_balance,
            "is_distributor": True,
            "distributor_category": payload.category,
            "distributor_status": "Pending",
            "updated_at": now,
        }})

        order_dict = {
            "user_id": user_id,
            "items": [{
                "id": str(product.get("_id")),
                "name": item_name,
                "price": unit_price,
                "quantity": quantity,
                "variant": variant_label or None,
            }],
            "total_amount": total_amount,
            "shipping_fee": 0,
            "final_total": total_amount,
            "shipping_address": {
                "fullName": full_name,
                "phone": phone,
                "address": address,
            },
            "status": "Pending",
            "is_distributor_order": True,
            "distributor_id": distributor_id,
            "created_at": now,
            "order_id": order_id,
        }

        distributor_dict = {
            "_id": distributor_id,
            "user_id": user_id,
            "distributor_id": distributor_id,
            "client_ref": client_ref,
            "full_name": full_name,
            "phone": phone,
            "address": address,
            "nin": nin,
            "selfie": selfie_url,
            "category": payload.category,
            "product_id": str(product.get("_id")),
            "product_name": product.get("name"),
            "variant": selected_variant,
            "variant_label": variant_label,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_amount": total_amount,
            "order_id": order_id,
            "status": "Pending",
            "created_at": now,
        }

        order_inserted = False
        distributor_inserted = False
        try:
            orders_collection.insert_one(order_dict)
            order_inserted = True
            distributors_collection.insert_one(distributor_dict)
            distributor_inserted = True
        except Exception:
            if order_inserted:
                orders_collection.delete_one({"order_id": order_id})
            if distributor_inserted:
                distributors_collection.delete_one({"_id": distributor_id})
            user_collection.update_one({"_id": user_id}, {"$set": {
                "wallet_balance": wallet_balance,
                "is_distributor": user.get("is_distributor", False),
                "distributor_category": user.get("distributor_category"),
                "distributor_status": user.get("distributor_status"),
            }})
            raise

        try:
            transactions_collection.insert_one({
                "tx_ref": f"DIST-{secrets.token_hex(4).upper()}",
                "user_id": user_id,
                "amount": total_amount,
                "gateway": "Wallet",
                "type": "DEBIT",
                "purpose": f"Distributor Registration: {order_id}",
                "status": "SUCCESS",
                "created_at": now,
            })
        except Exception as tx_error:
            logger.error(f"Distributor transaction record failed: {tx_error}")

        background_tasks.add_task(
            send_distributor_welcome_email,
            user.get("email"),
            full_name or user.get("user_name", "Distributor"),
            distributor_id,
            product.get("name") or "Product",
            variant_label,
            quantity,
            total_amount,
        )

        return JSONResponse({
            "message": "Distributor registration successful",
            "status": 200,
            "distributor_id": distributor_id,
            "order_id": order_id,
            "new_balance": new_balance,
        })
    except Exception as e:
        logger.error(f"Error in /register-distributor: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/my-orders")
async def get_my_orders(user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        orders = list(orders_collection.find({"user_id": user_id}).sort({"created_at": -1}))
        for o in orders:
            o["_id"] = str(o["_id"])
        return JSONResponse({"message": "Orders fetched", "data": orders, "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/reviews")
async def add_review(review: dict, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        user = user_collection.find_one({"_id": user_id})
        
        review_dict = {
            "product_id": review.get("product_id"),
            "user_id": user_id,
            "user_name": user.get("user_name", "User"),
            "rating": int(review.get("rating")),
            "comment": review.get("comment"),
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        
        reviews_collection.insert_one(review_dict)
        return JSONResponse({"message": "Review added successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/reviews/{product_id}")
async def get_product_reviews(product_id: str):
    try:
        reviews = list(reviews_collection.find({"product_id": product_id}).sort({"created_at": -1}))
        for r in reviews:
            r["_id"] = str(r["_id"])
        return JSONResponse({"message": "Reviews fetched", "data": reviews, "status": 200})
    except Exception as e:
        logger.error(f"Error in /reviews: {e}")
        if "CORRUPTED_COLLECTION_SCHEMA" in str(e):
            return JSONResponse({"message": "Reviews fetched", "data": [], "status": 200})
        return JSONResponse({"message": str(e), "status": 500})

@router.put("/reviews/{review_id}")
async def update_review(review_id: str, review: dict, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        existing = reviews_collection.find_one({"_id": review_id})
        if not existing:
            return JSONResponse({"message": "Review not found", "status": 404})
        if str(existing.get("user_id")) != str(user_id):
            return JSONResponse({"message": "You can only edit your own review", "status": 403})
        
        updates = {
            "rating": int(review.get("rating")),
            "comment": review.get("comment"),
            "updated_at": datetime.datetime.utcnow().isoformat()
        }
        reviews_collection.update_one({"_id": review_id}, {"$set": updates})
        return JSONResponse({"message": "Review updated successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        existing = reviews_collection.find_one({"_id": review_id})
        if not existing:
            return JSONResponse({"message": "Review not found", "status": 404})
        if str(existing.get("user_id")) != str(user_id):
            return JSONResponse({"message": "You can only delete your own review", "status": 403})
        
        reviews_collection.delete_one({"_id": review_id})
        return JSONResponse({"message": "Review deleted successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})
