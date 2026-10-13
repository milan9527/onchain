import threading

from onchain.security import Denied


class SessionBinding:
    """Additional microVM-local invariant; identities can never change within a warm runtime."""
    def __init__(self):
        self.lock = threading.Lock()
        self.identity = None

    def check(self, claims):
        identity = (claims["owner"], claims["sid"], claims["runtime_sid"])
        with self.lock:
            if self.identity is None:
                self.identity = identity
            elif self.identity != identity:
                raise Denied("MICROVM_SESSION_MISMATCH", "This execution environment belongs to another session.")
