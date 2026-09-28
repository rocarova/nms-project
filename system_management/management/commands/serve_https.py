import os

from django.core.management.base import BaseCommand, CommandError

from system_management import tls


def endpoint_path(path):
    # Twisted endpoint strings use ':' as a separator, so Windows paths (C:\...) must be escaped
    return str(path).replace('\\', '\\\\').replace(':', '\\:')


class Command(BaseCommand):
    help = ('Serves NetOps Center over HTTPS directly with Daphne, using the certificate from Settings -> Certificate. '
            'For small or local installs; on a Linux server use nginx (see DEPLOYMENT.md). Restart it after '
            'installing a new certificate.')

    def add_arguments(self, parser):
        parser.add_argument('--host', default='0.0.0.0', help='Address to listen on (default: all interfaces)')
        parser.add_argument('--port', type=int, default=443, help='HTTPS port (default: 443)')

    def handle(self, *args, **options):
        directory = tls.cert_dir()
        key, cert, chain = directory / tls.ACTIVE_KEY, directory / tls.ACTIVE_CERT, directory / tls.CHAIN_ONLY
        if not (key.exists() and cert.exists()):
            raise CommandError('No certificate is installed. Create one in Settings -> Certificate, or run:\n'
                               '  python manage.py nms_certificate selfsigned --cn <hostname-or-ip>')

        info = tls.certificate_info()
        if info['expired']:
            self.stderr.write(self.style.WARNING(f"The installed certificate expired on {info['not_after']:%Y-%m-%d}."))

        endpoint = (f"ssl:port={options['port']}:interface={options['host']}"
                    f':privateKey={endpoint_path(key)}:certKey={endpoint_path(cert)}')
        if chain.exists():
            endpoint += f':extraCertChain={endpoint_path(chain)}'

        # Daphne alone doesn't serve CSS/JS (nginx does that on a server); let the app serve them
        os.environ['NMS_SERVE_STATIC'] = '1'
        self.stdout.write(self.style.SUCCESS(
            f"Serving https://{info['common_name']}:{options['port']}/ "
            f"({'self-signed' if info['self_signed'] else info['issuer_cn']}, valid until {info['not_after']:%Y-%m-%d}). "
            'Ctrl+C to stop.'))

        from daphne.cli import CommandLineInterface
        CommandLineInterface().run(['-e', endpoint, 'nms.asgi:application'])
