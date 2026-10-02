from django.core.management.base import BaseCommand, CommandError

from apps.email.keys import issue_service_key


class Command(BaseCommand):
    help = 'Create a service API key. The plaintext key is printed once.'

    def add_arguments(self, parser):
        parser.add_argument('--service', required=True)
        parser.add_argument('--lanes', default='transactional')
        parser.add_argument('--prefixes', default='')
        parser.add_argument('--name', default='')

    def handle(self, *args, **options):
        service = options['service'].strip()
        if not service:
            raise CommandError('service is required')
        prefixes = [item.strip() for item in options['prefixes'].split(',') if item.strip()]
        if not prefixes:
            prefixes = [f'{service}.']
        lanes = [item.strip() for item in options['lanes'].split(',') if item.strip()]
        raw, client = issue_service_key(
            service=service,
            name=options['name'] or service,
            allowed_lanes=lanes,
            allowed_template_prefixes=prefixes,
        )
        self.stdout.write(f'id={client.pk}')
        self.stdout.write(f'prefix={client.key_prefix}')
        self.stdout.write(raw)
