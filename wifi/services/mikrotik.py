
# wifi/services/mikrotik.py
import logging
import socket
import time
from datetime import datetime
from librouteros import connect
from librouteros.exceptions import TrapError
from django.conf import settings


logger = logging.getLogger(__name__)

# ---------- Existing single‑router functions ----------
def get_connection():
    return connect(
        host=settings.MIKROTIK_HOST,
        port=settings.MIKROTIK_PORT,
        username=settings.MIKROTIK_USERNAME,
        password=settings.MIKROTIK_PASSWORD,
        use_ssl=settings.MIKROTIK_USE_SSL,          # changed ssl→use_ssl
    )

def create_hotspot_user(username, password, profile=None):
    api = get_connection()
    try:
        api.path('/ip/hotspot/user').add(
            name=username,
            password=password,
            profile=profile or 'default'
        )
        logger.info(f"Hotspot user {username} created")
        return True
    except TrapError as e:
        logger.error(f"Failed to create hotspot user: {e}")
        return False

def activate_hotspot_user(username):
    api = get_connection()
    try:
        api.path('/ip/hotspot/user').update(
            where={'name': username},
            disabled=False
        )
        logger.info(f"Hotspot user {username} activated")
        return True
    except TrapError as e:
        logger.error(f"Failed to activate hotspot user: {e}")
        return False

def disable_hotspot_user(username):
    api = get_connection()
    try:
        api.path('/ip/hotspot/user').update(
            where={'name': username},
            disabled=True
        )
        logger.info(f"Hotspot user {username} disabled")
        return True
    except TrapError as e:
        logger.error(f"Failed to disable hotspot user: {e}")
        return False

def disconnect_hotspot_user(username):
    api = get_connection()
    try:
        sessions = api.path('/ip/hotspot/active').select('id').where({'user': username})
        for session in sessions:
            api.path('/ip/hotspot/active').remove(id=session['id'])
        logger.info(f"Hotspot user {username} disconnected")
        return True
    except TrapError as e:
        logger.error(f"Failed to disconnect user: {e}")
        return False

def set_user_expiry(username, expiry_datetime):
    api = get_connection()
    try:
        api.path('/ip/hotspot/user').update(
            where={'name': username},
            expires=expiry_datetime.strftime('%Y-%m-%d %H:%M:%S')
        )
        logger.info(f"Expiry set for {username} to {expiry_datetime}")
        return True
    except TrapError as e:
        logger.error(f"Failed to set expiry: {e}")
        return False

def get_active_sessions():
    api = get_connection()
    try:
        return api.path('/ip/hotspot/active').select('user', 'address', 'uptime')
    except TrapError as e:
        logger.error(f"Failed to get active sessions: {e}")
        return []

# ---------- New Remote Manager for multiple routers ----------
class RemoteMikroTikManager:
    """
    Handles connections and operations for a single remote MikroTik device.
    Uses the device configuration stored in the MikroTikDevice model.
    """
    def __init__(self, device=None, host=None, port=None, user=None, password=None, use_ssl=False):
        if device:
            self.config = {
                'host': device.ip_address,
                'port': device.api_port,
                'user': device.username,
                'password': device.password,   # already decrypted via property
                'use_ssl': device.use_ssl,
            }
        else:
            # Fallback to settings (for backwards compatibility)
            self.config = {
                'host': host or settings.MIKROTIK_HOST,
                'port': port or settings.MIKROTIK_PORT,
                'user': user or settings.MIKROTIK_USERNAME,
                'password': password or settings.MIKROTIK_PASSWORD,
                'use_ssl': use_ssl or settings.MIKROTIK_USE_SSL,
            }
        self.connection = None
        self.api = None
        self.connection_timeout = 15
        self.max_retries = 3
        self.retry_delay = 2
        self._lock = None   # for thread safety, optional
        self.connection_errors = []

    def test_connectivity(self):
        """Test if the router is reachable (TCP port) and API is responsive."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.connection_timeout)
            result = sock.connect_ex((self.config['host'], self.config['port']))
            sock.close()
            if result != 0:
                logger.error(f"Port {self.config['port']} not reachable on {self.config['host']}")
                return False
            # Now try a minimal API command
            api = connect(
                host=self.config['host'],
                port=self.config['port'],
                username=self.config['user'],
                password=self.config['password'],
                use_ssl=self.config.get('use_ssl', False),   # changed
            )
            # Execute a simple read-only command
            api.path('/system/identity').select('name')
            api.close()
            return True
        except Exception as e:
            logger.error(f"Connectivity test failed: {e}")
            return False

    def connect(self):
        """Establish connection with retries."""
        for attempt in range(self.max_retries):
            try:
                self.connection = connect(
                    host=self.config['host'],
                    port=self.config['port'],
                    username=self.config['user'],
                    password=self.config['password'],
                    use_ssl=self.config.get('use_ssl', False),   # changed
                )
                self.api = self.connection
                # Test with a simple command
                self.api.path('/system/resource').select('version').first()
                logger.info(f"Connected to {self.config['host']}")
                self.connection_errors = []  # clear on success
                return True
            except Exception as e:
                error_msg = f"Attempt {attempt+1}: {str(e)}"
                logger.warning(error_msg)
                self.connection_errors.append({'timestamp': datetime.now(), 'error': error_msg})
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
        return False

    def disconnect(self):
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
            self.connection = None
            self.api = None

    def execute(self, path, command=None, **kwargs):
        """
        Execute a command on the router. The path can be a string like '/ip/hotspot/user',
        and optional commands like .add(), .select(), etc.
        """
        if not self.api:
            if not self.connect():
                raise Exception("Could not connect to MikroTik")
        try:
            resource = self.api.path(path)
            if command == 'add':
                return resource.add(**kwargs)
            elif command == 'select':
                return resource.select(*kwargs.get('fields', []))
            elif command == 'update':
                where = kwargs.pop('where', {})
                return resource.update(where=where, **kwargs)
            elif command == 'remove':
                where = kwargs.pop('where', {})
                return resource.remove(where=where)
            else:
                # Default: just get list
                return resource.get()
        except TrapError as e:
            logger.error(f"TrapError on {path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Execution error: {e}")
            raise

    # ---------- Convenience methods ----------
    def get_system_info(self):
        if not self.api and not self.connect():
            return None
        try:
            return self.api.path('/system/resource').get()
        except Exception as e:
            logger.error(f"Could not get system info: {e}")
            return None

    def get_active_users(self):
        if not self.api and not self.connect():
            return []
        try:
            return self.api.path('/ip/hotspot/active').select('user', 'address', 'uptime').get()
        except Exception as e:
            logger.error(f"Could not get active users: {e}")
            return []

    def create_hotspot_user(self, username, password, profile='default', duration_hours=None):
        if not self.api and not self.connect():
            return False
        try:
            params = {
                'name': username,
                'password': password,
                'profile': profile,
            }
            if duration_hours:
                params['limit-uptime'] = f"{duration_hours}h"
            self.api.path('/ip/hotspot/user').add(**params)
            logger.info(f"User {username} created on {self.config['host']}")
            return True
        except TrapError as e:
            logger.error(f"Failed to create user: {e}")
            return False

    def remove_hotspot_user(self, username):
        if not self.api and not self.connect():
            return False
        try:
            self.api.path('/ip/hotspot/user').remove(where={'name': username})
            logger.info(f"User {username} removed")
            return True
        except TrapError as e:
            logger.error(f"Failed to remove user: {e}")
            return False

    def disconnect_user(self, username):
        if not self.api and not self.connect():
            return False
        try:
            sessions = self.api.path('/ip/hotspot/active').select('id').where({'user': username})
            for session in sessions:
                self.api.path('/ip/hotspot/active').remove(id=session['id'])
            logger.info(f"User {username} disconnected")
            return True
        except TrapError as e:
            logger.error(f"Failed to disconnect: {e}")
            return False

    def set_user_expiry(self, username, expiry_datetime):
        if not self.api and not self.connect():
            return False
        try:
            self.api.path('/ip/hotspot/user').update(
                where={'name': username},
                expires=expiry_datetime.strftime('%Y-%m-%d %M:%H:%S')  # adjust if needed
            )
            return True
        except TrapError as e:
            logger.error(f"Failed to set expiry: {e}")
            return False

    def execute_raw_command(self, command_path, params=None):
        if not self.api and not self.connect():
            return None
        try:
            return self.api.path(command_path).get(**params or {})
        except Exception as e:
            logger.error(f"Raw command failed: {e}")
            return None

    def get_full_metrics(self):
        """Collect all metrics: system resource, active users, interface traffic."""
        if not self.api and not self.connect():
            return None
        metrics = {}
        # System resource
        try:
            resource = self.api.path('/system/resource').get()[0]
            metrics['cpu_load'] = float(resource.get('cpu-load', 0))
            metrics['free_memory'] = int(resource.get('free-memory', 0))
            metrics['total_memory'] = int(resource.get('total-memory', 0))
            metrics['uptime'] = resource.get('uptime', '')
        except Exception as e:
            logger.error(f"Resource error: {e}")
            return None
        
        # Active hotspot users
        try:
            active = self.api.path('/ip/hotspot/active').get()
            metrics['active_users'] = len(active)
        except Exception as e:
            logger.error(f"Hotspot active error: {e}")
            metrics['active_users'] = 0
        
        # Interface traffic – get first ethernet interface
        try:
            # Try to get the first ethernet interface
            iface = self.api.path('/interface').select('name', 'rx-byte', 'tx-byte').where({'type': 'ether'}).first()
            if iface:
                metrics['rx_byte'] = int(iface.get('rx-byte', 0))
                metrics['tx_byte'] = int(iface.get('tx-byte', 0))
            else:
                # Fallback to first interface
                iface = self.api.path('/interface').select('name', 'rx-byte', 'tx-byte').first()
                if iface:
                    metrics['rx_byte'] = int(iface.get('rx-byte', 0))
                    metrics['tx_byte'] = int(iface.get('tx-byte', 0))
                else:
                    metrics['rx_byte'] = 0
                    metrics['tx_byte'] = 0
        except Exception as e:
            logger.error(f"Traffic error: {e}")
            metrics['rx_byte'] = 0
            metrics['tx_byte'] = 0
        
        return metrics
