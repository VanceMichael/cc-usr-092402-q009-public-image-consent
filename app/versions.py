
"""版本派生：裁剪/打码/合成/字幕替换继承并重新计算人物可见状态。

每个版本落库时即重算 version_persons（visible/masked），
从而把“该版本对谁构成可识别使用”固化为可追溯事实。
"""

from .errors import bad_request, not_found

TERRITORY_ALL = "ALL"


def _regions_overlap(a: dict, b: dict) -> bool:
    """归一化矩形相交判定；缺省坐标视为整幅覆盖（保守相交）。"""
    if not a or not b:
        return True
    ax1, ay1 = a.get("x", 0.0), a.get("y", 0.0)
    ax2, ay2 = ax1 + a.get("w", 1.0), ay1 + a.get("h", 1.0)
    bx1, by1 = b.get("x", 0.0), b.get("y", 0.0)
    bx2, by2 = bx1 + b.get("w", 1.0), by1 + b.get("h", 1.0)
    return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


def _row_exists(db, table: str, ref: str, column: str) -> bool:
    return db.execute(
        f"SELECT 1 FROM {table} WHERE {column} = ?", (ref,)
    ).fetchone() is not None


def _persons_of(db, version_ref: str) -> dict[str, dict]:
    """返回某版本当前人物状态 {person_ref: {state, region}}。"""
    rows = db.execute(
        "SELECT person_ref, state, region_json FROM version_persons WHERE version_ref = ?",
        (version_ref,),
    ).fetchall()
    import json

    return {
        row["person_ref"]: {"state": row["state"], "region": json.loads(row["region_json"] or "{}")}
        for row in rows
    }


def _replace_persons(db, version_ref: str, persons: dict[str, dict]) -> None:
    import json

    db.execute("DELETE FROM version_persons WHERE version_ref = ?", (version_ref,))
    for person_ref, info in persons.items():
        db.execute(
            "INSERT INTO version_persons(version_ref, person_ref, state, region_json)"
            " VALUES (?, ?, ?, ?)",
            (version_ref, person_ref, info["state"], json.dumps(info.get("region", {}), ensure_ascii=False)),
        )


def derive_persons(db, parent_ref: str | None, kind: str, params: dict) -> dict[str, dict]:
    """根据父版本与变换参数，重算新版本的人物可见状态。"""
    if kind == "original":
        return {}

    if kind == "composite":
        sources = params.get("sources") or []
        if not sources:
            raise bad_request("合成版本至少需要一个来源版本", {"field": "sources"})
        merged: dict[str, dict] = {}
        for source_ref in sources:
            if not _row_exists(db, "versions", source_ref, "version_ref"):
                raise not_found(f"来源版本不存在：{source_ref}")
            for person_ref, info in _persons_of(db, source_ref).items():
                if info["state"] == "masked":
                    continue
                # 多个来源只要有一处可识别，合成就可识别（最严格）
                merged.setdefault(person_ref, info)
        return merged

    if parent_ref is None:
        raise bad_request(f"{kind} 版本必须指定父版本", {"field": "parent_version_ref"})
    if not _row_exists(db, "versions", parent_ref, "version_ref"):
        raise not_found(f"父版本不存在：{parent_ref}")

    parents = _persons_of(db, parent_ref)

    if kind == "crop":
        crop = params.get("region") or {}
        kept = {
            person_ref: {"state": "visible", "region": _rebase_region(info.get("region", {}), crop)}
            for person_ref, info in parents.items()
            if info["state"] == "visible" and _regions_overlap(info.get("region", {}), crop)
        }
        return kept

    if kind == "mask":
        masks = params.get("regions") or []
        result = dict(parents)
        for mask in masks:
            target = mask.get("person_ref")
            area = mask.get("region") or {}
            for person_ref, info in result.items():
                if info["state"] != "visible":
                    continue
                if target is not None:
                    if person_ref == target:
                        info["state"] = "masked"
                elif _regions_overlap(info.get("region", {}), area):
                    info["state"] = "masked"
        return result

    if kind == "subtitle":
        # 字幕替换不改变人物可识别性，原样继承（仍重算并固化）
        return dict(parents)

    raise bad_request(f"未知版本类型：{kind}")


def _rebase_region(region: dict, crop: dict) -> dict:
    """把原图归一化坐标换算到裁剪后坐标系；无坐标信息时保留空区域。"""
    if not region or not crop or crop.get("w") in (None, 0) or crop.get("h") in (None, 0):
        return {}
    cx, cy, cw, ch = crop["x"], crop["y"], crop["w"], crop["h"]
    x1 = max(0.0, (region.get("x", 0.0) - cx) / cw)
    y1 = max(0.0, (region.get("y", 0.0) - cy) / ch)
    x2 = min(1.0, (region.get("x", 0.0) + region.get("w", 1.0) - cx) / cw)
    y2 = min(1.0, (region.get("y", 0.0) + region.get("h", 1.0) - cy) / ch)
    return {"x": x1, "y": y1, "w": max(0.0, x2 - x1), "h": max(0.0, y2 - y1)}
