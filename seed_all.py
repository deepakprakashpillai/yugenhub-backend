import asyncio
from database import projects_collection, associates_collection, clients_collection
from datetime import datetime, timedelta
import random
import uuid

# ===== ASSOCIATES =====
SAMPLE_ASSOCIATES = [
    {"name": "Rahul Sharma", "role": "Photographer", "city": "Mumbai", "email": "rahul.sharma@yugen.co"},
    {"name": "Priya Patel", "role": "Cinematographer", "city": "Delhi", "email": "priya.patel@yugen.co"},
    {"name": "Amit Kumar", "role": "Editor", "city": "Bangalore", "email": "amit.kumar@yugen.co"},
    {"name": "Sneha Reddy", "role": "Drone Pilot", "city": "Hyderabad", "email": "sneha.reddy@yugen.co"},
    {"name": "Vikram Singh", "role": "Photographer", "city": "Pune", "email": "vikram.singh@yugen.co"},
    {"name": "Anjali Mehta", "role": "Lead", "city": "Mumbai", "email": "anjali.mehta@yugen.co"},
    {"name": "Karthik Rao", "role": "Cinematographer", "city": "Chennai", "email": "karthik.rao@yugen.co"},
    {"name": "Meera Iyer", "role": "Editor", "city": "Bangalore", "email": "meera.iyer@yugen.co"},
    {"name": "Arjun Nair", "role": "Drone Pilot", "city": "Kochi", "email": "arjun.nair@yugen.co"},
    {"name": "Divya Sharma", "role": "Assistant", "city": "Delhi", "email": "divya.sharma@yugen.co"},
]

# ===== CLIENTS & METADATA BY VERTICAL =====
SAMPLE_CLIENTS = {
    "knots": [
        {
            "name": "Raj & Priya Malhotra",
            "phone": "+91 98765 43210",
            "email": "raj.priya@gmail.com",
            "location": "Mumbai",
            "metadata": {
                "side": "Both",
                "groom_name": "Rajesh Malhotra",
                "bride_name": "Priya Sharma",
                "groom_number": "+91 98765 43210",
                "bride_number": "+91 87654 32109",
                "groom_age": 28,
                "bride_age": 26,
                "groom_location": "Malhotra House, Bandra West, Mumbai",
                "bride_location": "Sharma Villa, Juhu, Mumbai",
                "wedding_style": "Traditional",
                "wedding_date": "2024-03-15"
            }
        },
        {
            "name": "Arjun & Meera Iyer",
            "phone": "+91 87654 32109",
            "email": "arjun.meera@gmail.com",
            "location": "Chennai",
            "metadata": {
                "side": "Groom",
                "groom_name": "Arjun Iyer",
                "bride_name": "Meera Subramaniam",
                "groom_number": "+91 87654 32109",
                "bride_number": "+91 76543 21098",
                "groom_age": 30,
                "bride_age": 27,
                "groom_location": "Iyer Residence, T. Nagar, Chennai",
                "bride_location": "Sri Lakshmi Nilayam, Mylapore, Chennai",
                "wedding_style": "South Indian Traditional",
                "wedding_date": "2024-04-22"
            }
        },
        {
            "name": "Karan & Simran Kapoor",
            "phone": "+91 76543 21098",
            "email": "karan.simran@gmail.com",
            "location": "Delhi",
            "metadata": {
                "side": "Bride",
                "groom_name": "Karan Kapoor",
                "bride_name": "Simran Mehra",
                "groom_number": "+91 76543 21098",
                "bride_number": "+91 65432 10987",
                "groom_age": 32,
                "bride_age": 29,
                "groom_location": "Kapoor Mansion, GK-1, Delhi",
                "bride_location": "Mehra House, Vasant Vihar, Delhi",
                "wedding_style": "Modern Fusion",
                "wedding_date": "2024-02-28"
            }
        },
    ],
    "pluto": [
        {
            "name": "Arav Sharma Family",
            "phone": "+91 65432 10987",
            "email": "arav.family@gmail.com",
            "location": "Bangalore",
            "metadata": {
                "child_name": "Arav Sharma",
                "child_age": 1,
                "child_birthday": "2023-02-15",
                "occasion_type": "Birthday",
                "mother_name": "Neha Sharma",
                "father_name": "Rohit Sharma",
                "address": "Prestige Lakeside Habitat, Whitefield, Bangalore",
                "theme": "Jungle Safari"
            }
        },
        {
            "name": "Vihaan Patel Family",
            "phone": "+91 54321 09876",
            "email": "vihaan.family@gmail.com",
            "location": "Pune",
            "metadata": {
                "child_name": "Vihaan Patel",
                "child_age": 0,
                "child_birthday": "2024-01-10",
                "occasion_type": "Newborn",
                "mother_name": "Kavya Patel",
                "father_name": "Harsh Patel",
                "address": "Amanora Park Town, Hadapsar, Pune",
                "theme": "Minimal White"
            }
        },
        {
            "name": "Anaya Reddy Family",
            "phone": "+91 43210 98765",
            "email": "anaya.family@gmail.com",
            "location": "Hyderabad",
            "metadata": {
                "child_name": "Anaya Reddy",
                "child_age": 0,
                "child_birthday": "2024-01-28",
                "occasion_type": "Baptism",
                "mother_name": "Priya Reddy",
                "father_name": "Venkat Reddy",
                "address": "Jubilee Hills, Hyderabad",
                "theme": "White & Gold"
            }
        },
    ],
    "festia": [
        {
            "name": "TechCorp India",
            "phone": "+91 99999 11111",
            "email": "events@techcorp.com",
            "location": "Bangalore",
            "metadata": {
                "event_scale": "Corporate",
                "company_name": "TechCorp India Pvt Ltd",
                "event_name": "Annual Tech Summit 2024",
                "venue": "Bangalore International Exhibition Centre"
            }
        },
        {
            "name": "FashionWeek Mumbai",
            "phone": "+91 88888 22222",
            "email": "contact@fashionweek.in",
            "location": "Mumbai",
            "metadata": {
                "event_scale": "Mass",
                "company_name": "FDCI (Fashion Design Council of India)",
                "event_name": "Spring Collection Launch 2024",
                "venue": "Jio World Convention Centre, BKC"
            }
        },
    ],
    "thryv": [
        {
            "name": "Bean Cafe",
            "phone": "+91 77777 33333",
            "email": "marketing@beancafe.in",
            "location": "Pune",
            "metadata": {
                "company_name": "Bean Cafe",
                "industry": "F&B",
                "campaign_type": "Social Media Campaign",
                "platforms": "Instagram, YouTube"
            }
        },
        {
            "name": "FitGym Studio",
            "phone": "+91 66666 44444",
            "email": "media@fitgym.co",
            "location": "Mumbai",
            "metadata": {
                "company_name": "FitGym Studio",
                "industry": "Fitness",
                "campaign_type": "Promotional Video Series",
                "platforms": "Instagram, YouTube, Website"
            }
        },
    ],
}

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

async def seed_all():
    print("=" * 50)
    print("🧹 CLEARING ALL DATA...")
    print("=" * 50)
    
    await projects_collection.delete_many({})
    await associates_collection.delete_many({})
    await clients_collection.delete_many({})
    print("✅ Cleared projects, associates, and clients.\n")

    # ===== SEED ASSOCIATES =====
    print("👥 Seeding Associates...")
    associates_to_insert = []
    for assoc in SAMPLE_ASSOCIATES:
        phone = f"+91 {random.randint(70000, 99999)} {random.randint(10000, 99999)}"
        associates_to_insert.append({
            "agency_id": "default_agency",
            "name": assoc["name"],
            "phone_number": phone,
            "email_id": assoc["email"],
            "base_city": assoc["city"],
            "primary_role": assoc["role"],
            "employment_type": random.choice(["In-house", "Freelance"]),
            "is_active": random.choice([True, True, True, False]),
            "created_at": datetime.now()
        })
    
    await associates_collection.insert_many(associates_to_insert)
    print(f"✅ Inserted {len(associates_to_insert)} associates.\n")

    # ===== SEED CLIENTS & PROJECTS =====
    print("👤 Seeding Clients & 📁 Projects...")
    
    projects_to_insert = []
    
    for vertical, clients in SAMPLE_CLIENTS.items():
        for client_data in clients:
            # Create Client
            client_doc = {
                "agency_id": "default_agency",
                "name": client_data["name"],
                "phone": client_data["phone"],
                "email": client_data["email"],
                "location": client_data["location"],
                "total_projects": 1,
                "type": random.choice(["Lead", "Active Client", "Legacy"]),
                "created_at": datetime.now() - timedelta(days=random.randint(30, 180)),
                "updated_at": datetime.now()
            }
            
            result = await clients_collection.insert_one(client_doc)
            client_id = str(result.inserted_id)
            print(f"   ✓ Client: {client_data['name']} (ID: {client_id[:8]}...)")
            
            # Create Project for this Client
            code = f"{vertical[:2].upper()}-{random.randint(1000, 9999)}"
            
            # Generate Events
            events = []
            event_types = EVENT_TYPES[vertical]
            num_events = random.randint(2, min(4, len(event_types)))
            chosen_events = random.sample(event_types, num_events)
            
            for evt_idx, evt_type in enumerate(chosen_events):
                event_date = datetime.now() + timedelta(days=random.randint(-5, 30) + (evt_idx * 3))
                
                # Generate Deliverables
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
                        "incharge_id": None,
                        "notes": random.choice(["Rush delivery", "Client wants 4K", "Include B-roll", ""])
                    })
                
                # Generate Assignments
                assignments = []
                num_assignments = random.randint(2, 4)
                chosen_associates = random.sample(SAMPLE_ASSOCIATES, min(num_assignments, len(SAMPLE_ASSOCIATES)))
                
                for assoc in chosen_associates:
                    assignments.append({
                        "id": str(uuid.uuid4()),
                        "associate_id": None,
                        "associate_name": assoc["name"],
                        "role": assoc["role"]
                    })
                
                events.append({
                    "id": str(uuid.uuid4()),
                    "type": evt_type,
                    "venue_name": random.choice(VENUES),
                    "venue_location": client_data["location"],
                    "start_date": event_date,
                    "end_date": event_date + timedelta(hours=random.randint(4, 12)),
                    "deliverables": deliverables,
                    "assignments": assignments,
                    "notes": random.choice(["VIP client", "Outdoor setup", "Extra lighting needed", ""])
                })
            
            events.sort(key=lambda x: x["start_date"])
            
            project_data = {
                "code": code,
                "agency_id": "default_agency",
                "vertical": vertical,
                "client_id": client_id,
                "status": random.choice(["enquiry", "booked", "production", "completed"]),
                "lead_source": random.choice(["Instagram", "Referral", "Website", "Wedding Planner", "Google"]),
                "events": events,
                "metadata": {
                    "client_name": client_data["name"],
                    **client_data.get("metadata", {})
                },
                "created_on": datetime.now() - timedelta(days=random.randint(1, 60)),
                "updated_on": datetime.now()
            }
            
            projects_to_insert.append(project_data)

    await projects_collection.insert_many(projects_to_insert)
    print(f"\n✅ Inserted {len(projects_to_insert)} projects (linked to clients).\n")
    
    print("=" * 50)
    print("🎉 ALL DATA SEEDED SUCCESSFULLY!")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(seed_all())
