import asyncio
from database import associates_collection
from datetime import datetime
import random

# Sample Associates - these match the names used in seed_projects.py
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

async def seed_associates():
    print("🗑️  Clearing existing associates...")
    await associates_collection.delete_many({})
    print("✅ Cleared all associates.")

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
            "is_active": random.choice([True, True, True, False]),  # 75% active
            "created_at": datetime.now()
        })

    if associates_to_insert:
        await associates_collection.insert_many(associates_to_insert)
        print(f"✅ Inserted {len(associates_to_insert)} associates!")
    else:
        print("⚠️ No associates to insert.")

if __name__ == "__main__":
    asyncio.run(seed_associates())
