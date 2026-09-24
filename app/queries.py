
"""查询与内部追溯：重现任一发布时间采用的影像摘要、授权范围、遮蔽处理和批准链。"""

import json

from .errors import not_found
from .evaluation import evaluate


def version_summary(db, version_ref: str) -> dict:
    version = db.execute("SELECT * FROM versions WHERE version_ref = ?", (version_ref,)).fetchone()
    if version is None:
        raise not_found("影像版本不存在")
    work = db.execute("SELECT * FROM works WHERE work_ref = ?", (version["work_ref"],)).fetchone()
    persons = db.execute(
        "SELECT person_ref, state, region_json FROM version_persons WHERE version_ref = ?",
        (version_ref,),
    ).fetchall()
    sources = [
        r["source_version_ref"]
        for r in db.execute(
            "SELECT source_version_ref FROM version_sources WHERE version_ref = ?",
            (version_ref,),
        ).fetchall()
    ]
    return {
        "version_ref": version_ref,
        "work_ref": version["work_ref"],
        "parent_version_ref": version["parent_version_ref"],
        "kind": version["kind"],
        "params": json.loads(version["params_json"] or "{}"),
        "sources": sources,
        "digest": version["digest"],
        "work_digest": work["digest"],
        "created_at": version["created_at"],
        "persons": [
            {"person_ref": p["person_ref"], "state": p["state"],
             "region": json.loads(p["region_json"] or "{}")}
            for p in persons
        ],
    }


def replay_publication(db, publication_ref: str) -> dict:
    """重放：以发布当时的事实重新计算核验，并回传不可变证据与批准链。"""
    pub = db.execute(
        "SELECT * FROM publications WHERE publication_ref = ?", (publication_ref,)
    ).fetchone()
    if pub is None:
        raise not_found("发布记录不存在")
    evidence = json.loads(pub["evidence_json"])

    # 以发布时间为“现在”重新求值：迟到授权不会计入，当时之后的撤回也不会倒改结论
    plan = db.execute("SELECT * FROM plans WHERE plan_ref = ?", (pub["plan_ref"],)).fetchone()
    replayed = evaluate(
        db, pub["version_ref"], plan["purpose"], pub["channel"],
        plan["territory"], pub["published_at"],
    )

    return {
        "publication_ref": publication_ref,
        "published_at": pub["published_at"],
        "channel": pub["channel"],
        "version": version_summary(db, pub["version_ref"]),
        "image_digest": evidence["digest"],
        "grant_scope": evidence.get("grant_scope", {}),
        "masking": evidence.get("masking", []),
        "approval_chain": evidence.get("approval_chain", []),
        "recorded_evidence": evidence,
        "replay_valid_at_publication": replayed.valid,
        "replay_verdict": replayed.as_dict(),
        "evidence_consistent": replayed.valid == evidence.get("verdict", {}).get("valid"),
    }


def disposition_list(db, withdrawal_ref: str | None = None) -> list[dict]:
    sql = (
        "SELECT item_ref, withdrawal_ref, publication_ref, channel, action, status"
        " FROM disposition_items"
    )
    rows = db.execute(
        sql + (" WHERE withdrawal_ref = ?" if withdrawal_ref else " ORDER BY created_at"),
        (withdrawal_ref,) if withdrawal_ref else (),
    ).fetchall() if withdrawal_ref else db.execute(sql + " ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]
