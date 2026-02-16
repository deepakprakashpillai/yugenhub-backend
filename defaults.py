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

    "finance_categories": [
        # Income Categories
        {
            "id": "sales",
            "name": "Sales",
            "type": "income",
            "subcategories": [
                {"id": "service_revenue", "name": "Service Revenue"},
                {"id": "product_sales", "name": "Product Sales"}
            ]
        },
        {
            "id": "consulting",
            "name": "Consulting",
            "type": "income",
            "subcategories": []
        },
        # Expense Categories
        {
            "id": "operational",
            "name": "Operational",
            "type": "expense",
            "subcategories": [
                {"id": "rent", "name": "Rent"},
                {"id": "utilities", "name": "Utilities"},
                {"id": "internet", "name": "Internet"}
            ]
        },
        {
            "id": "salaries",
            "name": "Salaries & Wages",
            "type": "expense",
            "subcategories": [
                {"id": "full_time", "name": "Full Time Staff"},
                {"id": "contractors", "name": "Contractors"}
            ]
        },
        {
            "id": "marketing",
            "name": "Marketing",
            "type": "expense",
            "subcategories": [
                {"id": "ads", "name": "Ads"},
                {"id": "events", "name": "Events"}
            ]
        },
        {
            "id": "equipment",
            "name": "Equipment",
            "type": "expense",
            "subcategories": [
                {"id": "purchase", "name": "Purchase"},
                {"id": "maintenance", "name": "Maintenance"}
            ]
        },
        {
            "id": "travel",
            "name": "Travel",
            "type": "expense",
            "subcategories": [
                {"id": "flights", "name": "Flights"},
                {"id": "hotels", "name": "Hotels"},
                {"id": "food", "name": "Food"}
            ]
        }
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
