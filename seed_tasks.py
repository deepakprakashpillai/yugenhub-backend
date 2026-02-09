import asyncio
from database import tasks_collection, projects_collection, users_collection
from datetime import datetime, timedelta
import random
import uuid

# Sample Data
TASK_TITLES = [
    "Review contract details", "Edit wedding highlights", "Select photos for album", 
    "Coordinate with venue manager", "Brief drone operator", "Design photo book layout", 
    "Upload raw footage", "Update client portal", "Schedule final review meeting", 
    "Confirm equipment list", "Send invoice to client", "Backup project files",
    "Color grade main film", "Select background music", "Create social media teaser",
    "Fix audio syncing issue", "Retouch bridal portraits", "Draft shot list",
    "Scout location for pre-wedding shoot", "Order prints from lab"
]

TASK_DESCRIPTIONS = [
    "Ensure all terms are clearly defined and signed.",
    "Focus on emotional moments and key speeches.",
    "Client requested a mix of candid and posed shots.",
    "Confirm access times and power supply points.",
    "Discuss no-fly zones and required permits.",
    "Use the new minimal template with white borders.",
    "Ensure upload speed is stable before leaving overnight.",
    "Add new deliverables to the client dashboard.",
    "Prepare presentation for final approval.",
    "Check batteries and memory cards for all cameras.",
    "Include breakdown of additional hours.",
    "Verify checksums after transfer.",
    "Match skin tones across different lighting conditions.",
    "Choose track with appropriate tempo and mood.",
    "Create 15-second vertical clip for Instagram.",
    "Sync external recorder audio with camera scratch track.",
    "Remove blemishes and distracting background elements.",
    "List must-have shots for the ceremony.",
    "Check lighting conditions at sunset time.",
    "Verify paper type and finish before ordering."
]

STATUSES = ['todo', 'in_progress', 'review', 'blocked', 'done']
PRIORITIES = ['low', 'medium', 'high', 'urgent']
TYPES = ['internal', 'project']
CATEGORIES = ['general', 'deliverable']

async def seed_tasks():
    print("🌱 Seeding tasks...")
    
    # Fetch existing data
    projects = await projects_collection.find({}, {"_id": 1, "code": 1}).to_list(length=100)
    users = await users_collection.find({}, {"_id": 1, "name": 1}).to_list(length=20)
    
    project_ids = [str(p["_id"]) for p in projects]
    user_ids = [str(u["_id"]) for u in users]
    
    if not project_ids:
        print("⚠️ No projects found. Please seed projects first.")
        return

    tasks_to_insert = []
    
    # Generate 75 tasks
    for i in range(75):
        # Randomly assign type
        task_type = random.choice(TYPES)
        project_id = random.choice(project_ids) if task_type == 'project' else None
        
        # Random dates
        created_at = datetime.now() - timedelta(days=random.randint(0, 30))
        due_date = created_at + timedelta(days=random.randint(1, 14))
        
        task_data = {
            "id": str(uuid.uuid4()),
            "title": random.choice(TASK_TITLES),
            "description": random.choice(TASK_DESCRIPTIONS),
            "type": task_type,
            "category": random.choice(CATEGORIES),
            "project_id": project_id,
            "studio_id": "default_agency",
            "status": random.choice(STATUSES),
            "priority": random.choice(PRIORITIES),
            "assigned_to": random.choice(user_ids) if user_ids and random.random() > 0.3 else None,
            "created_by": random.choice(user_ids) if user_ids else None,
            "due_date": due_date,
            "created_at": created_at,
            "updated_at": datetime.now()
        }
        
        tasks_to_insert.append(task_data)

    if tasks_to_insert:
        # Clear existing tasks (optional, maybe we want to add to existing?)
        # await tasks_collection.delete_many({}) 
        
        await tasks_collection.insert_many(tasks_to_insert)
        print(f"✅ Inserted {len(tasks_to_insert)} tasks!")
    else:
        print("⚠️ No tasks generated.")

if __name__ == "__main__":
    asyncio.run(seed_tasks())
