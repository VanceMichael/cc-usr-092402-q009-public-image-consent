
import unittest
from app.main import create_app


class HealthTest(unittest.TestCase):
    def test_health(self) -> None:
        client = create_app().test_client()
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
