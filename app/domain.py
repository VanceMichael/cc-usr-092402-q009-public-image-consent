"""影像授权核对的纯领域规则。

不访问数据库、不接触 HTTP：所有函数都对普通字典/元组操作，便于独立测试。
时间一律使用带时区偏移的 datetime；外部交换格式为 ISO 8601 字符串。
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable

# 授权用途
PURPOSES = ("exhibition", "promotion", "archive", "documentation", "education")
# 发布渠道
CHANNELS = ("onsite", "social_media", "overseas_tour")
# 识别声明中的人物角色
ROLES = ("subject", "performer", "extra", "audience", "staff")
# 版本派生类型
VERSION_KINDS = ("original", "crop", "mask", "composite", "subtitle")
# 通配符
WILDCARD = "*"

# 遮蔽区域覆盖人物框面积达到该比例，才视为人物被遮蔽（无法辨认）
MASK_COVER_RATIO = 0.5


class DomainError(ValueError):
    """输入违反领域约定。"""


# ---------------------------------------------------------------- 基础校验

def parse_time(value: str, *, field_name: str = "time") -> datetime:
    if not isinstance(value, str):
        raise DomainError(f"{field_name} 必须是 ISO 8601 字符串")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DomainError(f"{field_name} 不是合法的 ISO 8601 时间：{value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainError(f"{field_name} 必须带时区偏移量：{value}")
    return parsed


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_digest(value: str, *, field_name: str = "digest") -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise DomainError(f"{field_name} 必须形如 sha256:<hex>")
    hexpart = value.split(":", 1)[1]
    if len(hexpart) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
        raise DomainError(f"{field_name} 的 sha256 摘要长度应为 64 位十六进制")
    return value.lower()


def require_choice(value: str, choices: Iterable[str], *, field_name: str) -> str:
    choices = tuple(choices)
    if value not in choices:
        raise DomainError(f"{field_name} 必须是 {choices} 之一，收到：{value!r}")
    return value


def require_str_list(value: Any, *, field_name: str, allow_wildcard: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise DomainError(f"{field_name} 必须是非空字符串组成的数组")
    if not allow_wildcard and WILDCARD in value:
        raise DomainError(f"{field_name} 不允许使用通配符 {WILDCARD}")
    return value


def bbox(value: Any, *, field_name: str = "bbox") -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise DomainError(f"{field_name} 必须是 4 个数字 [x0,y0,x1,y1]")
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise DomainError(f"{field_name} 必须是 4 个数字") from exc
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise DomainError(f"{field_name} 必须落在 0..1 且 x0<x1、y0<y1")
    return x0, y0, x1, y1


# ---------------------------------------------------------------- 版本在场推导

@dataclass(frozen=True)
class Presence:
    """单个（子）作品上的一个人物在场状态。bbox 为当前画面坐标系下的位置；None 表示位置未知。"""

    person_ref: str
    role: str
    bbox: tuple[float, float, float, float] | None
    masked: bool = False
    details: tuple[str, ...] = ()

    def annotate(self, note: str) -> "Presence":
        return replace(self, details=(*self.details, note))


def _intersection_area(a: tuple[float, float, float, float],
                       b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    h = max(0.0, min(ay1, by1) - max(ay0, by0))
    return w * h


def _area(a: tuple[float, float, float, float]) -> float:
    return (a[2] - a[0]) * (a[3] - a[1])


def _remap(point_box: tuple[float, float, float, float],
           frame: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """把 frame 子画面内的局部坐标映射到父画面（合成贴片使用）。"""
    x0, y0, x1, y1 = point_box
    fx0, fy0, fx1, fy1 = frame
    fw, fh = fx1 - fx0, fy1 - fy0
    return (
        min(1.0, max(0.0, fx0 + x0 * fw)),
        min(1.0, max(0.0, fy0 + y0 * fh)),
        min(1.0, max(0.0, fx0 + x1 * fw)),
        min(1.0, max(0.0, fy0 + y1 * fh)),
    )


def _inv_remap(point_box: tuple[float, float, float, float],
               frame: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """把父画面坐标换算进 frame 裁剪后的新画面（裁剪使用）。"""
    x0, y0, x1, y1 = point_box
    fx0, fy0, fx1, fy1 = frame
    fw, fh = fx1 - fx0, fy1 - fy0
    return (
        min(1.0, max(0.0, (x0 - fx0) / fw)),
        min(1.0, max(0.0, (y0 - fy0) / fh)),
        min(1.0, max(0.0, (x1 - fx0) / fw)),
        min(1.0, max(0.0, (y1 - fy0) / fh)),
    )


def derive_crop(parents: list[Presence], spec: dict) -> list[Presence]:
    """裁剪：只保留与裁剪框相交的人物，坐标重映射到裁剪后的画面。"""
    frame = bbox(spec.get("bbox"), field_name="spec.bbox")
    out: list[Presence] = []
    for p in parents:
        if p.bbox is None:
            # 位置未知的识别声明保守保留，且仍无法定位
            out.append(p.annotate(f"裁剪{_fmt_bbox(frame)}：位置未知，保守保留"))
            continue
        if _intersection_area(p.bbox, frame) > 0:
            out.append(replace(p, bbox=_inv_remap(p.bbox, frame)))
    return out


def derive_mask(parents: list[Presence], spec: dict) -> list[Presence]:
    """打码：遮蔽框覆盖人物框面积达到阈值时该人物记为已遮蔽；无法辨认则无需授权。"""
    regions_raw = spec.get("regions", [])
    if not isinstance(regions_raw, list) or not regions_raw:
        raise DomainError("spec.regions 必须是非空数组")
    regions = [bbox(r, field_name="spec.regions[]") for r in regions_raw]
    full_frame = (0.0, 0.0, 1.0, 1.0)
    out: list[Presence] = []
    for p in parents:
        already = p.masked
        if not already:
            if p.bbox is None:
                covered = full_frame in regions or any(r == full_frame for r in regions)
            else:
                covered = any(
                    _intersection_area(p.bbox, r) / _area(p.bbox) >= MASK_COVER_RATIO
                    for r in regions
                )
            if covered:
                p = replace(p, masked=True)
        note = f"打码{[ _fmt_bbox(r) for r in regions ]}" + ("：已遮蔽" if p.masked and not already else "")
        out.append(p.annotate(note) if (p.masked and not already) else p)
    return out


def derive_subtitle(parents: list[Presence], spec: dict, *, roles_lookup) -> list[Presence]:
    """字幕替换：字幕具名提到的人物即使画面被遮蔽，仍因文字可识别而需要授权。"""
    mentions = spec.get("mentions", [])
    if not isinstance(mentions, list) or not all(isinstance(m, str) for m in mentions):
        raise DomainError("spec.mentions 必须是 person_ref 字符串数组")
    replaced = spec.get("replaced_text")
    replacement = spec.get("replacement_text")
    out = list(parents)
    note = f"字幕替换：{replaced!r} -> {replacement!r}"
    by_person = {p.person_ref: i for i, p in enumerate(out)}
    for person_ref in mentions:
        role = roles_lookup(person_ref)
        if person_ref in by_person:
            idx = by_person[person_ref]
            p = out[idx]
            # 文字署名直接破除遮蔽
            out[idx] = replace(p, masked=False).annotate(note + "；字幕具名，遮蔽不解除授权要求")
        else:
            out.append(Presence(
                person_ref=person_ref, role=role, bbox=None, masked=False,
                details=(note + "；仅字幕具名",),
            ))
    return out


def derive_composite(sources: list[tuple[dict, list[Presence]]]) -> list[Presence]:
    """合成：把每个来源版本按 placement 贴入新画面，多人出现取并集；
    同一人在多个来源中出现时，只要有一处未遮蔽即可辨认（masked 取 AND）。"""
    merged: dict[str, Presence] = {}
    for src_spec, entries in sources:
        placement = bbox(src_spec.get("placement"), field_name="sources[].placement")
        for p in entries:
            box = placement if p.bbox is None else _remap(p.bbox, placement)
            candidate = replace(p, bbox=box).annotate(f"合成自来源，贴片{_fmt_bbox(placement)}")
            if p.person_ref not in merged:
                merged[p.person_ref] = candidate
            else:
                old = merged[p.person_ref]
                merged[p.person_ref] = Presence(
                    person_ref=p.person_ref,
                    role=old.role,
                    bbox=None if old.bbox is None or candidate.bbox is None else old.bbox,
                    masked=old.masked and candidate.masked,
                    details=old.details + candidate.details,
                )
    return list(merged.values())


def derive_original(claims: list[dict]) -> list[Presence]:
    return [
        Presence(
            person_ref=c["person_ref"],
            role=c["role"],
            bbox=bbox(c["bbox"]) if c.get("bbox") is not None else None,
            details=("原始识别声明",),
        )
        for c in claims
    ]


def _fmt_bbox(b: tuple[float, float, float, float]) -> str:
    return "[" + ",".join(f"{v:.3f}" for v in b) + "]"


# ---------------------------------------------------------------- 授权时点判定

def _list_contains(allowed: list[str], value: str) -> bool:
    return WILDCARD in allowed or value in allowed


def grant_is_effective(grant: dict, at: datetime) -> bool:
    """授权在 at 时点是否存在且未到期、未撤回。撤回时点 <= at 即视为已撤回。"""
    if parse_time(grant["valid_from"], field_name="grant.valid_from") > at:
        return False
    if grant.get("valid_until"):
        if parse_time(grant["valid_until"], field_name="grant.valid_until") <= at:
            return False
    revoked_at = grant.get("revoked_at")
    if revoked_at and parse_time(revoked_at, field_name="grant.revoked_at") <= at:
        return False
    return True


def guardianship_active(guardianship: dict, at: datetime) -> bool:
    if parse_time(guardianship["valid_from"], field_name="guardianship.valid_from") > at:
        return False
    if guardianship.get("valid_to") and parse_time(
        guardianship["valid_to"], field_name="guardianship.valid_to"
    ) <= at:
        return False
    return True


def grant_covers(grant: dict, *, person_ref: str, purpose: str, channel: str,
                 region: str, at: datetime,
                 match_sessions: set[str] | None = None) -> bool:
    """授权是否覆盖指定人物在 at 时点的（用途、渠道、地域）使用；不含监护资格判断。
    match_sessions 为该作品所属场次集合；场次授权只在命中其中一场时生效。"""
    if grant["person_ref"] != person_ref:
        return False
    session_ref = grant.get("session_ref")
    if session_ref is not None and match_sessions is not None and session_ref not in match_sessions:
        return False
    if not grant_is_effective(grant, at):
        return False
    if not _list_contains(grant["purposes"], purpose):
        return False
    if not _list_contains(grant["channels"], channel):
        return False
    if not _list_contains(grant["regions"], region):
        return False
    return True


def evaluate_person(*, person: dict, presence: Presence,
                    match_sessions: set[str] | None,
                    grants: list[dict], guardianships: list[dict],
                    purpose: str, channel: str, region: str,
                    at: datetime) -> dict:
    """单个在场人物的判定。已遮蔽 -> obscured；否则必须有覆盖该用途且签署人合格的授权。"""
    if presence.masked:
        return {
            "person_ref": presence.person_ref,
            "role": presence.role,
            "status": "obscured",
            "matched_grants": [],
            "grantors": [],
            "detail": "；".join(presence.details),
        }

    person_ref = presence.person_ref
    matched: list[dict] = []
    for g in grants:
        if not grant_covers(g, person_ref=person_ref, purpose=purpose,
                            channel=channel, region=region, at=at,
                            match_sessions=match_sessions):
            continue
        if bool(person.get("is_minor")):
            # 未成年人：授权必须由在 at 时点监护关系有效的监护人签署
            if not any(
                guard["guardian_ref"] == g["granted_by"] and guardianship_active(guard, at)
                for guard in guardianships
                if guard["child_ref"] == person_ref
            ):
                continue
        elif g["granted_by"] != person_ref:
            # 成年人授权只能由本人签署
            continue
        matched.append(g)

    if not matched:
        return {
            "person_ref": person_ref,
            "role": presence.role,
            "status": "blocked",
            "matched_grants": [],
            "grantors": [],
            "detail": "；".join(presence.details) or "在场且可辨认，但没有覆盖该用途/渠道/地域/时点的有效授权",
        }

    untils = [g["valid_until"] for g in matched if g.get("valid_until")]
    return {
        "person_ref": person_ref,
        "role": presence.role,
        "status": "granted",
        "matched_grants": sorted(g["grant_id"] for g in matched),
        "grantors": sorted({g["granted_by"] for g in matched}),
        "earliest_valid_until": min(untils, key=lambda s: parse_time(s)) if untils else None,
        "detail": "；".join(presence.details),
    }


def merge_gates(gates: list[dict]) -> dict:
    """多人影像合并最严格条件：任一人 blocked 即整体 blocked；
    有效期取所有在场人物授权的最早到期；授权取并集用于追溯。"""
    visible = [g for g in gates if g["status"] != "obscured"]
    blockers = [g for g in visible if g["status"] == "blocked"]
    if blockers:
        return {
            "valid": False,
            "reason": "unlicensed_persons",
            "blocked_persons": [g["person_ref"] for g in blockers],
        }
    if not visible:
        return {"valid": True, "reason": "no_identifiable_person", "earliest_valid_until": None}
    untils = [g["earliest_valid_until"] for g in visible if g.get("earliest_valid_until")]
    return {
        "valid": True,
        "reason": "all_persons_licensed",
        "earliest_valid_until": min(untils, key=lambda s: parse_time(s)) if untils else None,
    }


# ---------------------------------------------------------------- 证据摘要

def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_digest(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
