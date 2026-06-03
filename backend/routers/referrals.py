"""
Real referral system with:
- Milestone rewards (50 → yearly Profi free, 100 → lifetime Profi free)
- Anti-cheat: referrals must pay + survive 30 days
- Account soft-delete
"""
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.notification import Notification
from models.referral_record import ReferralRecord
from models.user import User

log = logging.getLogger(__name__)
router = APIRouter()

MILESTONE_50  = 50
MILESTONE_100 = 100
VALIDATION_DAYS = 30
DISCOUNT_PER_REFERRAL = 5   # %
_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen_code() -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(8))


async def _ensure_code(user: User, db: AsyncSession) -> str:
    if not user.referral_code:
        while True:
            code = _gen_code()
            dup = await db.execute(select(User).where(User.referral_code == code))
            if not dup.scalars().first():
                break
        user.referral_code = code
        await db.commit()
    return user.referral_code


async def _revalidate_records(referrer_id: str, db: AsyncSession) -> int:
    """Re-check which records qualify as valid, return new valid count."""
    q = await db.execute(
        select(ReferralRecord).where(ReferralRecord.referrer_id == referrer_id)
    )
    records = q.scalars().all()
    now = datetime.utcnow()

    for r in records:
        if r.is_valid or r.invalidated_at:
            continue
        if not r.is_paid:
            continue
        age_days = (now - r.joined_at).days
        if age_days >= VALIDATION_DAYS:
            r.is_valid = True

    await db.commit()
    return sum(1 for r in records if r.is_valid)


async def _check_milestones(referrer: User, valid_count: int, db: AsyncSession) -> None:
    """Upgrade plan and send one-time notification when milestones are crossed."""
    if valid_count >= MILESTONE_100:
        referrer.plan = 'profi'
        notif_type = "referral_milestone_100"
        title = "🏆 Пожизненная подписка разблокирована!"
        msg   = "Вы привлекли 100 оплативших рефералов. Тариф «Профи» активирован бессрочно."
    elif valid_count >= MILESTONE_50:
        referrer.plan = 'profi'
        notif_type = "referral_milestone_50"
        title = "🎉 Годовая подписка разблокирована!"
        msg   = "Вы привлекли 50 оплативших рефералов. Тариф «Профи» активирован на 1 год бесплатно."
    else:
        await db.commit()
        return

    dup = await db.execute(
        select(Notification).where(
            Notification.user_id == referrer.id,
            Notification.type    == notif_type,
        )
    )
    if not dup.scalars().first():
        db.add(Notification(
            user_id=referrer.id,
            type=notif_type,
            title=title,
            message=msg,
        ))
        log.info("%s: user=%s valid=%d", notif_type, referrer.id, valid_count)
    await db.commit()


# ── Public endpoints ───────────────────────────────────────────────────────────

@router.get("/referrals/me")
async def get_my_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    code = await _ensure_code(current_user, db)
    valid_count = await _revalidate_records(current_user.id, db)
    await _check_milestones(current_user, valid_count, db)

    q = await db.execute(
        select(ReferralRecord).where(ReferralRecord.referrer_id == current_user.id)
        .order_by(ReferralRecord.joined_at.desc())
    )
    records = q.scalars().all()

    total_invited = len(records)
    total_paid    = sum(1 for r in records if r.is_paid)
    total_pending = sum(1 for r in records if r.is_paid and not r.is_valid and not r.invalidated_at)

    milestone = None
    if valid_count >= MILESTONE_100:
        milestone = "lifetime"
    elif valid_count >= MILESTONE_50:
        milestone = "yearly"

    discount_percent = min(100, valid_count * DISCOUNT_PER_REFERRAL)

    referred_by_email: Optional[str] = None
    if current_user.referred_by_id:
        rb = await db.get(User, current_user.referred_by_id)
        referred_by_email = rb.email if rb else None

    return {
        "referral_code":            code,
        "total_invited":            total_invited,
        "total_paid":               total_paid,
        "total_valid":              valid_count,
        "total_pending_validation": total_pending,
        "discount_percent":         discount_percent,
        "referred_by_email":        referred_by_email,
        "milestone":                milestone,
        "milestone_50_progress":    min(valid_count, MILESTONE_50),
        "milestone_100_progress":   min(valid_count, MILESTONE_100),
    }


@router.get("/referrals/invitees")
async def get_invitees(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = await db.execute(
        select(ReferralRecord).where(ReferralRecord.referrer_id == current_user.id)
        .order_by(ReferralRecord.joined_at.desc())
    )
    records = q.scalars().all()
    now = datetime.utcnow()

    result = []
    for r in records:
        invitee = await db.get(User, r.invitee_id)
        age_days = (now - r.joined_at).days
        validation_days_left = max(0, VALIDATION_DAYS - age_days)

        result.append({
            "id":                   r.invitee_id,
            "email":                invitee.email if invitee else "—",
            "joined_at":            r.joined_at.isoformat(),
            "has_paid":             r.is_paid,
            "paid_at":              r.paid_at.isoformat() if r.paid_at else None,
            "is_valid":             r.is_valid,
            "validation_days_left": validation_days_left if r.is_paid and not r.is_valid and not r.invalidated_at else 0,
            "invalidated":          r.invalidated_at is not None,
            "invalidation_reason":  r.invalidation_reason,
        })
    return result


@router.post("/referrals/mark-paid/{invitee_id}")
async def mark_referral_paid(
    invitee_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stub: called when a referred user completes payment. In prod triggered by payment webhook."""
    q = await db.execute(
        select(ReferralRecord).where(
            ReferralRecord.referrer_id == current_user.id,
            ReferralRecord.invitee_id  == invitee_id,
        )
    )
    record = q.scalars().first()
    if not record:
        raise HTTPException(404, "Referral record not found")
    if not record.is_paid:
        record.is_paid  = True
        record.paid_at  = datetime.utcnow()
    await db.commit()
    return {"ok": True}


# ── Account soft-delete ────────────────────────────────────────────────────────

@router.delete("/account")
async def delete_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.utcnow()

    # Check if this user was referred within the last 30 days → invalidate referrer's record
    if current_user.referred_by_id:
        q = await db.execute(
            select(ReferralRecord).where(ReferralRecord.invitee_id == current_user.id)
        )
        rec = q.scalars().first()
        if rec and not rec.is_valid and not rec.invalidated_at:
            age_days = (now - rec.joined_at).days
            if age_days < VALIDATION_DAYS:
                rec.invalidated_at      = now
                rec.invalidation_reason = "Аккаунт удалён до истечения 30 дней"
                log.info("referral_invalidated: invitee=%s referrer=%s age=%dd",
                         current_user.id, rec.referrer_id, age_days)

    # Soft delete
    q_has_referrals = await db.execute(
        select(ReferralRecord).where(ReferralRecord.referrer_id == current_user.id).limit(1)
    )
    current_user.deleted_at   = now
    current_user.was_referrer = q_has_referrals.scalars().first() is not None
    current_user.was_referred = current_user.referred_by_id is not None

    await db.commit()
    log.info("account_deleted: user=%s", current_user.id)
    return {"ok": True, "message": "Аккаунт помечен как удалённый. Email может быть повторно использован."}
