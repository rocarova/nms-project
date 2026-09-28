from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from system_management import tls


class Command(BaseCommand):
    help = ('Manages the HTTPS certificate from the command line (the same as Settings -> Certificate). '
            'Actions: status, selfsigned, csr, install.')

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status', help='Show the installed certificate and any pending request')

        for name, help_text in (('selfsigned', 'Create and install a self-signed certificate'),
                                ('csr', 'Create a private key and a CSR to send to your CA')):
            p = sub.add_parser(name, help=help_text)
            p.add_argument('--cn', required=True, help='Common name: the hostname or IP users browse to')
            p.add_argument('--san', action='append', default=[], help='Additional hostname or IP (repeatable)')
            p.add_argument('--key-type', default='rsa2048', choices=[k for k, _ in tls.KEY_TYPES])
            p.add_argument('--org', default='', help='Organization')
            p.add_argument('--country', default='', help='2-letter country code')
            if name == 'selfsigned':
                p.add_argument('--force', action='store_true', help='Replace an installed CA-signed certificate')

        p = sub.add_parser('install', help='Install the certificate your CA returned')
        p.add_argument('certificate', help='Certificate file (.pem, .crt, .cer, .p7b)')
        p.add_argument('--key', help='Private key file, only if the request was not created by NetOps Center')

    def handle(self, *args, action, **options):
        try:
            getattr(self, action.replace('selfsigned', 'self_signed'))(**options)
        except tls.CertificateError as exc:
            raise CommandError(str(exc))

    def status(self, **options):
        info = tls.certificate_info()
        if info:
            state = ('EXPIRED' if info['expired'] else f"expires in {info['days_left']} days") + \
                    (', self-signed' if info['self_signed'] else '')
            self.stdout.write(f"Installed: {info['common_name']} ({state})")
            self.stdout.write(f"  Names:   {', '.join(info['sans'])}")
            self.stdout.write(f"  Issuer:  {info['issuer_cn']}")
            self.stdout.write(f"  Valid:   {info['not_before']:%Y-%m-%d} -> {info['not_after']:%Y-%m-%d}")
        else:
            self.stdout.write('Installed: none')
        pending = tls.pending_info()
        self.stdout.write(f"Pending request: {pending['common_name']} ({', '.join(pending['sans'])})" if pending
                          else 'Pending request: none')
        self.stdout.write(f'Directory: {tls.cert_dir()}')

    def request_kwargs(self, options):
        return dict(common_name=options['cn'], sans_text=','.join(options['san']), key_type=options['key_type'],
                    organization=options['org'], country=options['country'])

    def self_signed(self, **options):
        info = tls.certificate_info()
        if info and not info['self_signed'] and not info['expired'] and not options['force']:
            raise CommandError(f"A CA-signed certificate for {info['common_name']} is installed; use --force to replace it.")
        cert = tls.create_self_signed(**self.request_kwargs(options))
        self.stdout.write(self.style.SUCCESS(
            f'Self-signed certificate installed for {tls.common_name(cert.subject)} '
            f'(valid until {cert.not_valid_after_utc:%Y-%m-%d}) in {tls.cert_dir()}'))

    def csr(self, **options):
        tls.create_csr(**self.request_kwargs(options))
        path = tls.cert_dir() / tls.PENDING_CSR
        self.stdout.write(path.read_text())
        self.stdout.write(self.style.SUCCESS(f'CSR saved to {path}. Send it to your CA, then run: '
                                             f'manage.py nms_certificate install <certificate-file>'))

    def install(self, **options):
        cert_data = Path(options['certificate']).read_bytes()
        key_data = Path(options['key']).read_bytes() if options['key'] else None
        cert, warnings = tls.install_certificate(cert_data, key_data)
        for warning in warnings:
            self.stderr.write(warning)
        self.stdout.write(self.style.SUCCESS(
            f'Installed certificate for {tls.common_name(cert.subject)} issued by {tls.common_name(cert.issuer)}, '
            f'valid until {cert.not_valid_after_utc:%Y-%m-%d}.'))
