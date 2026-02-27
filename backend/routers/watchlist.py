from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Watchlist

router = APIRouter()


@router.get("/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    items = db.query(Watchlist).order_by(Watchlist.added_at.desc()).all()
    return [{"id": w.id, "ticker": w.ticker, "added_at": w.added_at.isoformat()} for w in items]


@router.post("/watchlist/{ticker}", status_code=201)
def add_to_watchlist(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    existing = db.query(Watchlist).filter(Watchlist.ticker == ticker).first()
    if existing:
        return {"id": existing.id, "ticker": existing.ticker, "added_at": existing.added_at.isoformat()}
    item = Watchlist(ticker=ticker)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "ticker": item.ticker, "added_at": item.added_at.isoformat()}


@router.delete("/watchlist/{ticker}", status_code=204)
def remove_from_watchlist(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    item = db.query(Watchlist).filter(Watchlist.ticker == ticker).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"{ticker} not in watchlist")
    db.delete(item)
    db.commit()
