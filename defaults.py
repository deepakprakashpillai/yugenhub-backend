"""
Default agency configuration values.
Used when a new agency is created or when resetting to defaults.
"""

DEFAULT_AGENCY_CONFIG = {
    "org_name": "My Agency",
    "org_email": "",
    "org_phone": "",

    "status_options": [
        {"id": "enquiry", "label": "Enquiry", "color": "#71717a", "fixed": True},
        {"id": "booked", "label": "Booked", "color": "#3b82f6", "fixed": True},
        {"id": "ongoing", "label": "Ongoing", "color": "#a855f7", "fixed": True},
        {"id": "completed", "label": "Completed", "color": "#22c55e", "fixed": True},
        {"id": "cancelled", "label": "Cancelled", "color": "#ef4444", "fixed": True},
    ],

    "lead_sources": [
        "Instagram", "WhatsApp", "Website", "Referral", "Event", "Other"
    ],

    "deliverable_types": [
        "Cinematic Film", "Teaser", "Traditional Video",
        "Raw Photos", "Edited Photos", "Wedding Album"
    ],

    "verticals": [
        {
            "id": "knots",
            "label": "Knots",
            "description": "Weddings",
            "type": "wedding",
            "fields": []
        },
        {
            "id": "pluto",
            "label": "Pluto",
            "description": "Kids",
            "type": "children",
            "fields": []
        },
        {
            "id": "festia",
            "label": "Festia",
            "description": "Events",
            "type": "general",
            "fields": [
                {"name": "event_scale", "label": "Scale", "type": "select", "options": ["Private", "Corporate", "Mass"]},
                {"name": "company_name", "label": "Company Name", "type": "text", "options": []},
            ]
        },
        {
            "id": "thryv",
            "label": "Thryv",
            "description": "Marketing",
            "type": "general",
            "fields": [
                {"name": "service_type", "label": "Service", "type": "text", "options": []},
            ]
        },
    ],
}
