"""Who may sign in, and how a signed-in person is named in logs.

Two small, pure functions so the rules are easy to test without any network.
"""

import hashlib
import hmac
from collections.abc import Collection

USER_HASH_LENGTH = 16


def email_domain(email: str) -> str:
    """Return the part of an email address after the last ``@``, lower-cased.

    Args:
        email: An email address.

    Returns:
        The domain, or an empty string if there is no ``@``.
    """
    _, at, domain = email.strip().lower().rpartition("@")
    return domain if at else ""


def is_allowed(
    email: str,
    hosted_domain: str | None,
    allowed_domains: Collection[str],
    allowed_emails: Collection[str] = (),
) -> bool:
    """Decide whether a Google identity belongs to an allowed college domain.

    Both checks must pass:

    * the email must end in ``@<allowed domain>`` exactly (so
      ``x@evil-goa.bits-pilani.ac.in`` and ``x@goa.bits-pilani.ac.in.evil.com`` fail), and
    * Google's ``hd`` ("hosted domain") claim must name the same domain. Anyone can
      create a personal Google account that *uses* a college address, but only the
      college's own Google Workspace accounts carry the ``hd`` claim.

    Args:
        email: The verified email address from Google.
        hosted_domain: The ``hd`` claim, or ``None`` when Google sent none.
        allowed_domains: Domains that may sign in (lower-case).
        allowed_emails: Individual addresses that may sign in whatever their domain (exact
            match, lower-case). Meant for the developer's own account.

    Returns:
        ``True`` if the person may use the site.
    """
    if email.strip().lower() in {e.lower() for e in allowed_emails}:
        return True
    domain = email_domain(email)
    if not domain or hosted_domain is None:
        return False
    allowed = {d.lower() for d in allowed_domains}
    return domain in allowed and hosted_domain.strip().lower() == domain


def user_hash(email: str, secret: str) -> str:
    """Return a short, stable, non-reversible label for a person.

    Search logs and reports use this instead of the email, so the database holds
    no addresses. It is keyed with the site secret, so nobody can rebuild it
    from a guessed email without the secret.

    Args:
        email: The person's email address.
        secret: The site's session secret.

    Returns:
        A lower-case hex string of :data:`USER_HASH_LENGTH` characters.
    """
    digest = hmac.new(secret.encode(), email.strip().lower().encode(), hashlib.sha256)
    return digest.hexdigest()[:USER_HASH_LENGTH]
