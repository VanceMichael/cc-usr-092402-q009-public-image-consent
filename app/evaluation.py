
"""时点核验引擎。

对某影像版本在指定时间点、用途/渠道/地域下逐人求值，
多人影像合并“最严格”条件：任一可识别人物不满足即整版无效。

关键不变量：
- 授权以 created_at 为登记时点；迟到授权不得对历史发布生效。
- 授权窗口（valid_from/valid_until）与撤回（withdrawn_at）共同判定。
- 撤回只影响实际承载该人物的发布渠道；已遮蔽（masked）人物不再需要授权。
"""

from dataclasses import dataclass, field

from . import timeutil
from .versions import TERRITORY_ALL


@dataclass
class PersonVerdict:
    person_ref: str
    is_minor: bool
    required: bool
    valid: bool
    matched_grants: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "person_ref": self.person_ref,
            "is_minor": bool(self.is_minor),
            "required": self.required,
            "valid": self.valid,
            "matched_grants": self.matched_grants,
            "reasons": self.reasons,
        }


@dataclass
class Verdict:
    valid: bool
    at: str
    purpose: str
    channel: str
    territory: str
    version_ref: str
    persons: list[PersonVerdict]

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "at": self.at,
            "purpose": self.purpose,
            "channel": self.channel,
            "territory": self.territory,
            "version_ref": self.version_ref,
            "persons": [p.as_dict() for p in self.persons],
        }


def _territory_covers(allowed: list[str], requested: str) -> bool:
    return TERRITORY_ALL in allowed or requested in allowed


def _grants_for_person(db, person_ref: str, at_iso: str):
    """该人物在 at 时点已经登记、且未撤回（或撤回尚未生效）的授权。"""
    import json

    at = timeutil.parse(at_iso, "核验时间")
    rows = db.execute(
        """
        SELECT g.*,
               (SELECT w.withdrawn_at FROM withdrawals w
                 WHERE w.grant_ref = g.grant_ref
                 ORDER BY w.withdrawn_at LIMIT 1) AS withdrawn_at
          FROM grants g WHERE g.person_ref = ?
        """,
        (person_ref,),
    ).fetchall()
    usable = []
    for row in rows:
        created = timeutil.parse(row["created_at"], "授权登记时间")
        if created > at:
            continue  # 迟到授权：在 at 时点尚不存在
        if row["withdrawn_at"] is not None:
            if timeutil.parse(row["withdrawn_at"], "撤回时间") <= at:
                continue  # 已撤回
        valid_from = timeutil.parse(row["valid_from"], "授权生效时间")
        if valid_from > at:
            continue
        if row["valid_until"] is not None:
            if timeutil.parse(row["valid_until"], "授权到期时间") <= at:
                continue
        usable.append(row)
    return usable


def evaluate(
    db,
    version_ref: str,
    purpose: str,
    channel: str,
    territory: str,
    at: str,
) -> Verdict:
    person_rows = db.execute(
        """
        SELECT vp.person_ref, vp.state, p.is_minor
          FROM version_persons vp
          JOIN persons p ON p.person_ref = vp.person_ref
         WHERE vp.version_ref = ?
        """,
        (version_ref,),
    ).fetchall()

    verdicts: list[PersonVerdict] = []
    for row in person_rows:
        person_ref = row["person_ref"]
        is_minor = bool(row["is_minor"])
        if row["state"] == "masked":
            verdicts.append(
                PersonVerdict(person_ref, is_minor, required=False, valid=True,
                              reasons=["该人物在本版本已遮蔽，无需授权"])
            )
            continue

        pv = PersonVerdict(person_ref, is_minor, required=True, valid=False)
        grants = _grants_for_person(db, person_ref, at)
        for grant in grants:
            import json

            purposes = json.loads(grant["purposes_json"] or "[]")
            territories = json.loads(grant["territories_json"] or "[]")
            if purpose not in purposes:
                pv.reasons.append(f"授权 {grant['grant_ref']} 不含用途 {purpose}")
                continue
            if not _territory_covers(territories, territory):
                pv.reasons.append(f"授权 {grant['grant_ref']} 不覆盖地域 {territory}")
                continue
            if is_minor:
                guardian = db.execute(
                    "SELECT guardian_ref FROM persons WHERE person_ref = ?", (person_ref,)
                ).fetchone()
                if not guardian or not guardian["guardian_ref"]:
                    pv.reasons.append("未成年人缺少监护关系，无法由监护人授权")
                    continue
                if grant["granted_by_ref"] != guardian["guardian_ref"]:
                    pv.reasons.append(
                        f"授权 {grant['grant_ref']} 非监护人 {guardian['guardian_ref']} 授予"
                    )
                    continue
            pv.matched_grants.append(grant["grant_ref"])

        pv.valid = len(pv.matched_grants) > 0
        if not pv.matched_grants and not pv.reasons:
            pv.reasons.append("在该时点无有效的人物授权")
        verdicts.append(pv)

    # 最严格合并：所有“需要授权”的人都必须满足
    overall = all((not p.required) or p.valid for p in verdicts)
    return Verdict(
        valid=overall,
        at=at,
        purpose=purpose,
        channel=channel,
        territory=territory,
        version_ref=version_ref,
        persons=verdicts,
    )
