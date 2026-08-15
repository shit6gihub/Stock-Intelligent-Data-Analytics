"""操作审计 API(2026-08-15 阶段3): owner 视角审计日志。

- log_audit(db, user, action, detail, ip): 通用写审计入口, 供 auth/个人中心/导出等模块调用
- GET /api/audit?limit=200: 最近审计日志(仅 owner; 按时间倒序)

路由挂载约定: app.py 中 include_router(audit.router, prefix="/api/audit", tags=["audit"]),
本文件路由路径为空串, 挂载后即 /api/audit。

循环依赖说明: 本文件模块级 import auth.require_owner; auth.py 内对 log_audit 使用
函数内延迟 import —— 两侧不同时模块级互引, 任意导入顺序均安全。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.web.models import AuditLog
from src.web.api.auth import require_owner

router = APIRouter()


def log_audit(db: Session, user, action: str, detail: str = "", ip: str = "") -> AuditLog:
    """写入一条审计日志(关键写操作: 登录/注册/修改资料/改密/用户管理/配置修改/导出等)。

    user 可为 User 对象或 None(系统任务); username 取 user.username。
    """
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "",
        action=action,
        detail=detail or "",
        ip=ip or "",
    )
    db.add(entry)
    db.commit()
    return entry


@router.get("")
def list_audit(
    limit: int = Query(200, ge=1, le=1000),
    owner=Depends(require_owner),
    db: Session = Depends(get_db),
):
    """最近审计日志(仅 owner): 按时间倒序, 默认最近 200 条。"""
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "logs": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "action": r.action,
                "detail": r.detail,
                "ip": r.ip,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }
