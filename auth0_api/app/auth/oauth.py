"""
OAuth2 and Auth0 configuration and management.
"""

from authlib.integrations.starlette_client import OAuth
from app.config.settings import settings
from app.config.logging import get_logger

logger = get_logger(__name__)


def setup_oauth() -> OAuth:
    """
    Initialize and configure OAuth client with Auth0.

    Returns:
        Configured OAuth instance
    """
    oauth = OAuth()

    # Register Auth0
    auth0 = oauth.register(
        'auth0',
        client_id=settings.AUTH0_CLIENT_ID,
        client_secret=settings.AUTH0_CLIENT_SECRET,
        server_metadata_url=f'https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration',
        client_kwargs={
            'scope': settings.AUTH0_SCOPE,
        },
    )

    logger.debug("OAuth client initialized for Auth0 domain: %s", settings.AUTH0_DOMAIN)
    return oauth


def get_oauth_instance() -> OAuth:
    """
    Get a singleton OAuth instance.

    Returns:
        OAuth instance
    """
    if not hasattr(get_oauth_instance, '_instance'):
        get_oauth_instance._instance = setup_oauth()
    return get_oauth_instance._instance
