import datetime
import io
import shutil
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import tls

NOW = datetime.datetime.now(datetime.timezone.utc)


def make_cert(subject_cn, issuer_cn, public_key, signing_key, ca=False, days=365, start=None, sans=()):
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(start or NOW - datetime.timedelta(days=1))
        .not_valid_after((start or NOW) + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    return builder.sign(signing_key, hashes.SHA256())


class TestCA:
    """A root + intermediate CA, to sign CSRs the way a real certificate authority would."""

    def __init__(self):
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.root = make_cert('Test Root CA', 'Test Root CA', self.root_key.public_key(), self.root_key, ca=True)
        self.int_key = ec.generate_private_key(ec.SECP256R1())
        self.intermediate = make_cert('Test Issuing CA', 'Test Root CA', self.int_key.public_key(), self.root_key, ca=True)

    def sign(self, csr_pem, **kwargs):
        csr = x509.load_pem_x509_csr(csr_pem)
        sans = [str(n.value) for n in csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value]
        return make_cert(tls.common_name(csr.subject), 'Test Issuing CA', csr.public_key(), self.int_key, sans=sans, **kwargs)


def pem(*certs):
    return b''.join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


class TempCertDirMixin:
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        override = override_settings(NMS_CERT_DIR=self.dir)
        override.enable()
        self.addCleanup(override.disable)
        self.ca = TestCA()

    def pending_csr(self):
        return (tls.cert_dir() / tls.PENDING_CSR).read_bytes()


class TlsTests(TempCertDirMixin, SimpleTestCase):
    def test_parse_sans(self):
        names = tls.parse_sans('nms.example.com, 10.0.99.20\n*.corp.example.com nms.example.com')
        self.assertEqual([str(n.value) for n in names], ['nms.example.com', '10.0.99.20', '*.corp.example.com'])
        with self.assertRaises(tls.CertificateError):
            tls.parse_sans('not a/host')

    def test_csr_contains_names_and_key_stays_pending(self):
        tls.create_csr('nms.example.com', 'nms, 10.0.99.20', key_type='ec256', organization='ACME', country='mx')
        info = tls.pending_info()
        self.assertEqual(info['common_name'], 'nms.example.com')
        self.assertEqual(info['sans'], ['nms.example.com', 'nms', '10.0.99.20'])
        self.assertIn('C=MX', info['subject'])
        self.assertEqual(info['key'], 'ECDSA secp256r1')
        self.assertIsNone(tls.certificate_info())  # Nothing installed yet

    def test_install_ca_signed_chain_in_any_order(self):
        tls.create_csr('nms.example.com', key_type='rsa2048')
        leaf = self.ca.sign(self.pending_csr())
        # CAs often send root/intermediate first; the root is dropped, the rest reordered leaf-first
        cert, warnings = tls.install_certificate(pem(self.ca.root, self.ca.intermediate, leaf))
        self.assertEqual(cert, leaf)
        self.assertEqual(warnings, [])
        info = tls.certificate_info()
        self.assertEqual((info['common_name'], info['issuer_cn'], info['chain_length']), ('nms.example.com', 'Test Issuing CA', 2))
        self.assertFalse(info['self_signed'])
        self.assertIsNone(tls.pending_info())  # Pending request consumed
        self.assertEqual(x509.load_pem_x509_certificates((tls.cert_dir() / tls.CHAIN_ONLY).read_bytes()), [self.ca.intermediate])

    def test_install_der_and_p7b(self):
        tls.create_csr('nms.example.com')
        leaf = self.ca.sign(self.pending_csr())
        p7b = pkcs7.serialize_certificates([leaf, self.ca.intermediate], serialization.Encoding.DER)
        tls.install_certificate(p7b)
        self.assertEqual(tls.certificate_info()['chain_length'], 2)
        # Renewal with the same (now active) key, as a single DER certificate
        tls.install_certificate(leaf.public_bytes(serialization.Encoding.DER))
        self.assertTrue(tls.certificate_info()['chain_length'] == 1)

    def test_rejects_mismatched_expired_and_garbage(self):
        tls.create_csr('nms.example.com')
        other_key = ec.generate_private_key(ec.SECP256R1())
        stranger = make_cert('other.example.com', 'Test Issuing CA', other_key.public_key(), self.ca.int_key)
        with self.assertRaisesRegex(tls.CertificateError, "doesn't match"):
            tls.install_certificate(pem(stranger))
        expired = self.ca.sign(self.pending_csr(), start=NOW - datetime.timedelta(days=400), days=30)
        with self.assertRaisesRegex(tls.CertificateError, 'expired'):
            tls.install_certificate(pem(expired))
        with self.assertRaisesRegex(tls.CertificateError, 'Could not read'):
            tls.install_certificate(b'hello')
        self.assertIsNone(tls.certificate_info())  # Nothing was installed by any failed attempt

    def test_import_certificate_with_its_own_key(self):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = make_cert('*.corp.example.com', 'Test Issuing CA', key.public_key(), self.ca.int_key)
        _, warnings = tls.install_certificate(pem(cert), tls.key_pem(key))
        self.assertEqual(tls.certificate_info()['common_name'], '*.corp.example.com')
        self.assertIn('intermediate', warnings[0])

    def test_self_signed_and_rollback_copy(self):
        tls.create_self_signed('10.0.99.20', 'nms.local')
        first = tls.certificate_info()
        self.assertTrue(first['self_signed'])
        self.assertEqual(first['sans'], ['10.0.99.20', 'nms.local'])
        tls.create_self_signed('10.0.99.20')
        self.assertTrue((tls.cert_dir() / 'fullchain.pem.previous').exists())
        self.assertTrue((tls.cert_dir() / 'privkey.pem.previous').exists())

    def test_encrypted_key_is_explained(self):
        key = ec.generate_private_key(ec.SECP256R1())
        encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.BestAvailableEncryption(b'secret'))
        with self.assertRaisesRegex(tls.CertificateError, 'password-protected'):
            tls.load_private_key(encrypted)


class CertificateSettingsViewTests(TempCertDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('admin'))
        self.url = reverse('settings')

    def post(self, **data):
        return self.client.post(self.url, {'section': 'certificate', **data}, follow=True)

    def test_full_csr_flow(self):
        response = self.client.get(self.url, {'tab': 'certificate'})
        self.assertContains(response, 'No certificate installed')

        response = self.post(action='csr', common_name='nms.example.com', sans='10.0.99.20', key_type='rsa2048', country='US')
        self.assertContains(response, 'Certificate request created')
        self.assertContains(response, 'Waiting for your CA')

        download = self.client.get(reverse('download_csr'))
        self.assertIn(b'BEGIN CERTIFICATE REQUEST', download.content)
        self.assertIn('nms.example.com.csr', download['Content-Disposition'])

        signed = pem(self.ca.sign(download.content), self.ca.intermediate)
        response = self.client.post(self.url, {'section': 'certificate', 'action': 'install',
                                               'certificate_file': SimpleUploadedFile('nms.crt', signed)}, follow=True)
        self.assertContains(response, 'Certificate installed for nms.example.com')
        self.assertContains(response, 'Test Issuing CA')
        self.assertNotContains(response, 'Waiting for your CA')
        self.assertEqual(self.client.get(reverse('download_certificate')).status_code, 200)

    def test_validation_errors_shown(self):
        response = self.post(action='csr', common_name='bad host!', key_type='rsa2048', country='USA')
        self.assertIn('common_name', response.context['forms']['csr'].errors)
        self.assertIn('country', response.context['forms']['csr'].errors)
        response = self.post(action='install', certificate_text='garbage')
        self.assertContains(response, 'Could not read the certificate')

    def test_self_signed_and_cancel(self):
        response = self.post(action='selfsigned', common_name='nms.example.com', key_type='ec256')
        self.assertContains(response, 'Self-signed certificate installed')
        self.post(action='csr', common_name='nms.example.com', key_type='ec256')
        response = self.post(action='cancel')
        self.assertContains(response, 'were discarded')
        self.assertIsNone(tls.pending_info())

    def test_private_key_is_never_served(self):
        tls.create_self_signed('nms.example.com')
        body = self.client.get(reverse('download_certificate')).content
        self.assertNotIn(b'PRIVATE KEY', body)
        self.assertEqual(self.client.get(reverse('download_csr')).status_code, 404)

    def test_downloads_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('download_certificate')).status_code, 302)


class CertificateCommandTests(TempCertDirMixin, SimpleTestCase):
    def test_selfsigned_status_csr_install(self):
        out = io.StringIO()
        call_command('nms_certificate', 'selfsigned', '--cn', 'nms.example.com', '--san', '10.0.99.20', stdout=out)
        self.assertIn('Self-signed certificate installed', out.getvalue())
        call_command('nms_certificate', 'csr', '--cn', 'nms.example.com', stdout=io.StringIO())
        cert_file = tls.cert_dir() / 'signed.pem'
        cert_file.write_bytes(pem(self.ca.sign(self.pending_csr()), self.ca.intermediate))
        call_command('nms_certificate', 'install', str(cert_file), stdout=io.StringIO())
        out = io.StringIO()
        call_command('nms_certificate', 'status', stdout=out)
        self.assertIn('Installed: nms.example.com', out.getvalue())
        self.assertIn('Pending request: none', out.getvalue())
