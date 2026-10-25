"""
Inventory service HTTP client.
Centralizes all communication with inventory-service.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def inventory_url(path):
    return f"{settings.INVENTORY_SERVICE_URL}/api/{path}"


def _build_headers(request=None, idempotency_key=None):
    """Build request headers with gateway user context and optional idempotency key."""
    # A guest or the Stripe webhook has no user to forward: inventory accepts the service token instead
    headers = {"X-Internal-Token": settings.INTERNAL_SERVICE_TOKEN}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if request:
        for meta_key, header_name in [
            ("HTTP_X_GATEWAY_USER_ID", "X-Gateway-User-Id"),
            ("HTTP_X_GATEWAY_USER_ROLE", "X-Gateway-User-Role"),
            ("HTTP_X_GATEWAY_USER_USERNAME", "X-Gateway-User-Username"),
            ("HTTP_X_GATEWAY_USER_EMAIL", "X-Gateway-User-Email"),
        ]:
            if meta_key in request.META:
                headers[header_name] = request.META[meta_key]
    return headers


def _post(url, json_data, headers):
    return requests.post(url, json=json_data, timeout=10, headers=headers)


def _get(url, headers, params=None):
    return requests.get(url, timeout=10, headers=headers, params=params)


def _parse_response(resp):
    """Parse inventory service response. Returns (data, error)."""
    if resp.status_code in (200, 201):
        return resp.json(), None
    if resp.status_code == 409:
        return resp.json(), None
    try:
        return None, resp.json().get("error", f"HTTP {resp.status_code}")
    except (ValueError, AttributeError):
        return None, f"HTTP {resp.status_code}"


def reserve_stock(product_id, warehouse_id, quantity, reference_type, reference_id, idempotency_key, request=None):
    """Reserve stock for a product at a warehouse."""
    url = inventory_url("stock/reserve/")
    headers = _build_headers(request, idempotency_key)
    payload = {
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "quantity": quantity,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "idempotency_key": idempotency_key,
    }
    try:
        return _parse_response(_post(url, payload, headers))
    except requests.RequestException as e:
        logger.error("reserve_stock failed: product=%s error=%s", product_id, e)
        return None, str(e)


def deduct_stock_batch(warehouse_id, items, from_reserved, reference_type, reference_id, idempotency_key, request=None):
    """Write off all items in one inventory transaction: every item or none."""
    url = inventory_url("stock/deduct_batch/")
    headers = _build_headers(request, idempotency_key)
    payload = {
        "warehouse_id": warehouse_id,
        "items": items,
        "from_reserved": from_reserved,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "idempotency_key": idempotency_key,
    }
    try:
        return _parse_response(_post(url, payload, headers))
    except requests.RequestException as e:
        logger.error("deduct_stock_batch failed: reference=%s error=%s", reference_id, e)
        return None, str(e)


def release_stock(product_id, warehouse_id, quantity, reference_type, reference_id, idempotency_key, request=None):
    """Release reserved stock for a product."""
    url = inventory_url("stock/release/")
    headers = _build_headers(request, idempotency_key)
    payload = {
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "quantity": quantity,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "idempotency_key": idempotency_key,
    }
    try:
        return _parse_response(_post(url, payload, headers))
    except requests.RequestException as e:
        logger.error("release_stock failed: product=%s error=%s", product_id, e)
        return None, str(e)


def return_stock(product_id, warehouse_id, quantity, reference_type, reference_id, idempotency_key, request=None):
    """Put returned units back into a warehouse."""
    url = inventory_url("stock/return_to_stock/")
    headers = _build_headers(request, idempotency_key)
    payload = {
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "quantity": quantity,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "idempotency_key": idempotency_key,
    }
    try:
        return _parse_response(_post(url, payload, headers))
    except requests.RequestException as e:
        logger.error("return_stock failed: product=%s error=%s", product_id, e)
        return None, str(e)


def pick_fulfilment_warehouse(items, request=None):
    """
    Warehouse that ships a delivery order: DEFAULT_FULFILMENT_WAREHOUSE_ID, else the first active
    warehouse (type "warehouse", lowest id) holding every item. Returns (warehouse_id, error);
    (None, None) means no warehouse has it all.
    """
    if settings.DEFAULT_FULFILMENT_WAREHOUSE_ID:
        return int(settings.DEFAULT_FULFILMENT_WAREHOUSE_ID), None
    headers = _build_headers(request)
    try:
        # shortcut: reads the first page only (20 warehouses); follow "next" once there are more
        resp = _get(inventory_url("warehouses/"), headers)
        data, error = _parse_response(resp)
        if error:
            return None, error
        warehouses = sorted(
            w["id"] for w in (data.get("results", data) if isinstance(data, dict) else data)
            if w["type"] == "warehouse" and w["is_active"]
        )
        available = {}
        for item in items:
            stock, error = _parse_response(_get(inventory_url("stock/"), headers, {"product_id": item["product_id"]}))
            if error:
                return None, error
            for row in stock:
                available[(row["warehouse"], row["product_id"])] = row["quantity"] - row["reserved"]
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        logger.error("pick_fulfilment_warehouse failed: %s", e)
        return None, str(e)
    wanted = {}
    for item in items:
        wanted[item["product_id"]] = wanted.get(item["product_id"], 0) + item["quantity"]
    for wid in warehouses:
        if all(available.get((wid, pid), 0) >= qty for pid, qty in wanted.items()):
            return wid, None
    return None, None


def check_availability(product_id, warehouse_id, quantity, request=None):
    """Pre-check if stock is available before order creation."""
    url = inventory_url("stock/")
    headers = _build_headers(request)
    params = {"product_id": product_id, "warehouse_id": warehouse_id}
    try:
        resp = _get(url, headers, params)
        data, error = _parse_response(resp)
        if error:
            return False, error
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        elif isinstance(data, list):
            results = data
        else:
            results = [data]
        for stock in results:
            if int(stock.get("product_id", 0)) == product_id:
                available = int(stock.get("quantity", 0)) - int(stock.get("reserved", 0))
                if available >= quantity:
                    return True, None
                return False, f"Insufficient stock: {available} available, {quantity} requested"
        return False, f"No stock record for product {product_id} at warehouse {warehouse_id}"
    except requests.RequestException as e:
        logger.error("check_availability failed: product=%s error=%s", product_id, e)
        return None, str(e)


def get_stock_valuation(request=None):
    """On-hand stock value at weighted-average cost. Returns (data, error)."""
    try:
        return _parse_response(_get(inventory_url("stock/valuation/"), _build_headers(request)))
    except requests.RequestException as e:
        logger.error("get_stock_valuation failed: %s", e)
        return None, str(e)
