import asyncio
from database import clients_collection, associates_collection
from models.client import ClientModel
from models.associate import AssociateModel
from datetime import datetime, timedelta
import random

async def seed_data():
    print("🌱 Seeding Clients and Associates...")
    agency_id = "default_agency" # Matching the existing agency

    # --- CLIENTS ---
    clients = []
    types = ['Lead', 'Active Client', 'Legacy']
    for i in range(1, 26): # 25 Clients
        c_type = random.choice(types)
        created_date = datetime.now() - timedelta(days=random.randint(0, 60))
        
        clients.append({
            "agency_id": agency_id,
            "name": f"Client {i}",
            "phone": f"9876543{i:03d}",
            "email": f"client{i}@example.com",
            "location": random.choice(["Mumbai", "Delhi", "Udaipur", "Goa"]),
            "type": c_type,
            "total_projects": random.randint(0, 5) if c_type == 'Active Client' else 0,
            "created_at": created_date,
            "updated_at": datetime.now()
        })
    
    if clients:
        # Clear old
        await clients_collection.delete_many({"agency_id": agency_id})
        await clients_collection.insert_many(clients)
        print(f"✅ inserted {len(clients)} clients.")

    # --- ASSOCIATES ---
    associates = []
    roles = ['Photographer', 'Cinematographer', 'Editor', 'Drone Pilot']
    emp_types = ['Freelance', 'In-house']
    
    for i in range(1, 26): # 25 Associates
        role = random.choice(roles)
        emp_type = random.choice(emp_types)
        is_active = random.choice([True, True, True, False]) # Mostly active
        created_date = datetime.now() - timedelta(days=random.randint(0, 60))

        associates.append({
            "agency_id": agency_id,
            "name": f"Associate {i}",
            "phone_number": f"9123456{i:03d}",
            "email_id": f"associate{i}@example.com",
            "base_city": random.choice(["Mumbai", "Bangalore", "Chennai"]),
            "primary_role": role,
            "employment_type": emp_type,
            "is_active": is_active,
            "created_at": created_date
        })

    if associates:
        # Clear old
        await associates_collection.delete_many({"agency_id": agency_id})
        await associates_collection.insert_many(associates)
        print(f"✅ inserted {len(associates)} associates.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(seed_data())
