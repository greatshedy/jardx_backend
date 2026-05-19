from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from db.database import products_collection, orders_collection, reviews_collection, user_collection, transactions_collection
from model import Product, Order, Review, OrderItem
from utill import get_token, get_image_url, send_jardproc_invoice_email, VerifyHashed
from fastapi.responses import JSONResponse
import datetime
import secrets

router = APIRouter(prefix="/jardproc", tags=["JardProc"])

@router.get("/products")
async def get_products(category: str = "All"):
    try:
        query = {}
        if category != "All":
            query = {"category": category}
        
        products = list(products_collection.find(query))
        for p in products:
            p["_id"] = str(p["_id"])
            if isinstance(p.get("image"), list):
                p["image"] = [get_image_url(img) for img in p["image"]]
            else:
                p["image"] = get_image_url(p.get("image"))
            
        return JSONResponse({"message": "Products fetched", "data": products, "status": 200})
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
        return JSONResponse({"message": str(e), "status": 500})
