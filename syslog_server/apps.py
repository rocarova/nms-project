from django.apps import AppConfig
from django.db.backends.signals import connection_created


def enable_sqlite_wal(sender, connection, **kwargs):
    # The syslog listener writes while the web app reads; WAL lets both work without "database is locked"
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')


class SyslogServerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'syslog_server'
    verbose_name = 'Syslog'

    def ready(self):
        connection_created.connect(enable_sqlite_wal)
