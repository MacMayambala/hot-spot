import logging
from librouteros import connect
from librouteros.exceptions import TrapError
from django.conf import settings

logger = logging.getLogger(__name__)

def get_connection():
    return connect(
        host=settings.MIKROTIK_HOST,
        port=settings.MIKROTIK_PORT,
        username=settings.MIKROTIK_USERNAME,
        password=settings.MIKROTIK_PASSWORD,
        ssl=settings.MIKROTIK_USE_SSL,
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
    # Enable user (if disabled) or set limits via profile
    # We assume user is created with proper profile; activation might just mean enable
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