import io
import json
import tempfile
import unittest
from pathlib import Path

from test_commerce import PNG_BYTES, load_panel_module


class CustomerApiTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.panel = load_panel_module(Path(self.tempdir.name))
        self.client = self.panel.app.test_client()
        self.panel.state.create_plan(
            self.panel.state.validate_plan_payload(
                {
                    "slug": "basic-30d-100g",
                    "name": "基础套餐",
                    "description": "30 天 100G",
                    "price_fen": "990",
                    "duration_days": "30",
                    "traffic_limit": "100G",
                    "enabled": True,
                    "sort_order": "1",
                }
            )
        )

    def tearDown(self):
        self.tempdir.cleanup()

    # --- helpers -----------------------------------------------------------

    def csrf(self):
        # The portal shell seeds the CSRF token into the session via
        # ensure_csrf_token(); hit it so the token exists before reading it.
        self.client.get("/portal")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def json_headers(self):
        return {"X-CSRF-Token": self.csrf(), "Content-Type": "application/json"}

    def register(self, email="user@example.com", password="Password123!"):
        return self.client.post(
            "/api/customer/auth/register",
            data=json.dumps({"email": email, "password": password, "confirm_password": password}),
            headers=self.json_headers(),
        )

    def fulfilled_subscription(self):
        # Drive the full purchase to a fulfilled subscription via the JSON API
        # for the customer side and the existing admin API for fulfillment.
        self.register()
        create = self.client.post(
            "/api/customer/orders",
            data=json.dumps({"plan_slug": "basic-30d-100g"}),
            headers=self.json_headers(),
        )
        order_no = create.get_json()["data"]["order_no"]
        self.client.post(
            f"/api/customer/orders/{order_no}/payment-proof",
            data={"payer_note": "已付", "proof_image": (io.BytesIO(PNG_BYTES), "p.png")},
            headers={"X-CSRF-Token": self.csrf()},
            content_type="multipart/form-data",
        )
        order = self.panel.state.get_customer_order(1, order_no)
        fulfilled = self.client.post(
            f"/api/orders/{order['id']}/fulfill",
            data=json.dumps({"review_note": "到账"}),
            headers=self.json_headers(),
        )
        self.assertEqual(fulfilled.status_code, 200)
        return self.panel.state.query_customer_service_subscriptions(1)[0]

    # --- tests -------------------------------------------------------------

    def test_me_requires_auth_returns_json_401(self):
        response = self.client.get("/api/customer/me")
        self.assertEqual(response.status_code, 401)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "auth_required")
        self.assertEqual(body["login_url"], "/customer/login")

    def test_register_login_logout_session_cycle(self):
        register = self.register()
        self.assertEqual(register.status_code, 200)
        self.assertEqual(register.get_json()["data"]["customer"]["email"], "user@example.com")

        me = self.client.get("/api/customer/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["data"]["customer"]["email"], "user@example.com")

        self.client.post("/api/customer/auth/logout", headers=self.json_headers())
        self.assertEqual(self.client.get("/api/customer/me").status_code, 401)

        login = self.client.post(
            "/api/customer/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "Password123!"}),
            headers=self.json_headers(),
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get("/api/customer/me").status_code, 200)

    def test_plans_endpoint_is_public(self):
        response = self.client.get("/api/customer/plans")
        self.assertEqual(response.status_code, 200)
        slugs = [p["slug"] for p in response.get_json()["data"]["plans"]]
        self.assertIn("basic-30d-100g", slugs)

    def test_csrf_rejected_on_create_order(self):
        self.register()
        response = self.client.post(
            "/api/customer/orders",
            data=json.dumps({"plan_slug": "basic-30d-100g"}),
            headers={"Content-Type": "application/json"},  # no X-CSRF-Token
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_json()["message"])

    def test_create_order_then_submit_payment_proof(self):
        self.register()
        create = self.client.post(
            "/api/customer/orders",
            data=json.dumps({"plan_slug": "basic-30d-100g"}),
            headers=self.json_headers(),
        )
        self.assertEqual(create.status_code, 200)
        order_no = create.get_json()["data"]["order_no"]
        self.assertEqual(self.panel.state.get_customer_order(1, order_no)["status"], "pending_payment")

        proof = self.client.post(
            f"/api/customer/orders/{order_no}/payment-proof",
            data={"payer_note": "尾号1234", "proof_image": (io.BytesIO(PNG_BYTES), "p.png")},
            headers={"X-CSRF-Token": self.csrf()},
            content_type="multipart/form-data",
        )
        self.assertEqual(proof.status_code, 200)
        self.assertEqual(self.panel.state.get_customer_order(1, order_no)["status"], "payment_submitted")

    def test_subscriptions_and_detail_expose_access_links(self):
        service = self.fulfilled_subscription()

        subs = self.client.get("/api/customer/subscriptions")
        self.assertEqual(subs.status_code, 200)
        items = subs.get_json()["data"]["subscriptions"]
        self.assertEqual(len(items), 1)
        access = items[0]["access"]
        self.assertTrue(access["tenant_subscription_clash_url"])
        self.assertTrue(access["tenant_subscription_v2ray_url"])
        self.assertTrue(access["share_link"])

        detail = self.client.get(f"/api/customer/subscriptions/{service['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["subscription"]["id"], service["id"])

    def test_renew_active_subscription_is_rejected(self):
        service = self.fulfilled_subscription()
        response = self.client.post(
            f"/api/customer/subscriptions/{service['id']}/renew",
            headers=self.json_headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_orders_listing_includes_status_label(self):
        self.register()
        self.client.post(
            "/api/customer/orders",
            data=json.dumps({"plan_slug": "basic-30d-100g"}),
            headers=self.json_headers(),
        )
        response = self.client.get("/api/customer/orders")
        self.assertEqual(response.status_code, 200)
        orders = response.get_json()["data"]["orders"]
        self.assertEqual(len(orders), 1)
        self.assertIn("status", orders[0])
        self.assertIn("status_label", orders[0])


if __name__ == "__main__":
    unittest.main()
