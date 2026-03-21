import re
import secrets
from database import db


def generate_slug(title: str) -> str:
    """Convert title to URL-friendly slug."""
    slug = title.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    slug = slug.strip('-')
    return slug


async def ensure_unique_slug(slug: str, exclude_album_id: str = None) -> str:
    """Check slug uniqueness against albums collection. Append random suffix on collision."""
    query = {"slug": slug}
    if exclude_album_id:
        query["id"] = {"$ne": exclude_album_id}

    existing = await db.albums.find_one(query)
    if existing:
        suffix = secrets.token_hex(2)
        slug = f"{slug}-{suffix}"
    return slug
