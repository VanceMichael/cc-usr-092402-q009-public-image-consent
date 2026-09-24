
"""命令与登记服务：写入业务事实、生成版本、审批与发布证据、撤回处置。"""

import json
import re

from . import timeutil
from .errors import bad_request, conflict, not_found, unprocessable
from .evaluation import evaluate
from .versions import derive_persons

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_KINDS = ("original", "crop", "mask", "composite", "subtitle")
PERSON_KINDS = ("visitor", "extra", "staff", "minor")


def _require_fields(payload: dict, fields: tuple[str, ...]) -> None:
    missing = [f for f in fields if payload.get(f) in (None, "")]
    if missing:
        raise bad_request("缺少必填字段", {"missing": missing})


def _digest(value: str) -> str:
    if not isinstance(value, str) or not DIGEST_RE.match(value):
        raise bad_request("摘要必须形如 sha256:<64位十六进制>", {"field": "digest"})
    return value


def _ref(db, table: str, ref: str, column: str = "ref"):
    pk = {
        "sessions": "session_ref",
        "works": "work_ref",
        "persons": "person_ref",
        "recognitions": "recognition_ref",
        "versions": "version_ref",
        "plans": "plan_ref",
        "grants": "grant_ref",
        "withdrawals": "withdrawal_ref",
        "publications": "publication_ref",
    }.get(table, column)
    return db.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (ref,)).fetchone()


def _ensure_absent(db, table: str, ref: str, label: str) -> None:
    if _ref(db, table, ref) is not None:
        raise conflict(f"{label}已存在：{ref}", {"ref": ref})


# ---------------------------------------------------------------- 登记

def register_session(db, payload: dict) -> dict:
    _require_fields(payload, ("session_ref", "shot_at"))
    timeutil.parse(payload["shot_at"], "拍摄时间")
    _ensure_absent(db, "sessions", payload["session_ref"], "拍摄场次")
    db.execute(
        "INSERT INTO sessions(session_ref, shot_at, location_ref, created_at)"
        " VALUES (?, ?, ?, ?)",
        (
            payload["session_ref"], payload["shot_at"],
            payload.get("location_ref"), timeutil.now(),
        ),
    )
    return {"session_ref": payload["session_ref"]}


def register_work(db, payload: dict) -> dict:
    _require_fields(payload, ("work_ref", "session_ref", "author_ref", "digest", "captured_at"))
    _digest(payload["digest"])
    timeutil.parse(payload["captured_at"], "拍摄时间")
    if _ref(db, "sessions", payload["session_ref"]) is None:
        raise unprocessable("拍摄场次不存在", {"session_ref": payload["session_ref"]})
    _ensure_absent(db, "works", payload["work_ref"], "作品")
    db.execute(
        "INSERT INTO works(work_ref, session_ref, author_ref, digest, captured_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            payload["work_ref"], payload["session_ref"], payload["author_ref"],
            payload["digest"], payload["captured_at"], timeutil.now(),
        ),
    )
    return {"work_ref": payload["work_ref"], "digest": payload["digest"]}


def register_person(db, payload: dict) -> dict:
    _require_fields(payload, ("person_ref",))
    _ensure_absent(db, "persons", payload["person_ref"], "人物")
    kind = payload.get("kind", "visitor")
    if kind not in PERSON_KINDS:
        raise bad_request("未知人物类型", {"allowed": PERSON_KINDS})
    is_minor = 1 if payload.get("is_minor") or kind == "minor" else 0
    guardian_ref = payload.get("guardian_ref")
    if is_minor and not guardian_ref:
        raise unprocessable("未成年人必须登记监护关系", {"person_ref": payload["person_ref"]})
    if guardian_ref and _ref(db, "persons", guardian_ref) is None:
        raise unprocessable("监护人不存在", {"guardian_ref": guardian_ref})
    db.execute(
        "INSERT INTO persons(person_ref, kind, is_minor, guardian_ref, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (payload["person_ref"], kind, is_minor, guardian_ref, timeutil.now()),
    )
    return {"person_ref": payload["person_ref"], "is_minor": bool(is_minor),
            "guardian_ref": guardian_ref}


def register_recognition(db, payload: dict) -> dict:
    _require_fields(payload, ("recognition_ref", "work_ref", "person_ref", "declared_by"))
    if _ref(db, "works", payload["work_ref"]) is None:
        raise unprocessable("作品不存在", {"work_ref": payload["work_ref"]})
    if _ref(db, "persons", payload["person_ref"]) is None:
        raise unprocessable("人物不存在", {"person_ref": payload["person_ref"]})
    _ensure_absent(db, "recognitions", payload["recognition_ref"], "识别声明")
    region = payload.get("region") or {}
    db.execute(
        "INSERT INTO recognitions(recognition_ref, work_ref, person_ref, region_json,"
        " declared_by, declared_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            payload["recognition_ref"], payload["work_ref"], payload["person_ref"],
            json.dumps(region, ensure_ascii=False), payload["declared_by"], timeutil.now(),
        ),
    )
    return {"recognition_ref": payload["recognition_ref"]}


def register_grant(db, payload: dict) -> dict:
    _require_fields(payload, ("grant_ref", "person_ref", "purposes", "territories", "valid_from"))
    person = _ref(db, "persons", payload["person_ref"])
    if person is None:
        raise unprocessable("人物不存在", {"person_ref": payload["person_ref"]})
    purposes = payload["purposes"]
    territories = payload["territories"]
    if not isinstance(purposes, list) or not purposes:
        raise bad_request("授权用途至少一项", {"field": "purposes"})
    if not isinstance(territories, list) or not territories:
        raise bad_request("授权地域至少一项", {"field": "territories"})
    if payload.get("valid_until") is not None:
        timeutil.ensure_order(payload["valid_from"], payload["valid_until"])
    granted_by = payload.get("granted_by_ref")
    if person["is_minor"]:
        if not granted_by or granted_by != person["guardian_ref"]:
            raise unprocessable(
                "未成年人授权须由其监护人授予",
                {"guardian_ref": person["guardian_ref"], "granted_by_ref": granted_by},
            )
    created_at = payload.get("created_at") or timeutil.now()
    timeutil.parse(created_at, "授权登记时间")
    _ensure_absent(db, "grants", payload["grant_ref"], "授权")
    db.execute(
        "INSERT INTO grants(grant_ref, person_ref, granted_by_ref, purposes_json,"
        " territories_json, valid_from, valid_until, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            payload["grant_ref"], payload["person_ref"], granted_by,
            json.dumps(purposes, ensure_ascii=False),
            json.dumps(territories, ensure_ascii=False),
            payload["valid_from"], payload.get("valid_until"), created_at,
        ),
    )
    return {"grant_ref": payload["grant_ref"], "created_at": created_at}


# ---------------------------------------------------------------- 版本

def create_version(db, payload: dict) -> dict:
    _require_fields(payload, ("version_ref", "work_ref", "kind", "digest"))
    _digest(payload["digest"])
    kind = payload["kind"]
    if kind not in VERSION_KINDS:
        raise bad_request("未知版本类型", {"allowed": VERSION_KINDS})
    work = _ref(db, "works", payload["work_ref"])
    if work is None:
        raise unprocessable("作品不存在", {"work_ref": payload["work_ref"]})
    _ensure_absent(db, "versions", payload["version_ref"], "影像版本")

    parent_ref = payload.get("parent_version_ref")
    params = payload.get("params") or {}

    if kind == "original":
        # 原始版本的可识别人物直接来自该作品的识别声明
        recognitions = db.execute(
            "SELECT person_ref, region_json FROM recognitions WHERE work_ref = ?",
            (payload["work_ref"],),
        ).fetchall()
        persons = {
            row["person_ref"]: {"state": "visible", "region": json.loads(row["region_json"] or "{}")}
            for row in recognitions
        }
    else:
        persons = derive_persons(db, parent_ref, kind, params)

    db.execute(
        "INSERT INTO versions(version_ref, work_ref, parent_version_ref, kind,"
        " params_json, digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            payload["version_ref"], payload["work_ref"], parent_ref, kind,
            json.dumps(params, ensure_ascii=False), payload["digest"], timeutil.now(),
        ),
    )
    if kind == "composite":
        for source_ref in params.get("sources", []):
            db.execute(
                "INSERT OR IGNORE INTO version_sources(version_ref, source_version_ref)"
                " VALUES (?, ?)",
                (payload["version_ref"], source_ref),
            )
    for person_ref, info in persons.items():
        db.execute(
            "INSERT INTO version_persons(version_ref, person_ref, state, region_json)"
            " VALUES (?, ?, ?, ?)",
            (payload["version_ref"], person_ref, info["state"],
             json.dumps(info.get("region", {}), ensure_ascii=False)),
        )
    return {
        "version_ref": payload["version_ref"], "kind": kind,
        "visible_persons": [p for p, i in persons.items() if i["state"] == "visible"],
        "masked_persons": [p for p, i in persons.items() if i["state"] == "masked"],
    }


# ---------------------------------------------------------------- 方案与审批

def submit_plan(db, payload: dict) -> dict:
    _require_fields(
        payload,
        ("plan_ref", "version_ref", "purpose", "channel", "territory", "submitted_by"),
    )
    if _ref(db, "versions", payload["version_ref"]) is None:
        raise unprocessable("影像版本不存在", {"version_ref": payload["version_ref"]})
    _ensure_absent(db, "plans", payload["plan_ref"], "用途方案")
    interested = payload.get("interested_parties") or []
    db.execute(
        "INSERT INTO plans(plan_ref, version_ref, purpose, channel, territory,"
        " submitted_by, interested_parties_json, submitted_at, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (
            payload["plan_ref"], payload["version_ref"], payload["purpose"],
            payload["channel"], payload["territory"], payload["submitted_by"],
            json.dumps(interested, ensure_ascii=False), timeutil.now(),
        ),
    )
    return {"plan_ref": payload["plan_ref"], "status": "pending"}


def decide_plan(db, payload: dict) -> dict:
    _require_fields(payload, ("approval_ref", "plan_ref", "approver_ref", "decision"))
    plan = _ref(db, "plans", payload["plan_ref"])
    if plan is None:
        raise not_found("用途方案不存在")
    if plan["status"] != "pending":
        raise conflict("该方案已被裁决", {"status": plan["status"]})
    decision = payload["decision"]
    if decision not in ("approved", "rejected"):
        raise bad_request("decision 必须为 approved/rejected")

    approver = payload["approver_ref"]
    interested = json.loads(plan["interested_parties_json"] or "[]")
    conflicts = []
    if approver == plan["submitted_by"]:
        conflicts.append("审核人不得是方案提交人")
    if approver in interested:
        conflicts.append("审核人不得是利益相关方")
    if conflicts:
        raise unprocessable("审核人存在利益冲突，必须由无利益关系审核人确认",
                            {"conflicts": conflicts, "approver_ref": approver})

    at = payload.get("decided_at") or timeutil.now()
    timeutil.parse(at, "裁决时间")
    verdict = evaluate(
        db, plan["version_ref"], plan["purpose"], plan["channel"],
        plan["territory"], at,
    )

    if decision == "approved" and not verdict.valid:
        raise unprocessable("核验未通过，不能批准", {"verdict": verdict.as_dict()})

    db.execute(
        "INSERT INTO approvals(approval_ref, plan_ref, approver_ref, decision,"
        " decided_at, note, verdict_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            payload["approval_ref"], payload["plan_ref"], approver, decision, at,
            payload.get("note"),
            json.dumps(verdict.as_dict(), ensure_ascii=False),
        ),
    )
    db.execute("UPDATE plans SET status = ? WHERE plan_ref = ?",
               (decision, payload["plan_ref"]))
    return {"plan_ref": payload["plan_ref"], "status": decision,
            "verdict": verdict.as_dict()}


# ---------------------------------------------------------------- 发布证据

def _approval_chain(db, plan_ref: str) -> list[dict]:
    rows = db.execute(
        "SELECT approval_ref, approver_ref, decision, decided_at, note FROM approvals"
        " WHERE plan_ref = ? ORDER BY decided_at",
        (plan_ref,),
    ).fetchall()
    return [dict(r) for r in rows]


def publish(db, payload: dict) -> dict:
    _require_fields(payload, ("publication_ref", "plan_ref"))
    plan = _ref(db, "plans", payload["plan_ref"])
    if plan is None:
        raise not_found("用途方案不存在")
    if plan["status"] != "approved":
        raise unprocessable("仅已批准方案可发布", {"status": plan["status"]})
    _ensure_absent(db, "publications", payload["publication_ref"], "发布证据")

    at = payload.get("published_at") or timeutil.now()
    timeutil.parse(at, "发布时间")
    verdict = evaluate(
        db, plan["version_ref"], plan["purpose"], plan["channel"],
        plan["territory"], at,
    )
    if not verdict.valid:
        raise unprocessable("发布时点核验未通过（授权可能已到期或撤回）",
                            {"verdict": verdict.as_dict()})

    version = _ref(db, "versions", plan["version_ref"])
    masking = [
        p["person_ref"]
        for p in db.execute(
            "SELECT person_ref FROM version_persons WHERE version_ref = ? AND state='masked'",
            (plan["version_ref"],),
        ).fetchall()
    ]
    grant_scope = {}
    for pv in verdict.persons:
        if pv.matched_grants:
            rows = db.execute(
                "SELECT grant_ref, purposes_json, territories_json, valid_from, valid_until"
                " FROM grants WHERE grant_ref IN (%s)" % ",".join("?" * len(pv.matched_grants)),
                pv.matched_grants,
            ).fetchall()
            grant_scope[pv.person_ref] = [
                {
                    "grant_ref": r["grant_ref"],
                    "purposes": json.loads(r["purposes_json"] or "[]"),
                    "territories": json.loads(r["territories_json"] or "[]"),
                    "valid_from": r["valid_from"],
                    "valid_until": r["valid_until"],
                }
                for r in rows
            ]

    evidence = {
        "publication_ref": payload["publication_ref"],
        "version_ref": plan["version_ref"],
        "digest": version["digest"],
        "channel": plan["channel"],
        "purpose": plan["purpose"],
        "territory": plan["territory"],
        "published_at": at,
        "visible_persons": [p.person_ref for p in verdict.persons if p.required],
        "masking": masking,
        "grant_scope": grant_scope,
        "verdict": verdict.as_dict(),
        "submitted_by": plan["submitted_by"],
        "approval_chain": _approval_chain(db, payload["plan_ref"]),
    }
    db.execute(
        "INSERT INTO publications(publication_ref, plan_ref, version_ref, channel,"
        " published_at, evidence_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            payload["publication_ref"], payload["plan_ref"], plan["version_ref"],
            plan["channel"], at, json.dumps(evidence, ensure_ascii=False),
        ),
    )
    return evidence


# ---------------------------------------------------------------- 撤回处置

def withdraw(db, payload: dict) -> dict:
    """登记撤回；只冻结实际承载该人物的渠道，并生成处置清单。历史发布证据不变。"""
    _require_fields(payload, ("withdrawal_ref", "grant_ref"))
    grant = _ref(db, "grants", payload["grant_ref"])
    if grant is None:
        raise not_found("授权不存在")
    existing = db.execute(
        "SELECT 1 FROM withdrawals WHERE grant_ref = ?", (payload["grant_ref"],)
    ).fetchone()
    if existing is not None:
        raise conflict("该授权已撤回", {"grant_ref": payload["grant_ref"]})

    at = payload.get("withdrawn_at") or timeutil.now()
    timeutil.parse(at, "撤回时间")
    db.execute(
        "INSERT INTO withdrawals(withdrawal_ref, grant_ref, withdrawn_at, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (payload["withdrawal_ref"], payload["grant_ref"], at,
         payload.get("reason"), timeutil.now()),
    )

    # 受影响发布：版本中该人物仍 visible，且发布时间不早于撤回（持续在线的渠道）。
    affected = db.execute(
        """
        SELECT DISTINCT pub.publication_ref, pub.channel
          FROM publications pub
          JOIN version_persons vp ON vp.version_ref = pub.version_ref
         WHERE vp.person_ref = ? AND vp.state = 'visible'
           AND pub.published_at < ?
        """,
        (grant["person_ref"], at),
    ).fetchall()

    freezes: list[str] = []
    items: list[dict] = []
    for row in affected:
        db.execute(
            "INSERT OR IGNORE INTO channel_freezes(freeze_ref, withdrawal_ref, channel,"
            " frozen_at, note) VALUES (?, ?, ?, ?, ?)",
            (f"{payload['withdrawal_ref']}:{row['channel']}", payload["withdrawal_ref"],
             row["channel"], at, "授权撤回，冻结受影响渠道"),
        )
        if row["channel"] not in freezes:
            freezes.append(row["channel"])
        item_ref = f"{payload['withdrawal_ref']}:{row['publication_ref']}"
        db.execute(
            "INSERT OR IGNORE INTO disposition_items(item_ref, withdrawal_ref,"
            " publication_ref, channel, action, status, created_at)"
            " VALUES (?, ?, ?, ?, 'takedown', 'pending', ?)",
            (item_ref, payload["withdrawal_ref"], row["publication_ref"],
             row["channel"], timeutil.now()),
        )
        items.append({"item_ref": item_ref, "publication_ref": row["publication_ref"],
                      "channel": row["channel"], "action": "takedown", "status": "pending"})

    return {
        "withdrawal_ref": payload["withdrawal_ref"],
        "withdrawn_at": at,
        "frozen_channels": freezes,
        "disposition": items,
        "note": "历史发布证据保持不可变，仅冻结受影响渠道并生成处置清单",
    }
