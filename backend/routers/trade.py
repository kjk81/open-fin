import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class TradeRequest(BaseModel):
    action: str = Field(..., pattern="^(BUY|SELL)$")
    ticker: str = Field(..., pattern="^[A-Z]{1,10}$")
    qty: int = Field(..., gt=0, le=1000000)


@router.post("/execute_trade")
def execute_trade(req: TradeRequest):
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="Alpaca credentials not configured")

    try:
        import alpaca_trade_api as tradeapi

        api = tradeapi.REST(
            key_id=api_key,
            secret_key=api_secret,
            base_url=base_url,
        )

        order = api.submit_order(
            symbol=req.ticker.upper(),
            qty=req.qty,
            side=req.action.lower(),  # Alpaca expects "buy" / "sell"
            type="market",
            time_in_force="day",
        )

        logger.info(
            "Trade executed: %s %d %s -> order %s",
            req.action,
            req.qty,
            req.ticker,
            order.id,
        )

        return {
            "success": True,
            "order_id": order.id,
            "symbol": order.symbol,
            "qty": int(float(order.qty)),
            "side": order.side,
            "status": order.status,
        }

    except Exception as exc:
        logger.error("Trade execution failed: %s", exc)
        raise HTTPException(status_code=400, detail="Trade execution failed")
