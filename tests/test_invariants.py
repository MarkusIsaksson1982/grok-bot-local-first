"""Exhaustive invariant tests for ops approval and enablement intake screening."""
from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "lib" / "ops" / "core"))
sys.path.insert(0, str(KIT / "lib" / "enablement" / "core"))

import enablement_core as enc  # noqa: E402
import ops_core as ops  # noqa: E402

EU = tuple(enc.EU_EEA)
DATA = tuple(enc.DATA_CLASSES) + ("", "secret")
REGIONS = ("eu", "eea", "se", "eu/eea", "us", "cn", "")
AMOUNTS = (None, 0, 0.0, 0.01, 1, 100)
CONTRACT_BODIES = ("please review this", "please review this nda", "CONTRACT attached")


def _proposal(dc, region, personal, write, mcp, allowlist, external, human, pid="p"):
    p = {
        "id": pid,
        "data_class": dc,
        "vendor_region": region,
        "personal_data": personal,
        "scopes": ["write"] if write else ["read"],
        "human_review": human,
        "external_output": external,
    }
    if mcp:
        p["type"] = "mcp_connector"
        if allowlist:
            p["allowlisted_tools"] = ["search"]
    return p


class TestScreenInvariants(unittest.TestCase):
    def test_exhaustive_screen_flags(self):
        n = 0
        for row in itertools.product(DATA, REGIONS, (False, True), (False, True),
                                     (False, True), (False, True), (False, True), (False, True)):
            dc, region, personal, write, mcp, allowlist, external, human = row
            p = _proposal(*row)
            r = enc.screen(p)
            n += 1
            dc_l = str(dc).lower()
            region_l = str(region).lower()
            with self.subTest(p=p, r=r):
                self.assertIn(r["decision"], ("fast-track", "review", "reject"))
                if dc_l == "restricted" and region_l not in enc.EU_EEA:
                    self.assertEqual(r["decision"], "reject")
                    self.assertEqual(r["priority"], 0)
                    self.assertEqual(r["reviews"], [])
                    self.assertTrue(any("outside the EU/EEA" in x for x in r["reasons"]))
                else:
                    self.assertNotEqual(r["decision"], "reject")
                    if r["decision"] == "fast-track":
                        self.assertEqual(r["reviews"], [])
                    else:
                        self.assertEqual(r["decision"], "review")
                        self.assertTrue(r["reviews"])
                        self.assertGreater(r["priority"], 0)
        self.assertEqual(n, len(DATA) * len(REGIONS) * 64)

    def test_reject_not_overridden_by_other_flags(self):
        # Restricted + US rejects even when every other flag would otherwise be "easy".
        r = enc.screen({"id": "x", "data_class": "restricted", "vendor_region": "US",
                        "scopes": ["read"], "human_review": True, "personal_data": False})
        self.assertEqual(r["decision"], "reject")
        r2 = enc.screen({"id": "x", "data_class": "restricted", "vendor_region": "US",
                         "scopes": ["admin", "delete"], "personal_data": True,
                         "type": "mcp_connector", "external_output": True, "human_review": False})
        self.assertEqual(r2["decision"], "reject")
        self.assertEqual(r2["priority"], 0)

    def test_unknown_class_is_review_not_reject(self):
        r = enc.screen({"id": "u", "data_class": "mystery", "vendor_region": "EU",
                        "scopes": ["read"], "human_review": True})
        self.assertEqual(r["decision"], "review")
        self.assertIn("tech", r["reviews"])


class TestApprovalInvariants(unittest.TestCase):
    def setUp(self):
        self.rules = ops.DEFAULT_RULES

    def test_exhaustive_needs_approval(self):
        n = 0
        for amount, body in itertools.product(AMOUNTS, CONTRACT_BODIES):
            r = {"amount": amount, "subject": "Request", "body": body}
            kinds = ops.approval_kinds(r, self.rules)
            need = ops.needs_approval(r, self.rules)
            n += 1
            expect_money = amount is not None and float(amount) > self.rules["approval"]["amount_over"]
            expect_contract = any(k in f" {body.lower()} " for k in
                                  (" contract ", " nda ", " dpa ", " agreement ", " msa ", " sign ", " terms "))
            # approval_kinds tokenises the same way as the worker (punctuation -> space).
            w = ops._words(f"{r['subject']} {r['body']}")
            expect_contract = any(f" {k} " in w for k in self.rules["approval"]["contract_words"])
            with self.subTest(amount=amount, body=body, kinds=kinds):
                self.assertEqual("money" in kinds, expect_money)
                self.assertEqual("contract" in kinds, expect_contract)
                self.assertEqual(need, bool(kinds))
                if expect_money or expect_contract:
                    self.assertTrue(need)
        self.assertEqual(n, len(AMOUNTS) * len(CONTRACT_BODIES))

    def test_zero_and_missing_amount_are_not_money(self):
        rules = self.rules
        self.assertNotIn("money", ops.approval_kinds({"amount": None, "subject": "x", "body": "y"}, rules))
        self.assertNotIn("money", ops.approval_kinds({"amount": 0, "subject": "x", "body": "y"}, rules))
        self.assertIn("money", ops.approval_kinds({"amount": 0.01, "subject": "x", "body": "y"}, rules))

    def test_amount_over_threshold_is_the_gate(self):
        rules = {"approval": {**self.rules["approval"], "amount_over": 50}}
        r = {"amount": 50, "subject": "x", "body": "y"}
        self.assertFalse(ops.needs_approval(r, rules))
        r2 = {"amount": 50.01, "subject": "x", "body": "y"}
        self.assertEqual(ops.approval_kinds(r2, rules), ["money"])

    def _db(self):
        db = ops.connect(":memory:")
        ops.load_reference(db, [], [
            {"email": "sam@company.example", "name": "Sam", "team": "finance", "manager": None},
            {"email": "lee@company.example", "name": "Lee", "team": "legal", "manager": None},
            {"email": "alex@company.example", "name": "Alex", "team": "sales", "manager": "sam@company.example"},
        ])
        return db

    def _insert(self, db, rid, requester, subject, body, amount, status="pending_approval"):
        db.execute(
            """INSERT INTO requests(id,source,external_id,requester,subject,body,amount,currency,
               vendor_hint,created_at,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "t", rid, requester, subject, body, amount, "SEK", "", ops.now(), rid, status),
        )
        db.commit()

    def test_requester_and_wrong_team_cannot_approve(self):
        db = self._db()
        self._insert(db, "r1", "alex@company.example", "Invoice", "pay this invoice", 100)
        self.assertFalse(ops.decide(db, "r1", "alex@company.example", True)["ok"])
        self.assertFalse(ops.decide(db, "r1", "lee@company.example", True)["ok"])  # legal, money-only
        ok = ops.decide(db, "r1", "sam@company.example", True)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["status"], "approved")

    def test_legal_can_approve_contract_but_not_money(self):
        db = self._db()
        self._insert(db, "c1", "alex@company.example", "NDA", "please review this nda", None)
        self.assertTrue(ops.decide(db, "c1", "lee@company.example", True)["ok"])
        db2 = self._db()
        self._insert(db2, "c2", "alex@company.example", "NDA plus fee", "nda and payment", 10)
        self.assertFalse(ops.decide(db2, "c2", "lee@company.example", True)["ok"])
        self.assertTrue(ops.decide(db2, "c2", "sam@company.example", True)["ok"])  # finance covers both

    def test_classify_apply_recomputes_approval_never_from_model(self):
        db = self._db()
        self._insert(db, "t1", "alex@company.example", "Odd request", "please help", 25, status="enriched")
        db.execute("UPDATE requests SET routed_by='fallback', reason='no rule, no model', status='enriched' WHERE id='t1'")
        db.commit()
        # Route-shaped fallback row for apply_classification:
        db.execute("UPDATE requests SET routed_by='fallback', status='routed' WHERE id='t1'")
        db.commit()
        ans = {"team": "finance", "confidence": 0.9, "reason": "sounds like money"}
        out = ops.apply_classification(db, "t1", ans)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "pending_approval")
        row = db.execute("SELECT status FROM requests WHERE id='t1'").fetchone()
        self.assertEqual(row["status"], "pending_approval")


if __name__ == "__main__":
    unittest.main()
