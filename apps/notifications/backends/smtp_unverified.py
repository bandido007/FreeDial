"""SMTP backend for an internal relay that presents a self-signed certificate.

**This turns certificate verification off**: the connection is encrypted but
the server's identity is not checked, so it must only be used for a relay on a
network you control. It is selected per mail server by un-ticking "Verify the
server certificate" on the e-mail configuration — never globally.
"""

import ssl

from django.core.mail.backends.smtp import EmailBackend as DjangoSmtpEmailBackend


class EmailBackend(DjangoSmtpEmailBackend):
    def open(self):
        if self.connection:
            return False
        connection_params = {}
        if self.timeout is not None:
            connection_params["timeout"] = self.timeout
        try:
            self.connection = self.connection_class(self.host, self.port, **connection_params)
            if self.use_tls:
                self.connection.ehlo()
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.connection.starttls(context=context)
                self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except OSError:
            if not self.fail_silently:
                raise
            return False
