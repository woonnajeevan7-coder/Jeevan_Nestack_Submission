import os
import hmac
import hashlib
import logging
from fastapi import APIRouter, Request, Response, Header, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mock-webhook"])

@router.post("/mock-webhook")
async def mock_webhook_receiver(
    request: Request,
    response: Response,
    x_webhook_signature: str = Header(None, alias="X-Webhook-Signature"),
    status_code: int = 200
):
    """
    Mock webhook receiver endpoint for testing.
    Verifies the incoming X-Webhook-Signature header using HMAC-SHA256 and the shared secret.
    Allows testing success/failure delivery outcomes by passing a 'status_code' query parameter.
    """
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    
    secret = os.getenv("WEBHOOK_SECRET", "nestack-secret-key")
    
    # Calculate HMAC signature using raw bytes
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    
    logger.info(f"Mock Webhook received a request:")
    logger.info(f"  URL: {request.url}")
    logger.info(f"  X-Webhook-Signature (Received): {x_webhook_signature}")
    logger.info(f"  X-Webhook-Signature (Expected): {expected_signature}")
    logger.info(f"  Raw Body: {body_str}")

    # Enforce HMAC signature check
    if not x_webhook_signature:
        logger.error("Signature verification failed: Missing X-Webhook-Signature header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Webhook-Signature header"
        )
        
    if not hmac.compare_digest(x_webhook_signature, expected_signature):
        logger.error("Signature verification failed: Hashes do not match")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HMAC signature verification failed"
        )
    
    # Respond with configurable status code
    response.status_code = status_code
    
    try:
        json_payload = await request.json()
    except Exception:
        json_payload = None

    return {
        "status": "success",
        "message": "Signature verified successfully",
        "returned_status_code": status_code,
        "payload": json_payload
    }
