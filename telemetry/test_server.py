import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import load_snapshot, make_handler


class TelemetryServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.snapshot_path = Path(cls.directory.name) / "metrics.json"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.snapshot_path))
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def setUp(self):
        self.snapshot_path.unlink(missing_ok=True)

    def write_snapshot(self, age=0, **overrides):
        snapshot = {
            "generated_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
            "host": {"name": "compute-01"},
            "vms": [{"vmid": 100}],
        }
        snapshot.update(overrides)
        self.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        return snapshot

    def request(self, path, method="GET"):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            status = response.status
            headers = dict(response.getheaders())
            body = response.read()
        finally:
            connection.close()
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        return status, (json.loads(body) if headers.get('Content-Type', '').startswith('application/json') else body.decode()) if body else None

    def test_fresh_metrics_and_health(self):
        expected = self.write_snapshot()
        before = self.snapshot_path.read_bytes()
        status, body = self.request("/metrics.json?refresh=1")
        self.assertEqual(status, 200)
        self.assertEqual(body["host"], expected["host"])
        self.assertEqual(body["vms"], expected["vms"])
        self.assertFalse(body["stale"])
        self.assertLess(body["age_seconds"], 10)
        self.assertEqual(self.snapshot_path.read_bytes(), before)
        status, body = self.request("/health")
        self.assertEqual((status, body["status"]), (200, "ok"))

    def test_stale_metrics_remain_available_but_health_fails(self):
        self.write_snapshot(age=300)
        status, body = self.request("/metrics.json")
        self.assertEqual(status, 200)
        self.assertTrue(body["stale"])
        self.assertGreaterEqual(body["age_seconds"], 300)
        status, body = self.request("/health")
        self.assertEqual((status, body["status"]), (503, "stale"))

    def test_missing_snapshot(self):
        for endpoint in ("/metrics.json", "/health"):
            with self.subTest(endpoint=endpoint):
                status, body = self.request(endpoint)
                self.assertEqual((status, body["status"]), (503, "unavailable"))

    def test_thermal_history_route_is_available_without_a_current_snapshot(self):
        status, body = self.request('/thermal-history.json')
        self.assertEqual(status, 200)
        self.assertEqual(body['hosts'], [])
        self.assertEqual(body['interval_seconds'], 600)

    def test_malformed_snapshot(self):
        for content in ("broken JSON", "[]", "{}", '{"host":{},"vms":[],"generated_at":null}', "\ufffd"):
            with self.subTest(content=content):
                self.snapshot_path.write_text(content, encoding="utf-8")
                status, body = self.request("/metrics.json")
                self.assertEqual((status, body["status"]), (503, "unavailable"))

    def test_invalid_schema_and_timestamps(self):
        for override in ({"host": []}, {"vms": {}}, {"generated_at": "yesterday"},
                         {"generated_at": "2026-09-08T00:00:00"}, {"host": {"cpu": float("nan")}}):
            with self.subTest(override=override):
                self.write_snapshot(**override)
                status, body = self.request("/metrics.json")
                self.assertEqual((status, body["status"]), (503, "unavailable"))
        self.write_snapshot(age=-120)
        self.assertEqual(self.request("/metrics.json")[0], 503)

    def test_only_exact_allowed_paths_are_served(self):
        self.write_snapshot()
        for path in ("/server.py", "/../metrics.json", "/%2e%2e/metrics.json",
                     "/metrics.json/", "/data/metrics.json", "/%6detrics.json"):
            with self.subTest(path=path):
                status, body = self.request(path)
                self.assertEqual((status, body["error"]), (404, "Not found"))

    def test_only_dashboard_assets_are_served_without_a_snapshot(self):
        for path in ('/', '/app.js', '/styles.css'):
            status, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
        for path in ('/.env', '/config/hosts.json', '/data/history/test.jsonl'):
            self.assertEqual(self.request(path)[0], 404)

    def test_dashboard_config_omits_unknown_fields_and_credential_bearing_links(self):
        from server import load_dashboard
        path = Path(self.directory.name) / 'dashboard-test.json'
        path.write_text(json.dumps({'title': 'Lab', 'private': 'not-published', 'services': [
            {'name': 'Good', 'url': 'https://node.example.invalid/', 'token': 'not-published'},
            {'name': 'Bad', 'url': 'https://' + 'user:password@' + 'node.example.invalid/'},
            {'name': 'Bad', 'url': 'javascript:alert(1)'},
            {'name': 'Bad', 'url': 'https://node.example.invalid/?token=hidden'}]}))
        result = load_dashboard(path)
        self.assertEqual(len(result['services']), 1)
        self.assertNotIn('not-published', json.dumps(result))

    def test_non_get_methods_cannot_change_snapshot(self):
        self.write_snapshot()
        before = self.snapshot_path.read_bytes()
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.request("/metrics.json", method=method)[0], 405)
        self.assertEqual(self.snapshot_path.read_bytes(), before)

    def test_stale_boundary_and_utc_z(self):
        current = datetime.now(timezone.utc)
        self.write_snapshot(generated_at=(current - timedelta(seconds=180)).isoformat().replace("+00:00", "Z"))
        self.assertFalse(load_snapshot(self.snapshot_path, now=current)["stale"])
        self.assertTrue(load_snapshot(self.snapshot_path, now=current + timedelta(seconds=0.1))["stale"])


if __name__ == "__main__":
    unittest.main()
