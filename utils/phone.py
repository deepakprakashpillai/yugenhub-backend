def resolve_whatsapp_number(client: dict) -> str | None:
    """Return the best WhatsApp number for a client — dedicated field first, phone fallback."""
    return client.get("whatsapp_number") or client.get("phone") or None
