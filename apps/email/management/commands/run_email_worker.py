import signal
import time

from django.core.management.base import BaseCommand

from apps.email.service import deliver_due


class Command(BaseCommand):
    help = 'Claim and send queued messages for one lane. The bulk worker also moves broadcasts forward.'

    def add_arguments(self, parser):
        parser.add_argument('--lane', required=True, choices=['auth', 'transactional', 'bulk'])
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--poll-seconds', type=float, default=1.0)
        parser.add_argument('--batch-size', type=int, default=20)

    def handle(self, *args, **options):
        from config.telemetry import configure_telemetry

        configure_telemetry()
        from apps.email.worker_heartbeat import HeartbeatThrottle

        previous = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }

        def _stop(signum, _frame):
            from shellui_django.telemetry import shutdown_telemetry

            shutdown_telemetry()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        lane = options['lane']
        heartbeat = HeartbeatThrottle(lane)
        try:
            self._run(lane, heartbeat, options)
        finally:
            signal.signal(signal.SIGTERM, previous[signal.SIGTERM])
            signal.signal(signal.SIGINT, previous[signal.SIGINT])

    def _run(self, lane, heartbeat, options):
        while True:
            heartbeat.beat()
            broadcasts = 0
            if lane == 'bulk':
                from apps.email.broadcasts import process_broadcasts

                broadcasts = process_broadcasts()
            count = deliver_due(lane, limit=options['batch_size'])
            if options['once']:
                self.stdout.write(f'processed={count} broadcasts={broadcasts}' if lane == 'bulk' else f'processed={count}')
                return
            if count == 0:
                time.sleep(options['poll_seconds'])
