"""
Pattern: Cron Webhook — Replacing Celery Beat with an external trigger.

Context: An e-commerce marketplace backend (Django REST Framework).
This view replaces a Celery Beat + Celery Worker pair that consumed
1,488+ instance-hours/month on a PaaS with 750 free hours/month.

How it works:
  An external cron service (e.g. Vercel Cron, GitHub Actions schedule,
  cron-job.org) makes one HTTP GET per day to this endpoint.
  The endpoint authenticates the caller via a shared secret in the
  Authorization header, then runs all periodic maintenance tasks
  synchronously in the same web process.

What you give up:
  - Task isolation: a crashing task blocks subsequent tasks in the list.
  - Web traffic isolation: the web worker is occupied for the duration.
  - Retry logic: if the external cron's HTTP request times out (e.g.
    because the backend was cold and took too long to start), the run
    is missed. There is no automatic retry.

See README.md in this directory for the full trade-off discussion.
"""
import logging
import os
import traceback

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class TriggerDailyTasksView(APIView):
    """
    Run all periodic maintenance tasks on demand.
    Protected by CRON_SECRET via Authorization: Bearer header only.
    """
    authentication_classes = []   # no session/JWT auth — uses shared secret
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # ── Step 1: Validate the shared secret ────────────────────────────
        expected = getattr(settings, 'CRON_SECRET', None) or os.environ.get('CRON_SECRET', '')

        if not expected:
            # Fail loudly if the secret is not configured — prevents an
            # open endpoint in a misconfigured deployment.
            return Response({'error': 'Server configuration error'}, status=status.HTTP_401_UNAUTHORIZED)

        # Reject secrets passed as query parameters — they appear in logs.
        if request.GET.get('secret'):
            logger.warning("Cron secret passed as query parameter — rejected")
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        provided = auth_header[len('Bearer '):].strip()
        if provided != expected.strip():
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        # ── Step 2: Run tasks, isolating each one ─────────────────────────
        # Import tasks here to avoid circular imports at module load time.
        try:
            from myapp.tasks import (
                check_batch_cutoffs,
                cleanup_abandoned_carts,
                process_auto_confirmations,
                send_delivery_reminders,
            )
        except ImportError:
            # Fallback stubs for standalone testing / reference demo
            check_batch_cutoffs = lambda: {'status': 'ok', 'processed': 0}
            cleanup_abandoned_carts = lambda: {'status': 'ok', 'cleaned': 0}
            process_auto_confirmations = lambda: {'status': 'ok', 'confirmed': 0}
            send_delivery_reminders = lambda: {'status': 'ok', 'sent': 0}

        results = {}

        def run_safe(key, func):
            """Run a task and record its result. Never raise."""
            try:
                results[key] = func()
            except Exception as exc:
                logger.error(
                    "Cron task %s failed: %s\n%s",
                    key, str(exc), traceback.format_exc(),
                )
                results[key] = {'status': 'error', 'message': str(exc)}

        run_safe('batch_cutoffs', check_batch_cutoffs)
        run_safe('abandoned_carts', cleanup_abandoned_carts)
        run_safe('auto_confirmations', process_auto_confirmations)
        run_safe('delivery_reminders', send_delivery_reminders)

        return Response({'status': 'ok', 'results': results})
