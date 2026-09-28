import socket


def server_lan_ip(request):
    """The address network devices should send syslog to: the one the browser reached us on, unless that's localhost."""
    host = request.get_host().rsplit(':', 1)[0]
    if host not in ('127.0.0.1', 'localhost', '[::1]'):
        return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 53))  # UDP connect sends nothing; it only picks the outbound interface
            return sock.getsockname()[0]
    except OSError:
        return host
