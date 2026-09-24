
import json
import tempfile
import unittest
from pathlib import Path

from app.main import create_app

PURPOSE = "public_display"
DIGEST = "sha256:" + "a" * 64


class AuthorizationFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.sqlite3")
        import os
        os.environ["DATABASE_PATH"] = self.db_path
        self.app = create_app(auto_migrate=True)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------------------------------------------------------------ helpers
    def post(self, path: str, payload: dict, expected: int = 201):
        resp = self.client.post(path, json=payload)
        self.assertEqual(
            resp.status_code, expected,
            f"{path} -> {resp.status_code}: {resp.get_data(as_text=True)}",
        )
        return resp.get_json()

    def seed(self, grants: bool = True) -> None:
        self.post("/v1/registry/sessions", {
            "session_ref": "S1", "shot_at": "2026-01-01T10:00:00+08:00",
            "location_ref": "HALL-A",
        })
        self.post("/v1/registry/works", {
            "work_ref": "W1", "session_ref": "S1", "author_ref": "AUTHOR-1",
            "digest": DIGEST, "captured_at": "2026-01-01T10:05:00+08:00",
        })
        # 监护人、未成年人、临时演员、普通观众
        self.post("/v1/registry/persons", {"person_ref": "P-GUARD", "kind": "staff"})
        self.post("/v1/registry/persons", {
            "person_ref": "P-MINOR", "kind": "minor", "guardian_ref": "P-GUARD"})
        self.post("/v1/registry/persons", {"person_ref": "P-EXTRA", "kind": "extra"})
        self.post("/v1/registry/persons", {"person_ref": "P-VISITOR", "kind": "visitor"})
        for ref, person, region in (
            ("R1", "P-MINOR", {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.5}),
            ("R2", "P-EXTRA", {"x": 0.35, "y": 0.0, "w": 0.3, "h": 0.5}),
            ("R3", "P-VISITOR", {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.5}),
        ):
            self.post("/v1/registry/recognitions", {
                "recognition_ref": ref, "work_ref": "W1", "person_ref": person,
                "declared_by": "CURATOR-1", "region": region,
            })
        self.post("/v1/versions", {
            "version_ref": "V-ORIG", "work_ref": "W1", "kind": "original",
            "digest": DIGEST,
        })
        if grants:
            self.post("/v1/registry/grants", {
                "grant_ref": "G-MINOR", "person_ref": "P-MINOR",
                "granted_by_ref": "P-GUARD", "purposes": [PURPOSE],
                "territories": ["CN"], "valid_from": "2026-01-02T00:00:00+08:00",
                "created_at": "2026-01-02T09:00:00+08:00",
            })
            self.post("/v1/registry/grants", {
                "grant_ref": "G-EXTRA", "person_ref": "P-EXTRA",
                "purposes": [PURPOSE], "territories": ["CN", "US"],
                "valid_from": "2026-01-02T00:00:00+08:00",
                "created_at": "2026-01-02T09:00:00+08:00",
            })
            self.post("/v1/registry/grants", {
                "grant_ref": "G-VISITOR", "person_ref": "P-VISITOR",
                "purposes": [PURPOSE], "territories": ["CN"],
                "valid_from": "2026-01-02T00:00:00+08:00",
                "created_at": "2026-01-02T09:00:00+08:00",
            })

    def verify(self, version, territory, channel="social_media", at="2026-03-01T10:00:00+08:00"):
        resp = self.client.post("/v1/verify", json={
            "version_ref": version, "purpose": PURPOSE, "channel": channel,
            "territory": territory, "at": at,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        # 对外核验只返回有效性，不暴露人物/授权细节
        self.assertEqual(set(body), {"version_ref", "purpose", "channel",
                                     "territory", "at", "valid"})
        return body["valid"]

    # ------------------------------------------------------------ tests
    def test_group_photo_merges_strictest_condition(self):
        self.seed()
        # 国内：三人授权齐备
        self.assertTrue(self.verify("V-ORIG", "CN"))
        # 海外：未成年人与观众授权仅限 CN，最严格合并 => 整版无效
        self.assertFalse(self.verify("V-ORIG", "US", channel="overseas_tour"))

        detail = self.client.post("/v1/internal/evaluate", json={
            "version_ref": "V-ORIG", "purpose": PURPOSE, "channel": "overseas_tour",
            "territory": "US", "at": "2026-03-01T10:00:00+08:00",
        }).get_json()
        failed = {p["person_ref"]: p for p in detail["persons"] if not p["valid"]}
        self.assertIn("P-MINOR", failed)
        self.assertIn("P-VISITOR", failed)
        self.assertTrue(next(p for p in detail["persons"] if p["person_ref"] == "P-EXTRA")["valid"])

    def test_minor_requires_guardian_grant(self):
        self.seed(grants=False)
        resp = self.client.post("/v1/registry/grants", json={
            "grant_ref": "G-BAD", "person_ref": "P-MINOR",
            "granted_by_ref": "P-EXTRA", "purposes": [PURPOSE],
            "territories": ["CN"], "valid_from": "2026-01-02T00:00:00+08:00",
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.get_json()["error"]["code"], "unprocessable")

    def test_minor_without_guardian_relation_rejected(self):
        self.seed(grants=False)
        resp = self.client.post("/v1/registry/persons", json={
            "person_ref": "P-LOST-CHILD", "kind": "minor"})
        self.assertEqual(resp.status_code, 422)

    def test_crop_inherits_only_overlapping_persons(self):
        self.seed()
        # 只裁到临时演员区域
        self.post("/v1/versions", {
            "version_ref": "V-CROP", "work_ref": "W1", "kind": "crop",
            "parent_version_ref": "V-ORIG",
            "params": {"region": {"x": 0.34, "y": 0.0, "w": 0.32, "h": 0.5}},
            "digest": "sha256:" + "b" * 64,
        })
        summary = self.client.get("/v1/versions/V-CROP").get_json()
        self.assertEqual([p["person_ref"] for p in summary["persons"]], ["P-EXTRA"])
        # 裁掉了 CN-only 的两人，海外可单独依赖临时演员
        self.assertTrue(self.verify("V-CROP", "US", channel="overseas_tour"))

    def test_mask_removes_authorization_requirement(self):
        self.seed()
        self.post("/v1/versions", {
            "version_ref": "V-MASK", "work_ref": "W1", "kind": "mask",
            "parent_version_ref": "V-ORIG",
            "params": {"regions": [
                {"person_ref": "P-MINOR"}, {"person_ref": "P-VISITOR"}]},
            "digest": "sha256:" + "c" * 64,
        })
        summary = self.client.get("/v1/versions/V-MASK").get_json()
        states = {p["person_ref"]: p["state"] for p in summary["persons"]}
        self.assertEqual(states["P-MINOR"], "masked")
        self.assertEqual(states["P-VISITOR"], "masked")
        # 遮蔽后海外只需临时演员授权（覆盖 US）
        self.assertTrue(self.verify("V-MASK", "US", channel="overseas_tour"))

    def test_subtitle_replacement_inherits_restrictions(self):
        self.seed()
        self.post("/v1/versions", {
            "version_ref": "V-SUB", "work_ref": "W1", "kind": "subtitle",
            "parent_version_ref": "V-ORIG", "params": {"text": "英文标题"},
            "digest": "sha256:" + "d" * 64,
        })
        self.assertTrue(self.verify("V-SUB", "CN"))
        self.assertFalse(self.verify("V-SUB", "US", channel="overseas_tour"))

    def test_composite_merges_sources_strictly(self):
        self.seed()
        # 裁剪版（仅临时演员）+ 原始版 => 合成仍包含全部三人
        self.post("/v1/versions", {
            "version_ref": "V-CROP2", "work_ref": "W1", "kind": "crop",
            "parent_version_ref": "V-ORIG",
            "params": {"region": {"x": 0.34, "y": 0.0, "w": 0.32, "h": 0.5}},
            "digest": "sha256:" + "e" * 64,
        })
        self.post("/v1/versions", {
            "version_ref": "V-COMP", "work_ref": "W1", "kind": "composite",
            "params": {"sources": ["V-CROP2", "V-ORIG"]},
            "digest": "sha256:" + "f" * 64,
        })
        persons = {p["person_ref"] for p in
                   self.client.get("/v1/versions/V-COMP").get_json()["persons"]}
        self.assertEqual(persons, {"P-MINOR", "P-EXTRA", "P-VISITOR"})
        self.assertFalse(self.verify("V-COMP", "US", channel="overseas_tour"))

    def _approved_plan(self, plan_ref="PLAN1", version="V-ORIG", channel="social_media",
                       territory="CN", submitter="EDITOR-1", approver="REVIEWER-1",
                       decide_at="2026-02-20T10:00:00+08:00"):
        self.post("/v1/plans", {
            "plan_ref": plan_ref, "version_ref": version, "purpose": PURPOSE,
            "channel": channel, "territory": territory,
            "submitted_by": submitter, "interested_parties": ["EDITOR-1", "AUTHOR-1"],
        })
        return self.post("/v1/plans/decide", {
            "approval_ref": f"A-{plan_ref}", "plan_ref": plan_ref,
            "approver_ref": approver, "decision": "approved", "decided_at": decide_at,
        }, expected=200)

    def test_submitter_and_interested_party_cannot_approve(self):
        self.seed()
        self.post("/v1/plans", {
            "plan_ref": "PLAN-X", "version_ref": "V-ORIG", "purpose": PURPOSE,
            "channel": "social_media", "territory": "CN", "submitted_by": "EDITOR-1",
            "interested_parties": ["AUTHOR-1"],
        })
        for approver in ("EDITOR-1", "AUTHOR-1"):
            resp = self.client.post("/v1/plans/decide", json={
                "approval_ref": f"A-X-{approver}", "plan_ref": "PLAN-X",
                "approver_ref": approver, "decision": "approved",
                "decided_at": "2026-02-20T10:00:00+08:00",
            })
            self.assertEqual(resp.status_code, 422)
        # 无利益关系审核人可以批准
        self.post("/v1/plans/decide", {
            "approval_ref": "A-X", "plan_ref": "PLAN-X", "approver_ref": "REVIEWER-9",
            "decision": "approved", "decided_at": "2026-02-20T10:00:00+08:00",
        }, expected=200)

    def test_cannot_approve_plan_that_fails_verification(self):
        self.seed()
        self.post("/v1/plans", {
            "plan_ref": "PLAN-US", "version_ref": "V-ORIG", "purpose": PURPOSE,
            "channel": "overseas_tour", "territory": "US", "submitted_by": "EDITOR-1",
        })
        resp = self.client.post("/v1/plans/decide", json={
            "approval_ref": "A-US", "plan_ref": "PLAN-US", "approver_ref": "REVIEWER-1",
            "decision": "approved", "decided_at": "2026-02-20T10:00:00+08:00",
        })
        self.assertEqual(resp.status_code, 422)

    def _publish_social(self):
        self._approved_plan()
        return self.post("/v1/publications", {
            "publication_ref": "PUB1", "plan_ref": "PLAN1",
            "published_at": "2026-03-01T12:00:00+08:00",
        })

    def test_publication_records_immutable_evidence(self):
        self.seed()
        evidence = self._publish_social()
        self.assertEqual(evidence["digest"], DIGEST)
        self.assertEqual(evidence["channel"], "social_media")
        self.assertIn("P-VISITOR", evidence["visible_persons"])
        self.assertEqual(evidence["grant_scope"]["P-VISITOR"][0]["grant_ref"], "G-VISITOR")
        chain = evidence["approval_chain"]
        self.assertEqual(chain[0]["approver_ref"], "REVIEWER-1")
        self.assertEqual(chain[0]["decision"], "approved")

    def test_late_grant_does_not_rewrite_published_evidence(self):
        self.seed()
        before = self._publish_social()
        # 6 月才补登的海外授权（迟到授权）
        self.post("/v1/registry/grants", {
            "grant_ref": "G-MINOR-LATE", "person_ref": "P-MINOR",
            "granted_by_ref": "P-GUARD", "purposes": [PURPOSE],
            "territories": ["US"], "valid_from": "2026-01-02T00:00:00+08:00",
            "created_at": "2026-06-01T09:00:00+08:00",
        })
        replay = self.client.get("/v1/internal/publications/PUB1").get_json()
        # 发布时点重放仍只依据当时存在的授权，结论与证据一致且未被改写
        self.assertTrue(replay["replay_valid_at_publication"])
        self.assertTrue(replay["evidence_consistent"])
        matched = {g for p in replay["replay_verdict"]["persons"] for g in p["matched_grants"]}
        self.assertNotIn("G-MINOR-LATE", matched)
        self.assertEqual(replay["recorded_evidence"], before)

    def test_withdrawal_freezes_only_affected_channel_and_lists_disposition(self):
        self.seed()
        self._publish_social()  # 仅 social_media 渠道有发布
        result = self.post("/v1/withdrawals", {
            "withdrawal_ref": "W1", "grant_ref": "G-VISITOR",
            "withdrawn_at": "2026-05-01T00:00:00+08:00", "reason": "观众撤回",
        }, expected=201)
        self.assertEqual(result["frozen_channels"], ["social_media"])
        self.assertEqual(len(result["disposition"]), 1)
        self.assertEqual(result["disposition"][0]["action"], "takedown")

        # 处置清单可查询
        items = self.client.get("/v1/dispositions?withdrawal_ref=W1").get_json()["items"]
        self.assertEqual([i["publication_ref"] for i in items], ["PUB1"])

        # 撤回后当下核验失效，但历史发布证据重放仍为发布时有效（不可倒改）
        self.assertFalse(self.verify("V-ORIG", "CN", at="2026-05-02T00:00:00+08:00"))
        replay = self.client.get("/v1/internal/publications/PUB1").get_json()
        self.assertTrue(replay["replay_valid_at_publication"])
        self.assertTrue(replay["evidence_consistent"])

        # 撤回后不能再发布撤回前已批准的新方案
        self.post("/v1/plans", {
            "plan_ref": "PLAN2", "version_ref": "V-ORIG", "purpose": PURPOSE,
            "channel": "social_media", "territory": "CN", "submitted_by": "EDITOR-2",
        })
        self.post("/v1/plans/decide", {
            "approval_ref": "A-PLAN2", "plan_ref": "PLAN2",
            "approver_ref": "REVIEWER-2", "decision": "approved",
            "decided_at": "2026-04-01T00:00:00+08:00",
        }, expected=200)
        resp = self.client.post("/v1/publications", json={
            "publication_ref": "PUB2", "plan_ref": "PLAN2",
            "published_at": "2026-05-02T00:00:00+08:00",
        })
        self.assertEqual(resp.status_code, 422)

    def test_masked_version_unaffected_by_withdrawal(self):
        self.seed()
        # 打码版本（观众已遮蔽）在海外发布
        self.post("/v1/versions", {
            "version_ref": "V-MASK2", "work_ref": "W1", "kind": "mask",
            "parent_version_ref": "V-ORIG",
            "params": {"regions": [{"person_ref": "P-MINOR"}, {"person_ref": "P-VISITOR"}]},
            "digest": "sha256:" + "c" * 64,
        })
        self._approved_plan(plan_ref="PLAN-M", version="V-MASK2",
                            channel="overseas_tour", territory="US")
        self.post("/v1/publications", {
            "publication_ref": "PUB-M", "plan_ref": "PLAN-M",
            "published_at": "2026-03-01T12:00:00+08:00",
        })
        result = self.post("/v1/withdrawals", {
            "withdrawal_ref": "W2", "grant_ref": "G-VISITOR",
            "withdrawn_at": "2026-05-01T00:00:00+08:00",
        })
        # 观众在该版本已遮蔽：海外渠道不受影响，无冻结、无处置项
        self.assertEqual(result["frozen_channels"], [])
        self.assertEqual(result["disposition"], [])
        self.assertTrue(self.verify("V-MASK2", "US", channel="overseas_tour",
                                    at="2026-05-02T00:00:00+08:00"))

    def test_expired_grant_invalidates_use(self):
        # 独立素材：单一临时演员，仅持 4 月到期的短期授权
        self.post("/v1/registry/sessions", {
            "session_ref": "S2", "shot_at": "2026-01-01T10:00:00+08:00"})
        self.post("/v1/registry/works", {
            "work_ref": "W2", "session_ref": "S2", "author_ref": "AUTHOR-1",
            "digest": "sha256:" + "0" * 64, "captured_at": "2026-01-01T10:05:00+08:00"})
        self.post("/v1/registry/persons", {"person_ref": "P-TEMP", "kind": "extra"})
        self.post("/v1/registry/recognitions", {
            "recognition_ref": "R-T", "work_ref": "W2", "person_ref": "P-TEMP",
            "declared_by": "CURATOR-1"})
        self.post("/v1/versions", {
            "version_ref": "V-T", "work_ref": "W2", "kind": "original",
            "digest": "sha256:" + "0" * 64})
        self.post("/v1/registry/grants", {
            "grant_ref": "G-TEMP", "person_ref": "P-TEMP",
            "purposes": [PURPOSE], "territories": ["CN"],
            "valid_from": "2026-01-02T00:00:00+08:00",
            "valid_until": "2026-04-01T00:00:00+08:00",
            "created_at": "2026-01-02T09:00:00+08:00",
        })
        self.assertTrue(self.verify("V-T", "CN", at="2026-03-30T00:00:00+08:00"))
        self.assertFalse(self.verify("V-T", "CN", at="2026-04-02T00:00:00+08:00"))


if __name__ == "__main__":
    unittest.main()
