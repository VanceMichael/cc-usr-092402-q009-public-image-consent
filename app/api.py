"""HTTP 边界：请求解析、结构化错误响应与路由注册。业务规则全部在 service/domain。"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, g, jsonify, request

from . import domain, service, store

api = Blueprint("api", __name__)


# ------------------------------------------------------------ 应用生命周期

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = store.connect()
    return g.db


def init_app(app) -> None:
    with app.app_context():
        conn = store.connect()
        store.migrate(conn)
        conn.close()
    app.register_blueprint(api)
    app.teardown_appcontext(_close_db)


def _close_db(_exc) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ------------------------------------------------------------ 错误处理

@api.errorhandler(domain.DomainError)
def _domain_error(exc: domain.DomainError):
    return jsonify({"error": "invalid_request", "message": str(exc)}), 400


@api.errorhandler(service.ConflictError)
def _conflict_error(exc: service.ConflictError):
    return jsonify({"error": "conflict", "message": str(exc)}), 409


@api.errorhandler(service.NotFoundError)
def _not_found_error(exc: service.NotFoundError):
    return jsonify({"error": "not_found", "message": str(exc)}), 404


def _body() -> dict:
    if not request.is_json:
        raise domain.DomainError("请求体必须是 application/json")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise domain.DomainError("请求体必须是 JSON 对象")
    return data


def _require_fields(body: dict, fields: tuple[str, ...]) -> None:
    missing = [f for f in fields if body.get(f) in (None, "")]
    if missing:
        raise domain.DomainError("缺少必填字段：" + ", ".join(missing))


def _created(payload: dict) -> tuple:
    return jsonify(payload), 201


# ------------------------------------------------------------ 录入接口

@api.post("/admin/works")
def create_work():
    body = _body()
    _require_fields(body, ("work_ref", "author_ref", "source_digest", "recorded_at"))
    return _created(service.register_work(get_db(), body))


@api.post("/admin/sessions")
def create_session():
    body = _body()
    _require_fields(body, ("session_ref", "work_ref", "shot_at"))
    return _created(service.register_session(get_db(), body))


@api.post("/admin/persons")
def create_person():
    body = _body()
    _require_fields(body, ("person_ref",))
    if "is_minor" not in body:
        raise domain.DomainError("缺少必填字段：is_minor")
    return _created(service.register_person(get_db(), body))


@api.post("/admin/claims")
def create_claim():
    body = _body()
    _require_fields(body, ("work_ref", "person_ref", "role", "declared_by"))
    return _created(service.register_claim(get_db(), body))


@api.post("/admin/guardianships")
def create_guardianship():
    body = _body()
    _require_fields(body, ("child_ref", "guardian_ref", "relation",
                           "evidence_digest", "valid_from"))
    return _created(service.register_guardianship(get_db(), body))


@api.post("/admin/grants")
def create_grant():
    body = _body()
    _require_fields(body, ("person_ref", "purposes", "channels", "regions",
                           "valid_from", "granted_by"))
    return _created(service.register_grant(get_db(), body))


# ------------------------------------------------------------ 版本派生

@api.post("/admin/versions")
def create_version():
    body = _body()
    _require_fields(body, ("kind", "digest", "created_by"))
    return _created(service.create_version(get_db(), body))


# ------------------------------------------------------------ 方案 / 审批 / 发布

@api.post("/admin/proposals")
def submit_proposal():
    body = _body()
    _require_fields(body, ("version_id", "purpose", "channel", "region",
                           "use_from", "submitted_by"))
    return _created(service.submit_proposal(get_db(), body))


@api.post("/admin/proposals/<proposal_id>/revisions")
def revise_proposal(proposal_id: str):
    body = _body()
    _require_fields(body, ("editor_ref", "patch"))
    return jsonify(service.revise_proposal(get_db(), proposal_id, body))


@api.post("/admin/proposals/<proposal_id>/decisions")
def decide_proposal(proposal_id: str):
    body = _body()
    _require_fields(body, ("reviewer_ref", "decision"))
    return jsonify(service.decide_proposal(get_db(), proposal_id, body))


@api.get("/admin/proposals/<proposal_id>/chain")
def proposal_chain(proposal_id: str):
    return jsonify(service.approval_chain(get_db(), proposal_id))


@api.post("/admin/publications")
def publish():
    body = _body()
    _require_fields(body, ("proposal_id",))
    return _created(service.publish(get_db(), body))


# ------------------------------------------------------------ 撤回 / 处置

@api.post("/admin/grants/<grant_id>/revocations")
def revoke_grant(grant_id: str):
    body = _body()
    _require_fields(body, ("revoked_at",))
    return jsonify(service.revoke_grant(get_db(), grant_id, body))


@api.get("/admin/dispositions")
def list_dispositions():
    return jsonify({"items": store.open_dispositions(get_db())})


@api.post("/admin/dispositions/<item_id>/handle")
def handle_disposition(item_id: str):
    return jsonify(service.handle_disposition(get_db(), item_id, _body()))


# ------------------------------------------------------------ 核验 / 证据

@api.get("/verify")
def external_verify():
    """对外核验：只回答该版本对指定用途/渠道/地域在某时点是否有效。"""
    args = request.args
    for key in ("version_id", "purpose", "channel", "region"):
        if not args.get(key):
            raise domain.DomainError(f"缺少查询参数：{key}")
    at_raw = args.get("as_of") or domain.now_iso()
    at = domain.parse_time(at_raw, field_name="as_of")
    result = service.external_verify(
        get_db(),
        version_id=args["version_id"], purpose=args["purpose"],
        channel=args["channel"], region=args["region"], at=at,
    )
    return jsonify(result)


@api.get("/admin/publications/<publication_id>/evidence")
def publication_evidence(publication_id: str):
    """内部重放：发布时影像摘要、授权范围、遮蔽处理与批准链。"""
    return jsonify(service.publication_evidence(get_db(), publication_id))
