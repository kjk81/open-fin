import os
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import UserPortfolio

logger = logging.getLogger(__name__)
router = APIRouter()


def sync_alpaca_portfolio(db: Session) -> None:
    """Fetch paper positions from Alpaca and upsert into UserPortfolio."""
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not api_secret:
        logger.warning("Alpaca credentials not set — skipping portfolio sync.")
        return

    try:
        import alpaca_trade_api as tradeapi

        api = tradeapi.REST(
            key_id=api_key,
            secret_key=api_secret,
            base_url=base_url,
        )
        positions = api.list_positions()
        logger.info("Alpaca sync: fetched %d positions", len(positions))

        # Clear and re-insert
        db.query(UserPortfolio).delete()
        for pos in positions:
            db.add(
                UserPortfolio(
                    symbol=pos.symbol,
                    qty=float(pos.qty),
                    avg_entry_price=float(pos.avg_entry_price),
                    current_price=float(pos.current_price),
                    synced_at=datetime.utcnow(),
                )
            )
        db.commit()
        logger.info("Alpaca sync: portfolio saved to DB.")
    except Exception as exc:
        logger.warning("Alpaca sync failed: %s", exc)
        db.rollback()


@router.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)):
    positions = db.query(UserPortfolio).all()
    return [
        {
            "symbol": p.symbol,
            "qty": p.qty,
            "avg_entry_price": p.avg_entry_price,
            "current_price": p.current_price,
            "market_value": round(p.qty * p.current_price, 2),
            "synced_at": p.synced_at.isoformat() if p.synced_at else None,
        }
        for p in positions
    ]


@router.post("/sync-portfolio")
def trigger_sync(db: Session = Depends(get_db)):
    sync_alpaca_portfolio(db)
    count = db.query(UserPortfolio).count()
    return {"synced": True, "positions": count}
