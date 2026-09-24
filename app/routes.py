
"""HTTP 路由：登记、版本派生、方案审批、发布、对外核验、内部追溯。"""

from flask import Blueprint, jsonify, request

from . import queries, service
from .db import get_db
from .errors import ApiError, bad_request
from .evaluation import evaluate

api = Blueprint("api", __name__, url_prefix="/v1")


def _body() -> dict:
    if not request.is_json:
        raise bad_request("请求体必须是 application/json")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise bad_request("请求体必须是 JSON 对象")
    return data


# ---------------------------------------------------------------- 登记接口

_REGISTRY = {
    "sessions": service.register_session,
    "works": service.register_work,
    "persons": service.register_person,
    "recognitions": service.register_recognition,
    "grants": service.register_grant,
}


@api.post("/registry/<resource>")
def register(resource: str):
    handler = _REGISTRY.get(resource)
    if handler is None:
        raise ApiError("not_found", f"未知登记资源：{resource}", 404)
    result = handler(get_db(), _body())
    return jsonify(result), 201


# ---------------------------------------------------------------- 版本派生

@api.post("/versions")
def create_version():
    return jsonify(service.create_version(get_db(), _body())), 201


@api.get("/versions/<version_ref>")
def get_version(version_ref: str):
    return jsonify(queries.version_summary(get_db(), version_ref))


# ---------------------------------------------------------------- 方案与审批

@api.post("/plans")
def submit_plan():
    return jsonify(service.submit_plan(get_db(), _body())), 201


@api.post("/plans/decide")
def decide_plan():
    return jsonify(service.decide_plan(get_db(), _body()))


@api.post("/publications")
def publish():
    return jsonify(service.publish(get_db(), _body())), 201


# ---------------------------------------------------------------- 撤回

@api.post("/withdrawals")
def withdraw():
    return jsonify(service.withdraw(get_db(), _body())), 201


@api.get("/dispositions")
def dispositions():
    withdrawal_ref = request.args.get("withdrawal_ref")
    return jsonify({"items": queries.disposition_list(get_db(), withdrawal_ref)})


# ---------------------------------------------------------------- 对外核验

@api.post("/verify")
def verify():
    """对外核验：只返回该版本对指定用途是否有效，不暴露人物/授权细节。"""
    payload = _body()
    for field in ("version_ref", "purpose", "channel", "territory"):
        if payload.get(field) in (None, ""):
            raise bad_request("缺少必填字段", {"missing": [field]})
    db = get_db()
    at = payload.get("at")
    if at is None:
        from .timeutil import now
        at = now()
    verdict = evaluate(
        db, payload["version_ref"], payload["purpose"],
        payload["channel"], payload["territory"], at,
    )
    return jsonify({
        "version_ref": payload["version_ref"],
        "purpose": payload["purpose"],
        "channel": payload["channel"],
        "territory": payload["territory"],
        "at": at,
        "valid": verdict.valid,
    })


# ---------------------------------------------------------------- 内部追溯

@api.post("/internal/evaluate")
def internal_evaluate():
    """内部：返回逐人裁决明细，含为何失败。"""
    payload = _body()
    for field in ("version_ref", "purpose", "channel", "territory", "at"):
        if payload.get(field) in (None, ""):
            raise bad_request("缺少必填字段", {"missing": [field]})
    verdict = evaluate(
        get_db(), payload["version_ref"], payload["purpose"],
        payload["channel"], payload["territory"], payload["at"],
    )
    return jsonify(verdict.as_dict())


@api.get("/internal/publications/<publication_ref>")
def internal_replay(publication_ref: str):
    """内部：重现任一发布时间采用的影像摘要、授权范围、遮蔽处理和批准链。"""
    return jsonify(queries.replay_publication(get_db(), publication_ref))
