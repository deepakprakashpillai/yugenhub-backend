import asyncio
from database import projects_collection, associates_collection, clients_collection
from datetime import datetime, timedelta
import random
import uuid

# Sample Associate IDs (we'll create some fake ones)
SAMPLE_ASSOCIATES = [
    {"name": "Rahul Sharma", "role": "Photographer"},
    {"name": "Priya Patel", "role": "Cinematographer"},
    {"name": "Amit Kumar", "role": "Editor"},
    {"name": "Sneha Reddy", "role": "Drone Pilot"},
    {"name": "Vikram Singh", "role": "Photographer"},
    {"name": "Anjali Mehta", "role": "Makeup Artist"},
]

# Deliverable Types by Vertical
DELIVERABLE_TYPES = {
    "knots": ["Photo Album", "Highlight Reel", "Full Wedding Film", "Drone Footage", "Same Day Edit", "Raw Photos"],
    "pluto": ["Photo Album", "Birthday Video", "Photo Prints", "Digital Gallery", "Thank You Cards"],
    "festia": ["Event Recap Video", "Live Stream", "Photo Gallery", "Social Media Clips", "Press Kit"],
    "thryv": ["Brand Video", "Social Media Pack", "Product Photos", "Reels/Shorts", "BTS Footage"],
}

# Event Types by Vertical
EVENT_TYPES = {
    "knots": ["Mehendi", "Sangeet", "Haldi", "Wedding", "Reception"],
    "pluto": ["Consultation", "Photo Shoot", "Video Shoot", "Editing Review", "Delivery"],
    "festia": ["Briefing", "Setup", "Main Event", "After Party", "Wrap Up"],
    "thryv": ["Discovery Call", "Pre-Production", "Shoot Day", "Post-Production", "Delivery"],
}

STATUSES = ["Pending", "In Progress", "Completed", "Delivered"]
VENUES = ["Grand Palace", "Taj Gardens", "Marriott Ballroom", "Beach Resort", "City Club", "Home Studio", "Client Office"]
LOCATIONS = ["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad", "Pune", "Goa"]

async def seed_projects():
    print("🗑️  Clearing existing projects...")
    await projects_collection.delete_many({})
    print("✅ Cleared all projects.")

    projects_to_insert = []
    
    # Sample Verticals with clients
    sample_verticals = {
        "knots": [
            {"client": "Raj & Priya", "desc": "Luxury destination wedding"},
            {"client": "Arjun & Meera", "desc": "Traditional South Indian wedding"},
            {"client": "Karan & Simran", "desc": "Intimate backyard wedding"},
        ],
        "pluto": [
            {"client": "Baby Arav", "desc": "1st Birthday Celebration"},
            {"client": "Baby Vihaan", "desc": "Newborn Photo Session"},
        ],
        "festia": [
            {"client": "TechCorp", "desc": "Annual Tech Summit 2024"},
            {"client": "FashionWeek", "desc": "Spring Fashion Show"},
        ],
        "thryv": [
            {"client": "Bean Cafe", "desc": "Social Media Campaign"},
            {"client": "FitGym", "desc": "Promotional Video Series"},
        ],
    }

    for vertical, items in sample_verticals.items():
        for item in items:
            code = f"{vertical[:2].upper()}-{random.randint(1000, 9999)}"
            
            # Generate Events
            events = []
            event_types = EVENT_TYPES[vertical]
            num_events = random.randint(2, min(4, len(event_types)))
            chosen_events = random.sample(event_types, num_events)
            
            for evt_idx, evt_type in enumerate(chosen_events):
                event_date = datetime.now() + timedelta(days=random.randint(-5, 30) + (evt_idx * 3))
                
                # Generate Deliverables for this event
                deliverables = []
                deliverable_types = DELIVERABLE_TYPES[vertical]
                num_deliverables = random.randint(2, 4)
                chosen_deliverables = random.sample(deliverable_types, min(num_deliverables, len(deliverable_types)))
                
                for del_type in chosen_deliverables:
                    deliverables.append({
                        "id": str(uuid.uuid4()),
                        "type": del_type,
                        "quantity": random.randint(1, 5),
                        "status": random.choice(STATUSES),
                        "due_date": event_date + timedelta(days=random.randint(3, 14)),
                        "incharge_id": str(uuid.uuid4())[:24],  # Fake ObjectId-like
                        "notes": random.choice(["Rush delivery", "Client wants 4K", "Include B-roll", ""])
                    })
                
                # Generate Assignments for this event
                assignments = []
                num_assignments = random.randint(2, 4)
                chosen_associates = random.sample(SAMPLE_ASSOCIATES, min(num_assignments, len(SAMPLE_ASSOCIATES)))
                
                for assoc in chosen_associates:
                    assignments.append({
                        "id": str(uuid.uuid4()),
                        "associate_id": str(uuid.uuid4())[:24],  # Fake ObjectId-like
                        "associate_name": assoc["name"],  # Store name for display
                        "role": assoc["role"]
                    })
                
                events.append({
                    "id": str(uuid.uuid4()),
                    "type": evt_type,
                    "venue_name": random.choice(VENUES),
                    "venue_location": random.choice(LOCATIONS),
                    "start_date": event_date,
                    "end_date": event_date + timedelta(hours=random.randint(4, 12)),
                    "deliverables": deliverables,
                    "assignments": assignments,
                    "notes": random.choice(["VIP client - extra attention", "Outdoor setup", "Need extra lighting", ""])
                })
            
            # Sort events by date
            events.sort(key=lambda x: x["start_date"])
            
            project_data = {
                "code": code,
                "agency_id": "default_agency",
                "vertical": vertical,
                "client_id": str(uuid.uuid4())[:24],
                "status": random.choice(["enquiry", "booked", "production", "completed"]),
                "lead_source": random.choice(["Instagram", "Referral", "Website", "Wedding Planner", "Google"]),
                "events": events,
                "metadata": {
                    "client_name": item["client"],
                    "description": item["desc"],
                    "budget": f"₹{random.randint(50, 500) * 1000:,}",
                    "priority": random.choice(["High", "Medium", "Low"]),
                    "notes": "Sample project for demo purposes."
                },
                "created_on": datetime.now() - timedelta(days=random.randint(1, 60)),
                "updated_on": datetime.now()
            }
            
            # Add vertical-specific metadata
            if vertical == "knots":
                project_data["metadata"]["wedding_date"] = (datetime.now() + timedelta(days=random.randint(10, 90))).strftime("%Y-%m-%d")
                project_data["metadata"]["guest_count"] = random.randint(100, 500)
                project_data["metadata"]["wedding_style"] = random.choice(["Traditional", "Modern", "Fusion", "Destination"])
            elif vertical == "pluto":
                project_data["metadata"]["child_age"] = random.choice(["Newborn", "6 months", "1 year", "2 years"])
                project_data["metadata"]["theme"] = random.choice(["Jungle Safari", "Princess", "Superheroes", "Minimal"])
            elif vertical == "festia":
                project_data["metadata"]["attendees"] = random.randint(50, 2000)
                project_data["metadata"]["event_type"] = random.choice(["Corporate", "Social", "Charity", "Launch"])
            elif vertical == "thryv":
                project_data["metadata"]["industry"] = random.choice(["F&B", "Fitness", "Tech", "Fashion", "Healthcare"])
                project_data["metadata"]["platforms"] = random.choice(["Instagram, YouTube", "LinkedIn, Website", "All Social"])
            
            projects_to_insert.append(project_data)

    if projects_to_insert:
        await projects_collection.insert_many(projects_to_insert)
        print(f"✅ Inserted {len(projects_to_insert)} projects with deliverables and assignments!")
    else:
        print("⚠️ No projects to insert.")

if __name__ == "__main__":
    asyncio.run(seed_projects())
