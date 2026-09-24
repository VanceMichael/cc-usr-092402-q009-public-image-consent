"""影像授权核对的端到端流程测试。

覆盖：多人最严格合并、未成年人监护、裁剪/打码/合成/字幕继承重算、
利益冲突审核、方案修订、迟到授权不倒改历史、撤回仅冻结相关渠道、
对外布尔核验与内部证据重放。
"""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = str(Path(self.tmp.name) / "test.sqlite3")
        from app.main import create_app
        self.client = create_app().test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("DATABASE_PATH", None)

    # ---------------------------------------------------------- 辅助

    def post(self, path: str, body: dict, expected: int = 201) -> dict:
        resp = self.client.post(path, json=body)
        self.assertEqual(resp.status_code, expected, resp.get_data(as_text=True))
        return resp.get_json()

    def get(self, path: str, expected: int = 200) -> dict:
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, expected, resp.get_data(as_text=True))
        return resp.get_json()

    T_EARLY = "2026-01-05T08:00:00+00:00"
    T_WINTER = "2026-02-01T08:00:00+00:00"
    T_SPRING = "2026-03-01T09:00:00+00:00"
    T_SPRING2 = "2026-03-05T09:00:00+00:00"
    T_SUMMER = "2026-06-01T09:00:00+00:00"

    def seed_museum(self) -> dict:
        """博物馆合影：P1 成年观众（仅社媒/CN）、P2 未成年人（监护人，全用途）、
        P3 临时演员（全开放）。"""
        c = self.client
        self.post("/admin/works", {
            "work_ref": "W1", "author_ref": "AUTHOR-A",
            "source_digest": digest("raw-W1"), "title": "开幕合影",
            "recorded_at": "2026-01-01T10:00:00+00:00",
        })
        self.post("/admin/sessions", {
            "session_ref": "S1", "work_ref": "W1",
            "shot_at": "2026-01-01T10:00:00+00:00", "locality": "CN-HZ",
        })
        for ref, minor in [("P1", False), ("P2", True), ("P3", False), ("GUARD-P2", False)]:
            self.post("/admin/persons", {"person_ref": ref, "is_minor": minor})
        claims = [
            ("P1", "audience", [0.0, 0.0, 0.30, 0.50]),
            ("P2", "subject", [0.35, 0.10, 0.65, 0.80]),
            ("P3", "extra", [0.70, 0.20, 0.95, 0.90]),
        ]
        for person, role, box in claims:
            self.post("/admin/claims", {
                "work_ref": "W1", "person_ref": person, "role": role,
                "bbox": box, "declared_by": "STAFF-1",
            })
        self.post("/admin/guardianships", {
            "child_ref": "P2", "guardian_ref": "GUARD-P2", "relation": "parent",
            "evidence_digest": digest("guard-doc"), "valid_from": "2025-01-01T00:00:00+00:00",
            "recorded_at": self.T_EARLY,
        })
        g1 = self.post("/admin/grants", {
            "person_ref": "P1", "session_ref": "S1",
            "purposes": ["promotion"], "channels": ["social_media"], "regions": ["CN"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_until": "2027-01-01T00:00:00+00:00",
            "granted_by": "P1", "recorded_at": self.T_EARLY,
        })["grant_id"]
        g2 = self.post("/admin/grants", {
            "person_ref": "P2", "session_ref": "S1",
            "purposes": ["promotion", "exhibition"], "channels": ["*"], "regions": ["*"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_until": "2027-01-01T00:00:00+00:00",
            "granted_by": "GUARD-P2", "recorded_at": self.T_EARLY,
        })["grant_id"]
        g3 = self.post("/admin/grants", {
            "person_ref": "P3",
            "purposes": ["*"], "channels": ["*"], "regions": ["*"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "granted_by": "P3", "recorded_at": self.T_EARLY,
        })["grant_id"]

        original = self.post("/admin/versions", {
            "version_id": "V1-ORIG", "kind": "original", "work_ref": "W1",
            "digest": digest("V1-ORIG"), "created_by": "EDITOR-1",
        })
        # 社媒裁剪：只含 P1、P2，裁掉临时演员 P3
        social = self.post("/admin/versions", {
            "version_id": "V1-SOCIAL", "kind": "crop", "parent_version_id": "V1-ORIG",
            "spec": {"bbox": [0.0, 0.0, 0.70, 1.0]},
            "digest": digest("V1-SOCIAL"), "created_by": "EDITOR-1",
        })["version"]
        # 线下展览：打码遮蔽观众 P1，保留 P2、P3
        onsite = self.post("/admin/versions", {
            "version_id": "V1-ONSITE", "kind": "mask", "parent_version_id": "V1-ORIG",
            "spec": {"regions": [[0.0, 0.0, 0.34, 0.55]]},
            "digest": digest("V1-ONSITE"), "created_by": "EDITOR-1",
        })["version"]
        return {"grants": {"P1": g1, "P2": g2, "P3": g3},
                "versions": {"original": original["version"], "social": social, "onsite": onsite}}

    def approve_and_publish(self, *, version_id: str, purpose: str, channel: str,
                            region: str = "CN", use_until: str | None = None,
                            decided_at: str, published_at: str,
                            submitter: str = "CURATOR-1",
                            reviewer: str = "REVIEWER-X") -> dict:
        proposal = self.post("/admin/proposals", {
            "version_id": version_id, "purpose": purpose, "channel": channel,
            "region": region, "use_from": decided_at, "use_until": use_until,
            "submitted_by": submitter,
        })
        pid = proposal["proposal_id"]
        self.post(f"/admin/proposals/{pid}/decisions", {
            "reviewer_ref": reviewer, "decision": "approved", "decided_at": decided_at,
        }, expected=200)
        pub = self.post("/admin/publications", {"proposal_id": pid, "published_at": published_at})
        return {"proposal_id": pid, **pub}

    def verify(self, version_id: str, purpose: str, channel: str, region: str, as_of: str) -> dict:
        from urllib.parse import urlencode
        qs = urlencode({"version_id": version_id, "purpose": purpose,
                        "channel": channel, "region": region, "as_of": as_of})
        return self.get(f"/verify?{qs}")

    # ---------------------------------------------------------- 基础

    def test_health_still_ok(self) -> None:
        self.assertEqual(self.get("/health"), {"status": "ok"})

    def test_invalid_inputs_are_structured_400(self) -> None:
        resp = self.client.post("/admin/works", json={
            "work_ref": "W", "author_ref": "A",
            "source_digest": "md5:bad", "recorded_at": "2026-01-01",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_request")

    # ---------------------------------------------------------- 多人最严格合并

    def test_multi_person_requires_everyone_licensed(self) -> None:
        self.seed_museum()
        # 原件含三人；对海外地域 EU：P1 仅 CN，P2/P3 开放 -> 整体 blocked
        result = self.verify("V1-ORIG", "promotion", "social_media", "EU", self.T_SPRING)
        self.assertFalse(result["valid"])
        # CN 社媒：三人都覆盖
        result = self.verify("V1-ORIG", "promotion", "social_media", "CN", self.T_SPRING)
        self.assertTrue(result["valid"])
        # 线下展览用途：P1 的授权只有 promotion
        result = self.verify("V1-ORIG", "exhibition", "onsite", "CN", self.T_SPRING)
        self.assertFalse(result["valid"])

    def test_minor_requires_guardian_signed_grant(self) -> None:
        self.post("/admin/works", {
            "work_ref": "W2", "author_ref": "AUTHOR-B",
            "source_digest": digest("raw-W2"), "recorded_at": "2026-01-01T10:00:00+00:00",
        })
        self.post("/admin/persons", {"person_ref": "P4", "is_minor": True})
        self.post("/admin/claims", {
            "work_ref": "W2", "person_ref": "P4", "role": "audience",
            "bbox": [0.1, 0.1, 0.5, 0.5], "declared_by": "STAFF-1",
        })
        # 陌生人代签，即使授权范围齐全也无效
        self.post("/admin/grants", {
            "person_ref": "P4", "purposes": ["*"], "channels": ["*"], "regions": ["*"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "granted_by": "STRANGER", "recorded_at": self.T_EARLY,
        })
        self.post("/admin/versions", {
            "kind": "original", "work_ref": "W2",
            "digest": digest("V2"), "created_by": "EDITOR-1",
        })
        result = self.verify(self.latest_version("W2"), "promotion", "social_media",
                             "CN", self.T_SPRING)
        self.assertFalse(result["valid"])

    def latest_version(self, work_ref: str) -> str:
        import sqlite3
        from app import store
        conn = store.connect(os.environ["DATABASE_PATH"])
        try:
            row = conn.execute(
                "SELECT version_id FROM versions WHERE work_ref = ? ORDER BY created_at",
                (work_ref,),
            ).fetchall()
            return row[-1][0]
        finally:
            conn.close()

    # ---------------------------------------------------------- 派生版本

    def test_crop_drops_excluded_person(self) -> None:
        self.seed_museum()
        # 社媒裁剪已剔除 P3，但 P1 只授权 promotion/social_media/CN：
        result = self.verify("V1-SOCIAL", "promotion", "social_media", "CN", self.T_SPRING)
        self.assertTrue(result["valid"])
        # 同一裁剪用于海外巡展渠道，P1 的 CN/社媒授权均不覆盖
        result = self.verify("V1-SOCIAL", "promotion", "overseas_tour", "EU", self.T_SPRING)
        self.assertFalse(result["valid"])

    def test_mask_then_subtitle_reintroduces_person(self) -> None:
        self.seed_museum()
        # P1 被打码：即便没有展览授权，现场渠道也有效
        self.assertTrue(self.verify("V1-ONSITE", "exhibition", "onsite", "CN", self.T_SPRING)["valid"])
        # 字幕替换具名 P1：文字可识别，授权要求恢复
        self.post("/admin/versions", {
            "version_id": "V1-SUB", "kind": "subtitle", "parent_version_id": "V1-ONSITE",
            "spec": {"mentions": ["P1"], "replaced_text": "感谢王女士",
                     "replacement_text": "感谢 P1"},
            "digest": digest("V1-SUB"), "created_by": "EDITOR-1",
        })
        self.assertFalse(self.verify("V1-SUB", "exhibition", "onsite", "CN", self.T_SPRING)["valid"])

    def test_composite_takes_strictest_union(self) -> None:
        self.seed_museum()
        # 第二件作品：P6 无任何授权
        self.post("/admin/works", {
            "work_ref": "W3", "author_ref": "AUTHOR-C",
            "source_digest": digest("raw-W3"), "recorded_at": "2026-02-01T10:00:00+00:00",
        })
        self.post("/admin/persons", {"person_ref": "P6", "is_minor": False})
        self.post("/admin/claims", {
            "work_ref": "W3", "person_ref": "P6", "role": "subject",
            "bbox": [0.1, 0.1, 0.5, 0.5], "declared_by": "STAFF-2",
        })
        self.post("/admin/versions", {
            "version_id": "V3-ORIG", "kind": "original", "work_ref": "W3",
            "digest": digest("V3-ORIG"), "created_by": "EDITOR-2",
        })
        comp = self.post("/admin/versions", {
            "version_id": "V-COMP-BAD", "kind": "composite", "work_ref": "W1",
            "sources": [
                {"source_version_id": "V1-SOCIAL", "placement": [0.0, 0.0, 1.0, 1.0]},
                {"source_version_id": "V3-ORIG", "placement": [0.0, 0.0, 0.25, 0.25]},
            ],
            "digest": digest("V-COMP-BAD"), "created_by": "EDITOR-1",
        })["version"]
        self.assertFalse(self.verify(comp["version_id"], "promotion", "social_media",
                                     "CN", self.T_SPRING)["valid"])
        # 先把 P6 打码，再合成即有效
        self.post("/admin/versions", {
            "version_id": "V3-MASKED", "kind": "mask", "parent_version_id": "V3-ORIG",
            "spec": {"regions": [[0.0, 0.0, 1.0, 1.0]]},
            "digest": digest("V3-MASKED"), "created_by": "EDITOR-2",
        })
        comp_ok = self.post("/admin/versions", {
            "version_id": "V-COMP-OK", "kind": "composite", "work_ref": "W1",
            "sources": [
                {"source_version_id": "V1-SOCIAL", "placement": [0.0, 0.0, 1.0, 1.0]},
                {"source_version_id": "V3-MASKED", "placement": [0.0, 0.0, 0.25, 0.25]},
            ],
            "digest": digest("V-COMP-OK"), "created_by": "EDITOR-1",
        })["version"]
        self.assertTrue(self.verify(comp_ok["version_id"], "promotion", "social_media",
                                    "CN", self.T_SPRING)["valid"])

    # ---------------------------------------------------------- 审批链

    def test_conflicted_reviewer_rejected(self) -> None:
        self.seed_museum()
        proposal = self.post("/admin/proposals", {
            "version_id": "V1-SOCIAL", "purpose": "promotion",
            "channel": "social_media", "region": "CN",
            "use_from": self.T_SPRING, "submitted_by": "CURATOR-1",
        })
        pid = proposal["proposal_id"]
        # 提交人自审
        resp = self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "CURATOR-1", "decision": "approved", "decided_at": self.T_SPRING,
        })
        self.assertEqual(resp.status_code, 409)
        # 在场人物/授权人自审
        resp = self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "P1", "decision": "approved", "decided_at": self.T_SPRING,
        })
        self.assertEqual(resp.status_code, 409)
        # 作者自审
        resp = self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "AUTHOR-A", "decision": "approved", "decided_at": self.T_SPRING,
        })
        self.assertEqual(resp.status_code, 409)

    def test_revision_history_and_immutability(self) -> None:
        self.seed_museum()
        proposal = self.post("/admin/proposals", {
            "version_id": "V1-SOCIAL", "purpose": "promotion",
            "channel": "social_media", "region": "CN",
            "use_from": self.T_SPRING, "submitted_by": "CURATOR-1",
        })
        pid = proposal["proposal_id"]
        self.post(f"/admin/proposals/{pid}/revisions", {
            "editor_ref": "CURATOR-1",
            "patch": {"region": "EU", "use_until": "2026-12-31T00:00:00+00:00"},
        }, expected=200)
        chain = self.get(f"/admin/proposals/{pid}/chain")
        self.assertEqual(chain["proposal"]["scope"]["region"], "EU")
        self.assertEqual(len(chain["revisions"]), 1)
        # 改成 EU 后门控失败，不能批准
        resp = self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "REVIEWER-X", "decision": "approved", "decided_at": self.T_SPRING,
        })
        self.assertEqual(resp.status_code, 409)
        # 改回 CN 再批准
        self.post(f"/admin/proposals/{pid}/revisions", {
            "editor_ref": "CURATOR-2", "patch": {"region": "CN"},
        }, expected=200)
        self.post(f"/admin/proposals/{pid}/decisions", {
            "reviewer_ref": "REVIEWER-X", "decision": "approved", "decided_at": self.T_SPRING,
        }, expected=200)
        # 已批准不可再编辑、不可重复审批
        self.assertEqual(self.client.post(f"/admin/proposals/{pid}/revisions", json={
            "editor_ref": "CURATOR-1", "patch": {"region": "JP"},
        }).status_code, 409)
        self.assertEqual(self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "REVIEWER-Y", "decision": "approved",
        }).status_code, 409)

    def test_pending_or_rejected_proposal_cannot_publish(self) -> None:
        self.seed_museum()
        proposal = self.post("/admin/proposals", {
            "version_id": "V1-SOCIAL", "purpose": "promotion",
            "channel": "social_media", "region": "CN",
            "use_from": self.T_SPRING, "submitted_by": "CURATOR-1",
        })
        pid = proposal["proposal_id"]
        self.assertEqual(self.client.post("/admin/publications",
                                          json={"proposal_id": pid}).status_code, 409)
        self.post(f"/admin/proposals/{pid}/decisions", {
            "reviewer_ref": "REVIEWER-X", "decision": "rejected",
        }, expected=200)
        self.assertEqual(self.client.post("/admin/publications",
                                          json={"proposal_id": pid}).status_code, 409)

    def test_use_period_cannot_exceed_grant_expiry(self) -> None:
        self.seed_museum()
        proposal = self.post("/admin/proposals", {
            "version_id": "V1-SOCIAL", "purpose": "promotion",
            "channel": "social_media", "region": "CN",
            "use_from": self.T_SPRING, "use_until": "2028-01-01T00:00:00+00:00",
            "submitted_by": "CURATOR-1",
        })
        pid = proposal["proposal_id"]
        resp = self.client.post(f"/admin/proposals/{pid}/decisions", json={
            "reviewer_ref": "REVIEWER-X", "decision": "approved", "decided_at": self.T_SPRING,
        })
        self.assertEqual(resp.status_code, 409)

    # ---------------------------------------------------------- 迟到授权

    def test_late_grant_does_not_retroactively_change_past(self) -> None:
        self.seed_museum()
        # W3/P6 在 2 月没有授权
        self.post("/admin/works", {
            "work_ref": "W3", "author_ref": "AUTHOR-C",
            "source_digest": digest("raw-W3"), "recorded_at": "2026-02-01T10:00:00+00:00",
        })
        self.post("/admin/persons", {"person_ref": "P6", "is_minor": False})
        self.post("/admin/claims", {
            "work_ref": "W3", "person_ref": "P6", "role": "subject",
            "bbox": [0.1, 0.1, 0.5, 0.5], "declared_by": "STAFF-2",
        })
        self.post("/admin/versions", {
            "version_id": "V3-ORIG", "kind": "original", "work_ref": "W3",
            "digest": digest("V3-ORIG"), "created_by": "EDITOR-2",
        })
        self.assertFalse(self.verify("V3-ORIG", "promotion", "social_media",
                                     "CN", self.T_WINTER)["valid"])
        # 6 月才补登记的授权，即便 valid_from 回填到 1 月，2 月时点仍不可用
        late_grant = self.post("/admin/grants", {
            "person_ref": "P6", "purposes": ["promotion"], "channels": ["social_media"],
            "regions": ["CN"], "valid_from": "2026-01-01T00:00:00+00:00",
            "granted_by": "P6", "recorded_at": self.T_SUMMER,
        })["grant_id"]
        self.assertFalse(self.verify("V3-ORIG", "promotion", "social_media",
                                     "CN", self.T_WINTER)["valid"])
        self.assertTrue(self.verify("V3-ORIG", "promotion", "social_media",
                                    "CN", "2026-06-02T00:00:00+00:00")["valid"])

    # ---------------------------------------------------------- 发布证据与撤回

    def test_publish_evidence_replay_and_targeted_freeze(self) -> None:
        refs = self.seed_museum()
        social = self.approve_and_publish(
            version_id="V1-SOCIAL", purpose="promotion", channel="social_media",
            decided_at=self.T_SPRING, published_at=self.T_SPRING,
        )
        onsite = self.approve_and_publish(
            version_id="V1-ONSITE", purpose="exhibition", channel="onsite",
            decided_at=self.T_SPRING2, published_at=self.T_SPRING2,
        )
        # 对外核验只暴露布尔结果，不泄露人物/授权细节
        payload = self.verify("V1-SOCIAL", "promotion", "social_media", "CN", self.T_SPRING)
        self.assertEqual(set(payload),
                         {"version_id", "purpose", "channel", "region", "as_of", "valid"})
        self.assertTrue(payload["valid"])

        # 内部证据重放：摘要自洽，含影像摘要、授权范围、遮蔽、批准链
        ev = self.get(f"/admin/publications/{social['publication_id']}/evidence")
        self.assertTrue(ev["recomputed_digest_matches"])
        body = ev["evidence"]
        self.assertEqual(body["version"]["digest"], digest("V1-SOCIAL"))
        self.assertEqual(body["scope"]["channel"], "social_media")
        chain = body["approval_chain"]
        self.assertEqual(len(chain["approvals"]), 1)
        self.assertEqual(chain["approvals"][0]["reviewer_ref"], "REVIEWER-X")
        self.assertTrue(chain["approvals"][0]["conflict_check"]["passed"])

        # 撤回 P1 的授权：只冻结社媒发布，线下展览不受影响
        result = self.post(f"/admin/grants/{refs['grants']['P1']}/revocations", {
            "revoked_at": "2026-04-01T00:00:00+00:00",
            "recorded_at": "2026-04-02T00:00:00+00:00",
            "reason": "观众撤回",
        }, expected=200)
        self.assertEqual(result["frozen_publications"], [social["publication_id"]])
        self.assertEqual(len(result["disposition_items"]), 1)
        self.assertEqual(result["disposition_items"][0]["action"], "take_down_post")

        ev_social = self.get(f"/admin/publications/{social['publication_id']}/evidence")
        self.assertEqual(ev_social["status"], "frozen")
        # 已固化证据不被后来事实改写
        self.assertTrue(ev_social["recomputed_digest_matches"])
        ev_onsite = self.get(f"/admin/publications/{onsite['publication_id']}/evidence")
        self.assertEqual(ev_onsite["status"], "active")

        # 当前时点重算：社媒失效，现场（P1 已遮蔽）仍有效
        self.assertFalse(self.verify("V1-SOCIAL", "promotion", "social_media",
                                     "CN", self.T_SUMMER)["valid"])
        self.assertTrue(self.verify("V1-ONSITE", "exhibition", "onsite",
                                    "CN", self.T_SUMMER)["valid"])

        # 处置清单可闭环
        items = self.get("/admin/dispositions")["items"]
        self.assertEqual(len(items), 1)
        self.post(f"/admin/dispositions/{items[0]['item_id']}/handle", {}, expected=200)
        self.assertEqual(self.get("/admin/dispositions")["items"], [])

    def test_revocation_backdated_still_respects_publish_evidence(self) -> None:
        self.seed_museum()
        pub = self.approve_and_publish(
            version_id="V1-SOCIAL", purpose="promotion", channel="social_media",
            decided_at=self.T_SPRING, published_at=self.T_SPRING,
        )
        # 撤回登记在发布之后，但撤回时点回填到发布之前：
        # 历史发布证据保持冻结动作所依赖的现状；新核验在发布时点依旧反映当时事实
        self.post("/admin/grants/" + self._g1() + "/revocations", {
            "revoked_at": "2026-02-01T00:00:00+00:00",
            "recorded_at": self.T_SUMMER, "reason": "补充撤回",
        }, expected=200)
        ev = self.get(f"/admin/publications/{pub['publication_id']}/evidence")
        self.assertEqual(ev["status"], "frozen")
        # 发布时点的重算不含“事后才登记”的撤回（迟到事实不倒改）
        self.assertTrue(self.verify("V1-SOCIAL", "promotion", "social_media",
                                    "CN", self.T_SPRING)["valid"])
        self.assertFalse(self.verify("V1-SOCIAL", "promotion", "social_media",
                                     "CN", self.T_SUMMER)["valid"])

    def _g1(self) -> str:
        from app import store
        conn = store.connect(os.environ["DATABASE_PATH"])
        try:
            return conn.execute(
                "SELECT grant_id FROM grants WHERE person_ref = 'P1'").fetchone()[0]
        finally:
            conn.close()

    def test_duplicate_active_publication_rejected(self) -> None:
        self.seed_museum()
        pub = self.approve_and_publish(
            version_id="V1-SOCIAL", purpose="promotion", channel="social_media",
            decided_at=self.T_SPRING, published_at=self.T_SPRING,
        )
        resp = self.client.post("/admin/publications", json={"proposal_id": pub["proposal_id"]})
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()
