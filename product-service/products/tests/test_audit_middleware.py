import threading
import time
from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from shared_auth import audit_middleware as am


def _wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class AuditMiddlewareTest(SimpleTestCase):
    """Audit events go out from a small pool over keep-alive sessions, never piling up unbounded."""

    def setUp(self):
        # A fresh pool: its threads start without sessions, so the test sees every one created
        if am._pool is not None:
            am._pool.shutdown(wait=True)
            am._pool = None
        self.middleware = am.AuditMiddleware(lambda request: HttpResponse(status=201))
        self.request = RequestFactory().post(
            "/api/orders/", HTTP_X_REQUEST_ID="r1", HTTP_X_GATEWAY_USER_ID="u1",
        )

    def test_event_is_posted_with_request_metadata(self):
        with mock.patch.object(am.requests.Session, "post") as post:
            self.assertEqual(self.middleware(self.request).status_code, 201)
            self.assertTrue(_wait(lambda: post.called))
        payload = post.call_args.kwargs["json"]
        got = [payload[k] for k in ("request_id", "method", "path", "user_id", "status_code")]
        self.assertEqual(got, ["r1", "POST", "/api/orders/", "u1", 201])

    def test_sessions_are_reused_across_requests(self):
        created = []
        real_init = am.requests.Session.__init__

        def counting_init(session, *a, **kw):
            created.append(threading.get_ident())
            real_init(session, *a, **kw)

        with mock.patch.object(am.requests.Session, "post") as post, \
                mock.patch.object(am.requests.Session, "__init__", counting_init):
            for _ in range(20):
                self.middleware(self.request)
            self.assertTrue(_wait(lambda: post.call_count == 20))
        self.assertLessEqual(len(created), am.POOL_SIZE)

    def test_full_backlog_drops_the_event_but_serves_the_request(self):
        with mock.patch.object(am, "_pending", threading.BoundedSemaphore(1)) as pending, \
                mock.patch.object(am.requests.Session, "post") as post:
            pending.acquire()  # backlog already full
            self.assertEqual(self.middleware(self.request).status_code, 201)
            time.sleep(0.1)
        post.assert_not_called()
