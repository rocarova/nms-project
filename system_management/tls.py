"""HTTPS certificate management: private keys, CSRs, self-signed certificates and installing signed certificates.

Files live in settings.NMS_CERT_DIR:
    privkey.pem       active private key      (read by nginx / serve_https; never downloadable)
    fullchain.pem     active certificate + intermediate chain
    chain.pem         the intermediates alone (only when there are any)
    pending-key.pem   private key of an outstanding CSR
    request.csr       the outstanding CSR, to send to the certificate authority
"""
import datetime
import hashlib
import ipaddress
import os
import re
import shutil
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.conf import settings

ACTIVE_KEY = 'privkey.pem'
ACTIVE_CERT = 'fullchain.pem'
PENDING_KEY = 'pending-key.pem'
PENDING_CSR = 'request.csr'
CHAIN_ONLY = 'chain.pem'

KEY_TYPES = [
    ('rsa2048', 'RSA 2048 (most compatible)'),
    ('rsa3072', 'RSA 3072'),
    ('rsa4096', 'RSA 4096'),
    ('ec256', 'ECDSA P-256'),
    ('ec384', 'ECDSA P-384'),
]
SELF_SIGNED_DAYS = 825  # Longest lifetime browsers accept for a leaf certificate
EXPIRY_WARNING_DAYS = 30
HOSTNAME_RE = re.compile(r'^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')


class CertificateError(Exception):
    """A problem with a certificate or key, with a message suitable for showing to the user."""


def cert_dir():
    path = Path(settings.NMS_CERT_DIR)
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)  # Holds private keys: owner only (nginx reads them as root)
    return path


# --- Building blocks -------------------------------------------------------------------------

def generate_key(key_type):
    if key_type.startswith('rsa'):
        return rsa.generate_private_key(public_exponent=65537, key_size=int(key_type[3:]))
    curves = {'ec256': ec.SECP256R1(), 'ec384': ec.SECP384R1()}
    if key_type not in curves:
        raise CertificateError(f'Unknown key type "{key_type}".')
    return ec.generate_private_key(curves[key_type])


def parse_sans(text):
    """Turns "nms.example.com, 10.0.99.20" (commas, spaces or new lines) into SubjectAlternativeName entries."""
    names = []
    for item in re.split(r'[\s,;]+', text or ''):
        if not item:
            continue
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(item)))
        except ValueError:
            if not HOSTNAME_RE.match(item) or len(item) > 253:
                raise CertificateError(f'"{item}" is not a valid hostname or IP address.')
            names.append(x509.DNSName(item.lower()))
    return list(dict.fromkeys(names))  # Drop duplicates, keep order


def build_subject(common_name, organization='', organizational_unit='', locality='', state='', country='', email=''):
    parts = [(NameOID.COMMON_NAME, common_name)]
    for oid, value in ((NameOID.ORGANIZATION_NAME, organization), (NameOID.ORGANIZATIONAL_UNIT_NAME, organizational_unit),
                       (NameOID.LOCALITY_NAME, locality), (NameOID.STATE_OR_PROVINCE_NAME, state),
                       (NameOID.COUNTRY_NAME, country.upper()), (NameOID.EMAIL_ADDRESS, email)):
        if value:
            parts.append((oid, value))
    try:
        return x509.Name([x509.NameAttribute(oid, value) for oid, value in parts])
    except ValueError as exc:
        raise CertificateError(str(exc))


def with_common_name(common_name, sans):
    """Browsers only look at SANs, so the common name must be one of them."""
    cn_names = parse_sans(common_name)
    return list(dict.fromkeys(cn_names + sans))


def key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def public_der(key_or_cert):
    public_key = key_or_cert.public_key()
    return public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def write_file(path, data, private=False):
    """Writes atomically (readers never see a half-written file); private keys are readable by the owner only."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        if private:
            os.chmod(tmp, 0o600)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def install_files(key, chain):
    """Makes key + chain the active certificate. The key is written first, so the chain appearing is the signal
    (a systemd path unit watches fullchain.pem and reloads nginx)."""
    directory = cert_dir()
    for name in (ACTIVE_KEY, ACTIVE_CERT):
        if (directory / name).exists():
            shutil.copyfile(directory / name, directory / f'{name}.previous')  # One-step rollback, kept on disk
            if name == ACTIVE_KEY:
                os.chmod(directory / f'{name}.previous', 0o600)
    write_file(directory / ACTIVE_KEY, key_pem(key), private=True)
    # Intermediates on their own too: serve_https (Twisted) takes the leaf and the chain as separate files
    if len(chain) > 1:
        write_file(directory / CHAIN_ONLY, b''.join(c.public_bytes(serialization.Encoding.PEM) for c in chain[1:]))
    else:
        (directory / CHAIN_ONLY).unlink(missing_ok=True)
    write_file(directory / ACTIVE_CERT, b''.join(c.public_bytes(serialization.Encoding.PEM) for c in chain))


# --- Actions ---------------------------------------------------------------------------------

def create_csr(common_name, sans_text='', key_type='rsa2048', **subject):
    """Generates a new private key and a CSR for it. Replaces any earlier pending request."""
    sans = with_common_name(common_name, parse_sans(sans_text))
    key = generate_key(key_type)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(build_subject(common_name, **subject))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    directory = cert_dir()
    write_file(directory / PENDING_KEY, key_pem(key), private=True)
    write_file(directory / PENDING_CSR, csr.public_bytes(serialization.Encoding.PEM))
    return csr


def create_self_signed(common_name, sans_text='', key_type='rsa2048', days=SELF_SIGNED_DAYS, **subject):
    """Creates and installs a self-signed certificate (browsers will warn; use until the CA-signed one arrives)."""
    sans = with_common_name(common_name, parse_sans(sans_text))
    key = generate_key(key_type)
    name = build_subject(common_name, **subject)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    install_files(key, [cert])
    return cert


def load_certificates(data):
    """Reads a certificate or chain in PEM, DER or PKCS#7 (.p7b/.p7c) form."""
    data = data.strip() if isinstance(data, bytes) else data.strip().encode()
    loaders = [x509.load_pem_x509_certificates, lambda d: [x509.load_der_x509_certificate(d)],
               pkcs7.load_pem_pkcs7_certificates, pkcs7.load_der_pkcs7_certificates]
    for loader in loaders:
        try:
            certs = loader(data)
            if certs:
                return certs
        except (ValueError, TypeError):
            continue
    raise CertificateError('Could not read the certificate. Upload a PEM (.pem/.crt/.cer), DER or PKCS#7 (.p7b) file.')


def load_private_key(data):
    data = data.strip() if isinstance(data, bytes) else data.strip().encode()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except TypeError:
        raise CertificateError('The private key is password-protected. Remove the password first, e.g. '
                               '"openssl pkey -in key.pem -out key-plain.pem".')
    except ValueError:
        raise CertificateError('Could not read the private key. Upload it in PEM format.')


def read_key(name):
    path = cert_dir() / name
    return serialization.load_pem_private_key(path.read_bytes(), password=None) if path.exists() else None


def install_certificate(cert_data, key_data=None):
    """Installs a CA-signed certificate. It must match the pending CSR's key, the active key, or key_data.

    Returns (certificate, warnings). Raises CertificateError when it can't be installed.
    """
    certs = load_certificates(cert_data)
    candidates = []
    if key_data:
        candidates.append(('uploaded', load_private_key(key_data)))
    for source, name in (('pending', PENDING_KEY), ('active', ACTIVE_KEY)):
        key = read_key(name)
        if key is not None:
            candidates.append((source, key))

    # The leaf is whichever certificate matches one of our keys; the rest are the CA chain
    match = next(((source, key, cert) for source, key in candidates for cert in certs
                  if public_der(cert) == public_der(key)), None)
    if match is None:
        raise CertificateError("This certificate doesn't match the pending request or the installed key. "
                               'If it was requested outside NetOps Center, also upload its private key.')
    source, key, leaf = match

    now = datetime.datetime.now(datetime.timezone.utc)
    if leaf.not_valid_after_utc <= now:
        raise CertificateError(f'This certificate expired on {leaf.not_valid_after_utc:%Y-%m-%d}.')
    if leaf.not_valid_before_utc > now + datetime.timedelta(minutes=5):
        raise CertificateError(f'This certificate is not valid until {leaf.not_valid_before_utc:%Y-%m-%d %H:%M} UTC.')

    chain = [leaf] + order_chain(leaf, [c for c in certs if c is not leaf])
    install_files(key, chain)
    if source == 'pending':
        for name in (PENDING_KEY, PENDING_CSR):
            (cert_dir() / name).unlink(missing_ok=True)

    warnings = []
    if len(chain) == 1 and leaf.issuer != leaf.subject:
        warnings.append('No intermediate certificates were included. If browsers report an incomplete chain, '
                        "upload the certificate together with your CA's intermediate certificate(s).")
    return leaf, warnings


def order_chain(leaf, others):
    """Orders intermediates leaf -> root by following issuer names; the root itself is left out (clients have it)."""
    ordered, current = [], leaf
    remaining = list(others)
    while True:
        parent = next((c for c in remaining if c.subject == current.issuer), None)
        if parent is None or parent.subject == parent.issuer:
            return ordered
        ordered.append(parent)
        remaining.remove(parent)
        current = parent


def cancel_pending():
    for name in (PENDING_KEY, PENDING_CSR):
        (cert_dir() / name).unlink(missing_ok=True)


# --- Status ----------------------------------------------------------------------------------

def describe_key(public_key):
    if isinstance(public_key, rsa.RSAPublicKey):
        return f'RSA {public_key.key_size}'
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        return f'ECDSA {public_key.curve.name}'
    return type(public_key).__name__


def san_list(obj):
    try:
        ext = obj.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return [str(name.value) for name in ext]


def common_name(name):
    values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return values[0].value if values else ''


def certificate_info():
    """Details of the active certificate, or None if none is installed."""
    path = cert_dir() / ACTIVE_CERT
    if not path.exists():
        return None
    certs = load_certificates(path.read_bytes())
    leaf = certs[0]
    now = datetime.datetime.now(datetime.timezone.utc)
    days_left = (leaf.not_valid_after_utc - now).days
    return {
        'common_name': common_name(leaf.subject),
        'subject': leaf.subject.rfc4514_string(),
        'issuer': leaf.issuer.rfc4514_string(),
        'issuer_cn': common_name(leaf.issuer) or leaf.issuer.rfc4514_string(),
        'sans': san_list(leaf),
        'not_before': leaf.not_valid_before_utc,
        'not_after': leaf.not_valid_after_utc,
        'days_left': days_left,
        'expired': days_left < 0,
        'expiring_soon': 0 <= days_left < EXPIRY_WARNING_DAYS,
        'self_signed': leaf.issuer == leaf.subject,
        'key': describe_key(leaf.public_key()),
        'serial': format(leaf.serial_number, 'X'),
        'fingerprint': ':'.join(f'{b:02X}' for b in leaf.fingerprint(hashes.SHA256())),
        'chain_length': len(certs),
    }


def pending_info():
    """Details of the outstanding CSR, or None."""
    path = cert_dir() / PENDING_CSR
    if not path.exists() or not (cert_dir() / PENDING_KEY).exists():
        return None
    data = path.read_bytes()
    csr = x509.load_pem_x509_csr(data)
    return {
        'common_name': common_name(csr.subject),
        'subject': csr.subject.rfc4514_string(),
        'sans': san_list(csr),
        'key': describe_key(csr.public_key()),
        'created': datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc),
        'pem': data.decode(),
        'sha256': hashlib.sha256(data).hexdigest()[:16],
    }
