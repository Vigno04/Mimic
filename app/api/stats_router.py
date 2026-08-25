import datetime
import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query
from sqlalchemy import select, func, desc, or_
from app.database.models import (
    AuditLogModel,
    ChatMessageModel,
    BotModel,
    DiscordUserModel,
    EndpointModel
)
from app.database.session import AsyncSessionLocal
from app.database.queries import get_dashboard_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
@router.get("/")
async def get_stats():
    return await get_dashboard_stats()


def _parse_range(range_str: str) -> tuple[Optional[datetime.datetime], Optional[int], str]:
    """Convert range string like '24h','7d','30d','90d','365d','all' to (cutoff_dt, days, mode)."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if not range_str or range_str == "all":
        return None, None, "all"
    if range_str in ("24h", "1d"):
        return now - datetime.timedelta(hours=24), 1, "24h"
    try:
        days = int(range_str.rstrip("d"))
        return now - datetime.timedelta(days=days), days, "days"
    except ValueError:
        return None, None, "all"


@router.get("/timeseries")
async def get_timeseries_stats(
    time_range: str = Query("30d", alias="range", description="Time range: 24h, 7d, 30d, 90d, 365d, all"),
    bot_id: Optional[str] = Query(None, description="Filter by bot ID"),
):
    """Returns continuous time-series data, tool analytics, and user leaderboards."""
    cutoff, num_days, mode = _parse_range(time_range)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    async with AsyncSessionLocal() as session:
        # ── Base filters ──
        def _audit_filter(stmt):
            if cutoff:
                stmt = stmt.where(AuditLogModel.timestamp >= cutoff)
            if bot_id and isinstance(bot_id, str) and bot_id.strip():
                stmt = stmt.where(AuditLogModel.bot_id == bot_id.strip())
            return stmt

        def _msg_filter(stmt):
            if cutoff:
                stmt = stmt.where(ChatMessageModel.timestamp >= cutoff)
            return stmt

        # ── 1. Continuous Date Range Generation ──
        date_keys = []
        if mode == "24h":
            # Generate 24 hourly buckets: YYYY-MM-DD HH:00
            for i in range(24):
                dt = (now - datetime.timedelta(hours=23 - i)).replace(minute=0, second=0, microsecond=0)
                date_keys.append(dt.strftime("%Y-%m-%d %H:00"))
        elif mode == "days" and num_days is not None and num_days <= 90:
            for i in range(num_days + 1):
                dt = (now - datetime.timedelta(days=num_days - i)).date()
                date_keys.append(dt.strftime("%Y-%m-%d"))

        # ── 2. Requests per period ──
        if mode == "24h":
            period_expr = func.strftime("%Y-%m-%d %H:00", AuditLogModel.timestamp)
        else:
            period_expr = func.strftime("%Y-%m-%d", AuditLogModel.timestamp)

        stmt = _audit_filter(
            select(
                period_expr.label("period"),
                func.count(AuditLogModel.id).label("requests"),
                func.sum(AuditLogModel.total_tokens).label("tokens"),
                func.sum(AuditLogModel.prompt_tokens).label("prompt_tokens"),
                func.sum(AuditLogModel.completion_tokens).label("completion_tokens"),
                func.count(func.nullif(AuditLogModel.refused, False)).label("refused"),
            ).group_by(period_expr).order_by(period_expr)
        )
        rows = (await session.execute(stmt)).all()
        rpd_map = {
            r.period: {
                "day": r.period,
                "requests": int(r.requests or 0),
                "tokens": int(r.tokens or 0),
                "prompt_tokens": int(r.prompt_tokens or 0),
                "completion_tokens": int(r.completion_tokens or 0),
                "refused": int(r.refused or 0),
            }
            for r in rows
        }

        # If we have predefined continuous keys, fill missing with 0
        if date_keys:
            requests_per_day = [
                rpd_map.get(
                    k,
                    {
                        "day": k,
                        "requests": 0,
                        "tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "refused": 0,
                    },
                )
                for k in date_keys
            ]
        else:
            requests_per_day = list(rpd_map.values())

        # ── 3. Tokens per day by model ──
        stmt = _audit_filter(
            select(
                period_expr.label("period"),
                AuditLogModel.model_used,
                func.sum(AuditLogModel.total_tokens).label("tokens"),
                func.count(AuditLogModel.id).label("requests"),
            )
            .group_by(period_expr, AuditLogModel.model_used)
            .order_by(period_expr)
        )
        rows = (await session.execute(stmt)).all()
        raw_tokens_by_model: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for r in rows:
            model = r.model_used or "unknown"
            if model not in raw_tokens_by_model:
                raw_tokens_by_model[model] = {}
            raw_tokens_by_model[model][r.period] = {
                "day": r.period,
                "tokens": int(r.tokens or 0),
                "requests": int(r.requests or 0),
            }

        tokens_by_model_day: Dict[str, List[Dict[str, Any]]] = {}
        for model, day_dict in raw_tokens_by_model.items():
            if date_keys:
                tokens_by_model_day[model] = [
                    day_dict.get(k, {"day": k, "tokens": 0, "requests": 0})
                    for k in date_keys
                ]
            else:
                tokens_by_model_day[model] = list(day_dict.values())

        # ── 4. Messages per day ──
        if mode == "24h":
            msg_period_expr = func.strftime("%Y-%m-%d %H:00", ChatMessageModel.timestamp)
        else:
            msg_period_expr = func.strftime("%Y-%m-%d", ChatMessageModel.timestamp)

        stmt = _msg_filter(
            select(
                msg_period_expr.label("period"),
                func.count(ChatMessageModel.id).label("messages"),
            )
            .group_by(msg_period_expr)
            .order_by(msg_period_expr)
        )
        rows = (await session.execute(stmt)).all()
        msg_map = {r.period: int(r.messages or 0) for r in rows}
        if date_keys:
            messages_per_day = [{"day": k, "messages": msg_map.get(k, 0)} for k in date_keys]
        else:
            messages_per_day = [{"day": k, "messages": v} for k, v in msg_map.items()]

        # ── 5. User activity daily (top 6 users) ──
        top_users_stmt = _msg_filter(
            select(
                ChatMessageModel.author_name,
                func.count(ChatMessageModel.id).label("cnt"),
            )
            .group_by(ChatMessageModel.author_name)
            .order_by(desc(func.count(ChatMessageModel.id)))
            .limit(6)
        )
        top_user_rows = (await session.execute(top_users_stmt)).all()
        top_user_names = [r.author_name for r in top_user_rows if r.author_name]

        user_daily: Dict[str, List[Dict[str, Any]]] = {}
        if top_user_names:
            stmt = _msg_filter(
                select(
                    msg_period_expr.label("period"),
                    ChatMessageModel.author_name,
                    func.count(ChatMessageModel.id).label("messages"),
                )
                .where(ChatMessageModel.author_name.in_(top_user_names))
                .group_by(msg_period_expr, ChatMessageModel.author_name)
                .order_by(msg_period_expr)
            )
            rows = (await session.execute(stmt)).all()
            raw_user_daily: Dict[str, Dict[str, int]] = {}
            for r in rows:
                author = r.author_name or "unknown"
                if author not in raw_user_daily:
                    raw_user_daily[author] = {}
                raw_user_daily[author][r.period] = int(r.messages or 0)

            for u in top_user_names:
                ud = raw_user_daily.get(u, {})
                if date_keys:
                    user_daily[u] = [{"day": k, "messages": ud.get(k, 0)} for k in date_keys]
                else:
                    user_daily[u] = [{"day": k, "messages": v} for k, v in ud.items()]

        # ── 6. Hourly distribution (24-hour aggregate) ──
        hour_expr = func.strftime("%H", AuditLogModel.timestamp)
        stmt = _audit_filter(
            select(
                hour_expr.label("hour"),
                func.count(AuditLogModel.id).label("requests"),
                func.sum(AuditLogModel.total_tokens).label("tokens"),
            )
            .group_by(hour_expr)
            .order_by(hour_expr)
        )
        rows = (await session.execute(stmt)).all()
        hourly_map = {int(r.hour): {"requests": int(r.requests or 0), "tokens": int(r.tokens or 0)} for r in rows if r.hour is not None}
        hourly_distribution = [
            {
                "hour": h,
                "label": f"{h:02d}:00",
                "requests": hourly_map.get(h, {}).get("requests", 0),
                "tokens": hourly_map.get(h, {}).get("tokens", 0),
            }
            for h in range(24)
        ]

        # ── 7. Per-bot totals ──
        stmt = select(
            AuditLogModel.bot_id,
            func.count(AuditLogModel.id).label("requests"),
            func.sum(AuditLogModel.total_tokens).label("tokens"),
            func.count(func.nullif(AuditLogModel.refused, False)).label("refused"),
        ).group_by(AuditLogModel.bot_id)
        if cutoff:
            stmt = stmt.where(AuditLogModel.timestamp >= cutoff)
        rows = (await session.execute(stmt)).all()

        bots_stmt = select(BotModel.id, BotModel.name)
        bots_map = {r.id: r.name for r in (await session.execute(bots_stmt)).all()}
        per_bot = [
            {
                "bot_id": r.bot_id,
                "bot_name": bots_map.get(r.bot_id, r.bot_id or "Unknown Bot"),
                "requests": int(r.requests or 0),
                "tokens": int(r.tokens or 0),
                "refused": int(r.refused or 0),
            }
            for r in rows
        ]

        # ── 8. Model breakdown totals ──
        stmt = _audit_filter(
            select(
                AuditLogModel.model_used,
                func.count(AuditLogModel.id).label("requests"),
                func.sum(AuditLogModel.total_tokens).label("tokens"),
                func.sum(AuditLogModel.prompt_tokens).label("prompt_tokens"),
                func.sum(AuditLogModel.completion_tokens).label("completion_tokens"),
                func.count(func.nullif(AuditLogModel.refused, False)).label("refused"),
            ).group_by(AuditLogModel.model_used).order_by(desc(func.count(AuditLogModel.id)))
        )
        rows = (await session.execute(stmt)).all()
        model_totals = [
            {
                "model": r.model_used or "unknown",
                "requests": int(r.requests or 0),
                "tokens": int(r.tokens or 0),
                "prompt_tokens": int(r.prompt_tokens or 0),
                "completion_tokens": int(r.completion_tokens or 0),
                "refused": int(r.refused or 0),
                "avg_tokens": round(int(r.tokens or 0) / max(1, int(r.requests or 0))),
            }
            for r in rows
        ]

        # ── 9. Tool Calls Analytics ──
        tool_audits_stmt = _audit_filter(
            select(AuditLogModel.tools_called).where(AuditLogModel.tools_called.isnot(None))
        )
        tool_rows = (await session.execute(tool_audits_stmt)).scalars().all()
        tool_counts: Dict[str, int] = {}
        for tc in tool_rows:
            if not tc:
                continue
            try:
                tools = json.loads(tc)
                if isinstance(tools, list):
                    for t in tools:
                        name = t.get("name") if isinstance(t, dict) else str(t)
                        if name:
                            tool_counts[name] = tool_counts.get(name, 0) + 1
            except Exception:
                pass

        tool_totals = [
            {"tool": k, "count": v}
            for k, v in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        # ── 10. Top users with Discord User resolution ──
        top_chat_users_stmt = _msg_filter(
            select(
                ChatMessageModel.author_id,
                ChatMessageModel.author_name,
                func.count(ChatMessageModel.id).label("cnt"),
            )
            .group_by(ChatMessageModel.author_id, ChatMessageModel.author_name)
            .order_by(desc(func.count(ChatMessageModel.id)))
            .limit(10)
        )
        top_user_recs = (await session.execute(top_chat_users_stmt)).all()

        user_ids = [r.author_id for r in top_user_recs if r.author_id]
        discord_users_map: Dict[str, DiscordUserModel] = {}
        if user_ids:
            du_stmt = select(DiscordUserModel).where(DiscordUserModel.id.in_(user_ids))
            for du in (await session.execute(du_stmt)).scalars().all():
                discord_users_map[du.id] = du

        top_users = []
        for r in top_user_recs:
            du = discord_users_map.get(r.author_id)
            top_users.append({
                "user_id": r.author_id,
                "author": r.author_name,
                "display_name": du.display_name if du and du.display_name else r.author_name,
                "username": du.username if du and du.username else r.author_name,
                "avatar_url": du.avatar_url if du and du.avatar_url else None,
                "is_bot": du.is_bot if du else False,
                "messages": int(r.cnt or 0),
            })

        # ── 11. Bots list for selector ──
        all_bots = [
            {"id": r.id, "name": r.name}
            for r in (await session.execute(select(BotModel.id, BotModel.name))).all()
        ]

        # ── 12. Summary Aggregates ──
        total_requests = sum(r["requests"] for r in requests_per_day)
        total_tokens = sum(r["tokens"] for r in requests_per_day)
        prompt_tokens = sum(r["prompt_tokens"] for r in requests_per_day)
        completion_tokens = sum(r["completion_tokens"] for r in requests_per_day)
        refused_count = sum(r["refused"] for r in requests_per_day)
        total_messages = sum(m["messages"] for m in messages_per_day)
        success_rate = (
            round(100.0 * (total_requests - refused_count) / max(1, total_requests), 1)
            if total_requests > 0
            else 100.0
        )
        avg_tokens_per_req = (
            round(total_tokens / max(1, total_requests)) if total_requests > 0 else 0
        )

        return {
            "range": time_range,
            "bot_id": bot_id,
            "summary": {
                "total_requests": total_requests,
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "refused_count": refused_count,
                "success_rate_pct": success_rate,
                "total_messages": total_messages,
                "avg_tokens_per_req": avg_tokens_per_req,
                "total_tools_called": sum(tool_counts.values()),
            },
            "requests_per_day": requests_per_day,
            "tokens_by_model_day": tokens_by_model_day,
            "messages_per_day": messages_per_day,
            "user_daily": user_daily,
            "hourly_distribution": hourly_distribution,
            "per_bot": per_bot,
            "model_totals": model_totals,
            "tool_totals": tool_totals,
            "top_users": top_users,
            "bots": all_bots,
        }
