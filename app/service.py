"""用例编排：录入、版本派生、方案审批、发布、撤回冻结、核验与证据重放。

时间观（关键）：所有门控判定都按“指定时点 as-of”重算——只采纳
recorded_at <= as_of 的授权/监护/撤回事实。因此迟到授权不可能改变
发布时刻已经固化的审批证据；撤回只影响仍处于 active 的发布。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from . import domain, store


class ConflictError(ValueError):
    """利益冲突或状态流转不允许（HTTP 409）。"""


class NotFoundError(LookupError):
    """引用的资源不存在（HTTP 404）。"""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _require(record: dict | None, kind: str, key: str) -> dict:
    if record is None:
        raise NotFoundError(f"{kind} 不存在：{key}")
    return record


# ================================================================ 录入

def register_work(conn: sqlite3.Connection, body: dict) -> dict:
    work = {
        "work_ref": body["work_ref"],
        "author_ref": body["author_ref"],
        "source_digest": domain.require_digest(body["source_digest"], field_name="source_digest"),
        "title": body.get("title"),
        "recorded_at": domain.parse_time(body["recorded_at"], field_name="recorded_at").isoformat(),
    }
    store.insert(conn, "works", work)
    conn.commit()
    return work


def register_session(conn: sqlite3.Connection, body: dict) -> dict:
    _require(store.get(conn, "works", "work_ref", body["work_ref"]), "作品", body["work_ref"])
    session = {
        "session_ref": body["session_ref"],
        "work_ref": body["work_ref"],
        "shot_at": domain.parse_time(body["shot_at"], field_name="shot_at").isoformat(),
        "locality": body.get("locality"),
        "recorded_at": domain.now_iso(),
    }
    store.insert(conn, "sessions", session)
    conn.commit()
    return session


def register_person(conn: sqlite3.Connection, body: dict) -> dict:
    if not isinstance(body.get("is_minor"), bool):
        raise domain.DomainError("is_minor 必须是布尔值")
    person = {
        "person_ref": body["person_ref"],
        "is_minor": int(body["is_minor"]),
        "label": body.get("label"),
        "recorded_at": domain.now_iso(),
    }
    store.insert(conn, "persons", person)
    conn.commit()
    return person


def register_claim(conn: sqlite3.Connection, body: dict) -> dict:
    _require(store.get(conn, "works", "work_ref", body["work_ref"]), "作品", body["work_ref"])
    _require(store.get(conn, "persons", "person_ref", body["person_ref"]), "人物", body["person_ref"])
    domain.require_choice(body["role"], domain.ROLES, field_name="role")
    claim = {
        "claim_id": body.get("claim_id") or _new_id("CLAIM"),
        "work_ref": body["work_ref"],
        "person_ref": body["person_ref"],
        "bbox": store.dumps(domain.bbox(body["bbox"])) if body.get("bbox") is not None else None,
        "role": body["role"],
        "declared_by": body["declared_by"],
        "recorded_at": domain.now_iso(),
    }
    store.insert(conn, "person_claims", claim)
    conn.commit()
    return claim


def register_guardianship(conn: sqlite3.Connection, body: dict) -> dict:
    _require(store.get(conn, "persons", "person_ref", body["child_ref"]), "人物", body["child_ref"])
    _require(store.get(conn, "persons", "person_ref", body["guardian_ref"]), "人物", body["guardian_ref"])
    guardianship = {
        "guardianship_id": body.get("guardianship_id") or _new_id("GUARD"),
        "child_ref": body["child_ref"],
        "guardian_ref": body["guardian_ref"],
        "relation": body["relation"],
        "evidence_digest": domain.require_digest(body["evidence_digest"], field_name="evidence_digest"),
        "valid_from": domain.parse_time(body["valid_from"], field_name="valid_from").isoformat(),
        "valid_to": (
            domain.parse_time(body["valid_to"], field_name="valid_to").isoformat()
            if body.get("valid_to") else None
        ),
        "recorded_at": (
            domain.parse_time(body["recorded_at"], field_name="recorded_at").isoformat()
            if body.get("recorded_at") else domain.now_iso()
        ),
    }
    store.insert(conn, "guardianships", guardianship)
    conn.commit()
    return guardianship


def register_grant(conn: sqlite3.Connection, body: dict) -> dict:
    _require(store.get(conn, "persons", "person_ref", body["person_ref"]), "人物", body["person_ref"])
    if body.get("session_ref"):
        _require(store.get(conn, "sessions", "session_ref", body["session_ref"]),
                 "拍摄场次", body["session_ref"])
    purposes = domain.require_str_list(body.get("purposes"), field_name="purposes")
    channels = domain.require_str_list(body.get("channels"), field_name="channels")
    regions = domain.require_str_list(body.get("regions"), field_name="regions")
    for p in purposes:
        if p != domain.WILDCARD:
            domain.require_choice(p, domain.PURPOSES, field_name="purposes[]")
    for c in channels:
        if c != domain.WILDCARD:
            domain.require_choice(c, domain.CHANNELS, field_name="channels[]")
    grant = {
        "grant_id": body.get("grant_id") or _new_id("GRANT"),
        "person_ref": body["person_ref"],
        "session_ref": body.get("session_ref"),
        "purposes": store.dumps(purposes),
        "channels": store.dumps(channels),
        "regions": store.dumps(regions),
        "valid_from": domain.parse_time(body["valid_from"], field_name="valid_from").isoformat(),
        "valid_until": (
            domain.parse_time(body["valid_until"], field_name="valid_until").isoformat()
            if body.get("valid_until") else None
        ),
        "granted_by": body["granted_by"],
        "recorded_at": (
            domain.parse_time(body["recorded_at"], field_name="recorded_at").isoformat()
            if body.get("recorded_at") else domain.now_iso()
        ),
    }
    store.insert(conn, "grants", grant)
    conn.commit()
    return grant


# ================================================================ 版本派生

def _presence_objects(rows: list[dict]) -> list[domain.Presence]:
    out = []
    for r in rows:
        out.append(domain.Presence(
            person_ref=r["person_ref"],
            role=r["role"],
            bbox=tuple(store.loads(r["bbox"])) if r.get("bbox") else None,
            masked=bool(r["masked"]),
            details=tuple(r["detail"].split("；")) if r.get("detail") else (),
        ))
    return out


def _role_lookup(conn: sqlite3.Connection, work_ref: str):
    claims = store.claims_for_work(conn, work_ref)
    role_by_person = {c["person_ref"]: c["role"] for c in claims}

    def lookup(person_ref: str) -> str:
        return role_by_person.get(person_ref, "mentioned")

    return lookup


def create_version(conn: sqlite3.Connection, body: dict) -> dict:
    kind = domain.require_choice(body.get("kind"), domain.VERSION_KINDS, field_name="kind")
    digest = domain.require_digest(body.get("digest"), field_name="digest")
    version_id = body.get("version_id") or _new_id("VER")
    now = domain.now_iso()

    if kind == "original":
        work_ref = body["work_ref"]
        _require(store.get(conn, "works", "work_ref", work_ref), "作品", work_ref)
        claims = store.claims_for_work(conn, work_ref)
        presence = domain.derive_original([
            {**c, "bbox": store.loads(c["bbox"]) if c["bbox"] else None} for c in claims
        ])
        parent_id = None
        spec: dict = {}
    else:
        spec = body.get("spec") or {}
        if kind == "composite":
            sources_raw = body.get("sources") or []
            if not sources_raw:
                raise domain.DomainError("合成版本必须提供 sources")
            work_ref = body.get("work_ref")
            if not work_ref:
                raise domain.DomainError("合成版本必须显式提供 work_ref")
            _require(store.get(conn, "works", "work_ref", work_ref), "作品", work_ref)
            sources: list[tuple[dict, list[domain.Presence]]] = []
            for s in sources_raw:
                src = _require(store.get(conn, "versions", "version_id", s.get("source_version_id")),
                               "来源版本", s.get("source_version_id") or "")
                sources.append((
                    {"placement": s.get("placement")},
                    _presence_objects(store.version_presence(conn, src["version_id"])),
                ))
            presence = domain.derive_composite(sources)
            spec = {"sources": sources_raw}
            parent_id = None  # 合成版本有多个来源，关系记在 version_sources
        else:
            parent_id = body.get("parent_version_id")
            parent = _require(store.get(conn, "versions", "version_id", parent_id),
                              "父版本", parent_id or "")
            work_ref = body.get("work_ref") or parent["work_ref"]
            _require(store.get(conn, "works", "work_ref", work_ref), "作品", work_ref)
            parents = _presence_objects(store.version_presence(conn, parent_id))

            if kind == "crop":
                presence = domain.derive_crop(parents, spec)
            elif kind == "mask":
                presence = domain.derive_mask(parents, spec)
            else:  # subtitle
                presence = domain.derive_subtitle(
                    parents, spec, roles_lookup=_role_lookup(conn, work_ref)
                )

    version = {
        "version_id": version_id,
        "work_ref": work_ref,
        "parent_version_id": parent_id,
        "kind": kind,
        "spec": store.dumps(spec),
        "digest": digest,
        "created_by": body["created_by"],
        "created_at": now,
    }
    store.insert(conn, "versions", version)
    if kind == "composite":
        for s in body.get("sources", []):
            store.insert(conn, "version_sources", {
                "version_id": version_id,
                "source_version_id": s["source_version_id"],
                "placement": store.dumps(s.get("placement")),
            })
    store.replace_version_presence(conn, version_id, presence)
    conn.commit()
    return {"version": version, "presence": [_presence_dict(p) for p in presence]}


def _presence_dict(p: domain.Presence) -> dict:
    return {
        "person_ref": p.person_ref,
        "role": p.role,
        "masked": p.masked,
        "bbox": list(p.bbox) if p.bbox is not None else None,
        "detail": "；".join(p.details),
    }


def _version_lineage(conn: sqlite3.Connection, version_id: str) -> list[dict]:
    """返回派生链（合成版本展开其来源）。"""
    chain: list[dict] = []
    seen: set[str] = set()

    def walk(vid: str) -> None:
        if vid in seen:
            return
        seen.add(vid)
        v = store.get(conn, "versions", "version_id", vid)
        if not v:
            return
        for row in conn.execute(
            "SELECT source_version_id FROM version_sources WHERE version_id = ?", (vid,)
        ):
            walk(row[0])
        if v["parent_version_id"]:
            walk(v["parent_version_id"])
        chain.append({
            "version_id": v["version_id"],
            "work_ref": v["work_ref"],
            "kind": v["kind"],
            "digest": v["digest"],
            "parent_version_id": v["parent_version_id"],
            "spec": store.loads(v["spec"]),
            "created_by": v["created_by"],
            "created_at": v["created_at"],
        })

    walk(version_id)
    return chain


# ================================================================ 门控判定

def _work_sessions(conn: sqlite3.Connection, work_refs: set[str]) -> set[str]:
    if not work_refs:
        return set()
    marks = ",".join("?" for _ in work_refs)
    rows = conn.execute(
        f"SELECT session_ref FROM sessions WHERE work_ref IN ({marks})", tuple(work_refs)
    ).fetchall()
    return {r[0] for r in rows}


def evaluate_version(conn: sqlite3.Connection, *, version_id: str, purpose: str,
                     channel: str, region: str, at: datetime) -> dict:
    """按 as-of 时点重算某版本对某用途的授权门控。"""
    version = _require(store.get(conn, "versions", "version_id", version_id), "影像版本", version_id)
    domain.require_choice(purpose, domain.PURPOSES, field_name="purpose")
    domain.require_choice(channel, domain.CHANNELS, field_name="channel")

    lineage = _version_lineage(conn, version_id)
    work_refs = {step["work_ref"] for step in lineage}
    match_sessions = _work_sessions(conn, work_refs)

    presence_rows = _presence_objects(store.version_presence(conn, version_id))
    persons = {
        r["person_ref"]: dict(r)
        for r in conn.execute("SELECT * FROM persons").fetchall()
    }
    # as-of：晚于判定时点记录的授权、监护与撤回不参与（迟到事实不倒改历史）。
    # 授权本身已登记、但其撤回尚未登记时，授权在该时点仍然有效。
    as_of_iso = at.isoformat()
    grants = []
    for g in store.grants_ledger(conn):
        if g["recorded_at"] > as_of_iso:
            continue
        if g.get("revocation_recorded_at") and g["revocation_recorded_at"] > as_of_iso:
            g = {**g, "revoked_at": None, "revocation_recorded_at": None}
        grants.append(g)
    guardianships = [
        dict(r) for r in conn.execute("SELECT * FROM guardianships").fetchall()
        if r["recorded_at"] <= as_of_iso
    ]

    gates = []
    for p in presence_rows:
        person = persons.get(p.person_ref, {"person_ref": p.person_ref, "is_minor": 0})
        gates.append(domain.evaluate_person(
            person=person, presence=p, match_sessions=match_sessions,
            grants=grants, guardianships=guardianships,
            purpose=purpose, channel=channel, region=region, at=at,
        ))
    merged = domain.merge_gates(gates)
    return {
        "as_of": as_of_iso,
        "version": {
            "version_id": version_id,
            "work_ref": version["work_ref"],
            "kind": version["kind"],
            "digest": version["digest"],
            "spec": store.loads(version["spec"]),
        },
        "work_refs": sorted(work_refs),
        "lineage": lineage,
        "scope": {"purpose": purpose, "channel": channel, "region": region},
        "presence": gates,
        "grants_considered": sorted(g["grant_id"] for g in grants),
        "gate": merged,
    }


# ================================================================ 方案与审批

def submit_proposal(conn: sqlite3.Connection, body: dict) -> dict:
    version_id = body["version_id"]
    _require(store.get(conn, "versions", "version_id", version_id), "影像版本", version_id)
    domain.require_choice(body["purpose"], domain.PURPOSES, field_name="purpose")
    domain.require_choice(body["channel"], domain.CHANNELS, field_name="channel")
    use_from = domain.parse_time(body["use_from"], field_name="use_from")
    use_until = (
        domain.parse_time(body["use_until"], field_name="use_until")
        if body.get("use_until") else None
    )
    if use_until and use_until <= use_from:
        raise domain.DomainError("use_until 必须晚于 use_from")
    proposal = {
        "proposal_id": _new_id("PROP"),
        "version_id": version_id,
        "purpose": body["purpose"],
        "channel": body["channel"],
        "region": body["region"],
        "use_from": use_from.isoformat(),
        "use_until": use_until.isoformat() if use_until else None,
        "submitted_by": body["submitted_by"],
        "created_at": domain.now_iso(),
        "status": "pending",
    }
    store.insert(conn, "proposals", proposal)
    conn.commit()
    return proposal


def revise_proposal(conn: sqlite3.Connection, proposal_id: str, body: dict) -> dict:
    proposal = _require(store.get(conn, "proposals", "proposal_id", proposal_id),
                        "用途方案", proposal_id)
    if proposal["status"] != "pending":
        raise ConflictError(f"方案处于 {proposal['status']} 状态，只能编辑 pending 方案")
    allowed = {"purpose", "channel", "region", "use_from", "use_until"}
    patch = {k: v for k, v in body.get("patch", {}).items() if k in allowed}
    if not patch:
        raise domain.DomainError("patch 至少包含一个可改字段：" + ",".join(sorted(allowed)))
    if "purpose" in patch:
        domain.require_choice(patch["purpose"], domain.PURPOSES, field_name="purpose")
    if "channel" in patch:
        domain.require_choice(patch["channel"], domain.CHANNELS, field_name="channel")
    if "use_from" in patch:
        patch["use_from"] = domain.parse_time(patch["use_from"], field_name="use_from").isoformat()
    if "use_until" in patch and patch["use_until"]:
        patch["use_until"] = domain.parse_time(patch["use_until"], field_name="use_until").isoformat()

    revision = {
        "revision_id": _new_id("REV"),
        "proposal_id": proposal_id,
        "editor_ref": body["editor_ref"],
        "revised_at": domain.now_iso(),
        "patch": store.dumps(patch),
    }
    store.insert(conn, "proposal_revisions", revision)
    conn.execute(
        "UPDATE proposals SET " + ", ".join(f"{k} = ?" for k in patch) + " WHERE proposal_id = ?",
        (*patch.values(), proposal_id),
    )
    conn.commit()
    return {"revision": revision, "patch": patch}


def _conflict_check(conn: sqlite3.Connection, proposal: dict, reviewer_ref: str,
                    evidence: dict) -> dict:
    """审核人利益冲突核验：与提交人、作者、在场可辨认人物、授权签署人均不得为同一人。"""
    version = store.get(conn, "versions", "version_id", proposal["version_id"])
    work = store.get(conn, "works", "work_ref", version["work_ref"])
    present_persons = {p["person_ref"] for p in evidence["presence"] if p["status"] != "obscured"}
    grantors = {g for p in evidence["presence"] for g in (p.get("grantors") or [])}
    interested = {
        proposal["submitted_by"], work["author_ref"], version["created_by"],
        *present_persons, *grantors,
    }
    conflicts = sorted(ref for ref in interested if ref == reviewer_ref)
    return {
        "reviewer_ref": reviewer_ref,
        "interested_parties": sorted(interested),
        "conflicts": conflicts,
        "passed": not conflicts,
    }


def decide_proposal(conn: sqlite3.Connection, proposal_id: str, body: dict) -> dict:
    proposal = _require(store.get(conn, "proposals", "proposal_id", proposal_id),
                        "用途方案", proposal_id)
    if proposal["status"] != "pending":
        raise ConflictError(f"方案已 {proposal['status']}，不能重复审批")
    decision = domain.require_choice(body.get("decision"), ("approved", "rejected"),
                                     field_name="decision")
    reviewer_ref = body["reviewer_ref"]
    decided_at = (
        domain.parse_time(body["decided_at"], field_name="decided_at")
        if body.get("decided_at") else domain.parse_time(domain.now_iso(), field_name="decided_at")
    )

    evidence = evaluate_version(
        conn,
        version_id=proposal["version_id"],
        purpose=proposal["purpose"], channel=proposal["channel"], region=proposal["region"],
        at=decided_at,
    )
    evidence["scope"]["use_from"] = proposal["use_from"]
    evidence["scope"]["use_until"] = proposal["use_until"]

    conflict = _conflict_check(conn, proposal, reviewer_ref, evidence)
    if not conflict["passed"]:
        raise ConflictError("审核人与本方案存在利益关系：" + ",".join(conflict["conflicts"]))
    if decision == "approved" and not evidence["gate"]["valid"]:
        raise ConflictError(
            "门控未通过，无法批准；未授权人物："
            + ",".join(evidence["gate"].get("blocked_persons", []))
        )
    if proposal.get("use_until") and evidence["gate"].get("earliest_valid_until"):
        cap = evidence["gate"]["earliest_valid_until"]
        if proposal["use_until"] > cap:
            raise ConflictError(
                f"方案期限超出授权最早到期 {cap}，请缩短 use_until 后重新提交"
            )

    approval_id = _new_id("APR")
    base_digest = domain.evidence_digest(evidence)
    # 批准链在写入前构造；当前批准描述符引用“不含链的门控证据”摘要，保证可确定性重放
    chain = approval_chain(conn, proposal_id)
    chain["approvals"].append({
        "approval_id": approval_id,
        "reviewer_ref": reviewer_ref,
        "decision": decision,
        "decided_at": decided_at.isoformat(),
        "comment": body.get("comment"),
        "conflict_check": conflict,
        "evidence_digest": base_digest,
    })
    evidence["approval_chain"] = chain

    approval = {
        "approval_id": approval_id,
        "proposal_id": proposal_id,
        "reviewer_ref": reviewer_ref,
        "decision": decision,
        "comment": body.get("comment"),
        "decided_at": decided_at.isoformat(),
        "conflict_check": store.dumps(conflict),
        "gate_snapshot": store.dumps(evidence),
    }

    store.insert(conn, "approvals", approval)
    conn.execute(
        "UPDATE proposals SET status = ? WHERE proposal_id = ?", (decision, proposal_id)
    )
    conn.commit()
    return {
        "approval_id": approval_id,
        "proposal_id": proposal_id,
        "decision": decision,
        "decided_at": approval["decided_at"],
        "conflict_check": conflict,
        "gate": evidence["gate"],
        "evidence_digest": domain.evidence_digest(evidence),
    }


def approval_chain(conn: sqlite3.Connection, proposal_id: str) -> dict:
    proposal = _require(store.get(conn, "proposals", "proposal_id", proposal_id),
                        "用途方案", proposal_id)
    revisions = [
        {"revision_id": r["revision_id"], "editor_ref": r["editor_ref"],
         "revised_at": r["revised_at"], "patch": store.loads(r["patch"])}
        for r in conn.execute(
            "SELECT * FROM proposal_revisions WHERE proposal_id = ? ORDER BY revised_at",
            (proposal_id,),
        )
    ]
    approvals = []
    for r in conn.execute(
        "SELECT * FROM approvals WHERE proposal_id = ? ORDER BY decided_at", (proposal_id,)
    ):
        snapshot = store.loads(r["gate_snapshot"])
        # 描述符摘要只覆盖门控证据本体（批准链除外），以保持确定性重放
        snapshot_for_digest = {k: v for k, v in snapshot.items() if k != "approval_chain"}
        approvals.append({
            "approval_id": r["approval_id"],
            "reviewer_ref": r["reviewer_ref"],
            "decision": r["decision"],
            "decided_at": r["decided_at"],
            "comment": r["comment"],
            "conflict_check": store.loads(r["conflict_check"]),
            "evidence_digest": domain.evidence_digest(snapshot_for_digest),
        })
    chain = {
        "proposal": {
            "proposal_id": proposal_id,
            "version_id": proposal["version_id"],
            "submitted_by": proposal["submitted_by"],
            "created_at": proposal["created_at"],
            "status": proposal["status"],
            "scope": {
                "purpose": proposal["purpose"], "channel": proposal["channel"],
                "region": proposal["region"], "use_from": proposal["use_from"],
                "use_until": proposal["use_until"],
            },
        },
        "revisions": revisions,
        "approvals": approvals,
    }
    return chain


# ================================================================ 发布

def publish(conn: sqlite3.Connection, body: dict) -> dict:
    proposal = _require(store.get(conn, "proposals", "proposal_id", body["proposal_id"]),
                        "用途方案", body["proposal_id"])
    if proposal["status"] != "approved":
        raise ConflictError(f"方案状态为 {proposal['status']}，只有 approved 方案可发布")
    existing = conn.execute(
        "SELECT publication_id FROM publications WHERE proposal_id = ? AND status = 'active'",
        (proposal["proposal_id"],),
    ).fetchone()
    if existing:
        raise ConflictError("该方案已有 active 发布：" + existing[0])

    published_at = (
        domain.parse_time(body["published_at"], field_name="published_at")
        if body.get("published_at") else domain.parse_time(domain.now_iso(), field_name="published_at")
    )
    # 发布时以发布时点重算门控，并把完整证据固化；此后事实变化不再改写本行
    evidence = evaluate_version(
        conn,
        version_id=proposal["version_id"],
        purpose=proposal["purpose"], channel=proposal["channel"], region=proposal["region"],
        at=published_at,
    )
    evidence["scope"]["use_from"] = proposal["use_from"]
    evidence["scope"]["use_until"] = proposal["use_until"]
    if not evidence["gate"]["valid"]:
        raise ConflictError(
            "发布时点门控未通过；未授权人物："
            + ",".join(evidence["gate"].get("blocked_persons", []))
        )
    if proposal["use_until"] and evidence["gate"].get("earliest_valid_until"):
        cap = evidence["gate"]["earliest_valid_until"]
        if proposal["use_until"] > cap:
            raise ConflictError(f"授权将于 {cap} 到期，晚于方案期限不可发布")

    approval_row = conn.execute(
        "SELECT * FROM approvals WHERE proposal_id = ? AND decision = 'approved' "
        "ORDER BY decided_at DESC LIMIT 1",
        (proposal["proposal_id"],),
    ).fetchone()
    approval_snapshot = store.loads(approval_row["gate_snapshot"])
    evidence["approval_chain"] = approval_chain(conn, proposal["proposal_id"])
    evidence["approved_evidence_digest"] = domain.evidence_digest(approval_snapshot)

    publication = {
        "publication_id": _new_id("PUB"),
        "proposal_id": proposal["proposal_id"],
        "version_id": proposal["version_id"],
        "purpose": proposal["purpose"],
        "channel": proposal["channel"],
        "region": proposal["region"],
        "published_at": published_at.isoformat(),
        "status": "active",
        "evidence": store.dumps(evidence),
        "evidence_digest": domain.evidence_digest(evidence),
        "frozen_at": None,
        "freeze_reason": None,
    }
    store.insert(conn, "publications", publication)
    conn.commit()
    return {
        "publication_id": publication["publication_id"],
        "published_at": publication["published_at"],
        "status": "active",
        "evidence_digest": publication["evidence_digest"],
    }


# ================================================================ 撤回与处置

def revoke_grant(conn: sqlite3.Connection, grant_id: str, body: dict) -> dict:
    _require(store.get(conn, "grants", "grant_id", grant_id), "授权", grant_id)
    revoked_at = domain.parse_time(body["revoked_at"], field_name="revoked_at")
    now = (
        domain.parse_time(body["recorded_at"], field_name="recorded_at")
        if body.get("recorded_at") else domain.parse_time(domain.now_iso(), field_name="recorded_at")
    ).isoformat()
    revocation = {
        "revocation_id": _new_id("REVOKE"),
        "grant_id": grant_id,
        "revoked_at": revoked_at.isoformat(),
        "reason": body.get("reason"),
        "recorded_at": now,
    }
    store.insert(conn, "grant_revocations", revocation)

    # 只冻结证据中实际依赖该授权、且仍 active 的发布；其它渠道不受影响
    affected = store.active_publications_using_grant(conn, grant_id)
    items = []
    for pub in affected:
        conn.execute(
            "UPDATE publications SET status = 'frozen', frozen_at = ?, freeze_reason = ? "
            "WHERE publication_id = ?",
            (now, f"授权 {grant_id} 撤回", pub["publication_id"]),
        )
        item = {
            "item_id": _new_id("ITEM"),
            "publication_id": pub["publication_id"],
            "channel": pub["channel"],
            "action": _disposition_action(pub["channel"]),
            "reason": f"授权 {grant_id} 撤回：冻结 {pub['channel']} 渠道发布",
            "status": "open",
            "created_at": now,
            "handled_at": None,
        }
        store.insert(conn, "disposition_items", item)
        items.append(item)
    conn.commit()
    return {
        "revocation_id": revocation["revocation_id"],
        "grant_id": grant_id,
        "revoked_at": revocation["revoked_at"],
        "frozen_publications": [p["publication_id"] for p in affected],
        "disposition_items": [{"item_id": i["item_id"], "channel": i["channel"],
                               "action": i["action"], "status": "open"} for i in items],
    }


def _disposition_action(channel: str) -> str:
    return {
        "onsite": "remove_from_exhibition",
        "social_media": "take_down_post",
        "overseas_tour": "pull_from_tour_materials",
    }.get(channel, "cease_use")


def handle_disposition(conn: sqlite3.Connection, item_id: str, body: dict) -> dict:
    item = _require(store.get(conn, "disposition_items", "item_id", item_id),
                    "处置事项", item_id)
    if item["status"] != "open":
        raise ConflictError("该处置事项已处理")
    handled_at = (
        domain.parse_time(body["handled_at"], field_name="handled_at")
        if body.get("handled_at") else domain.parse_time(domain.now_iso(), field_name="handled_at")
    ).isoformat()
    conn.execute(
        "UPDATE disposition_items SET status = 'done', handled_at = ? WHERE item_id = ?",
        (handled_at, item_id),
    )
    conn.commit()
    return {"item_id": item_id, "status": "done", "handled_at": handled_at}


# ================================================================ 核验与证据

def external_verify(conn: sqlite3.Connection, *, version_id: str, purpose: str,
                    channel: str, region: str, at: datetime) -> dict:
    """对外核验：仅返回该版本对指定用途是否有效。"""
    evidence = evaluate_version(
        conn, version_id=version_id, purpose=purpose, channel=channel,
        region=region, at=at,
    )
    return {
        "version_id": version_id,
        "purpose": purpose,
        "channel": channel,
        "region": region,
        "as_of": at.isoformat(),
        "valid": bool(evidence["gate"]["valid"]),
    }


def publication_evidence(conn: sqlite3.Connection, publication_id: str) -> dict:
    """内部证据重放：发布时固化的影像摘要、授权范围、遮蔽处理与批准链。"""
    pub = _require(store.get(conn, "publications", "publication_id", publication_id),
                   "发布", publication_id)
    evidence = store.loads(pub["evidence"])
    return {
        "publication_id": publication_id,
        "status": pub["status"],
        "published_at": pub["published_at"],
        "frozen_at": pub["frozen_at"],
        "freeze_reason": pub["freeze_reason"],
        "stored_evidence_digest": pub["evidence_digest"],
        "recomputed_digest_matches": domain.evidence_digest(evidence) == pub["evidence_digest"],
        "evidence": evidence,
    }
