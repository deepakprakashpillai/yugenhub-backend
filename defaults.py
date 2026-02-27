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
    
    "associate_roles": [
        "Photographer", "Cinematographer", "Editor", 
        "Makeup Artist", "DJ", "Decorator", 
        "Coordinator", "Assistant", "Other"
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
            "has_events": True,
            "include_in_finance_summary": True,
            "title_template": "{groom_name} & {bride_name}",
            "card_fields": ["side", "religion"],
            "table_fields": ["side", "religion", "wedding_date"],
            "fields": [
                {"name": "side", "label": "Side", "type": "select", "options": ["Groom", "Bride", "Both"]},
                {"name": "religion", "label": "Religion", "type": "select", "options": ["Hindu", "Christian", "Muslim", "Other"]},
                {"name": "groom_name", "label": "Groom Name", "type": "text", "options": []},
                {"name": "bride_name", "label": "Bride Name", "type": "text", "options": []},
                {"name": "groom_number", "label": "Groom Contact", "type": "tel", "options": []},
                {"name": "bride_number", "label": "Bride Contact", "type": "tel", "options": []},
                {"name": "wedding_date", "label": "Wedding Date", "type": "date", "options": []},
            ],
            "event_fields": []
        },
        {
            "id": "pluto",
            "label": "Pluto",
            "description": "Kids",
            "has_events": True,
            "include_in_finance_summary": True,
            "title_template": "{child_name}'s {occasion_type}",
            "card_fields": ["occasion_type"],
            "table_fields": ["child_name", "occasion_type"],
            "fields": [
                {"name": "child_name", "label": "Child Name", "type": "text", "options": []},
                {"name": "child_age", "label": "Age", "type": "number", "options": []},
                {"name": "occasion_type", "label": "Occasion", "type": "select", "options": ["Birthday", "Baptism", "Newborn", "Other"]},
            ],
            "event_fields": []
        },
        {
            "id": "festia",
            "label": "Festia",
            "description": "Events",
            "has_events": True,
            "include_in_finance_summary": True,
            "title_template": "{event_name}",
            "card_fields": ["event_scale"],
            "table_fields": ["event_scale", "company_name"],
            "fields": [
                {"name": "event_scale", "label": "Scale", "type": "select", "options": ["Private", "Corporate", "Mass"]},
                {"name": "company_name", "label": "Company Name", "type": "text", "options": []},
                {"name": "event_name", "label": "Event Name", "type": "text", "options": []},
            ],
            "event_fields": []
        },
        {
            "id": "thryv",
            "label": "Thryv",
            "description": "Marketing",
            "has_events": False,
            "include_in_finance_summary": True,
            "title_template": "{service_type}",
            "card_fields": ["service_type"],
            "table_fields": ["service_type"],
            "fields": [
                {"name": "service_type", "label": "Service", "type": "text", "options": []},
            ],
            "event_fields": []
        },
    ],
}
