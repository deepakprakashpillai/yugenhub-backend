"""
Debug script to check Priya Patel's user role and task assignments.
"""
import asyncio
from database import users_collection, tasks_collection

async def debug_priya_tasks():
    print("🔍 Debugging Priya Patel's Task Access...")
    
    # 1. Find Priya's User record
    priya = await users_collection.find_one({"email": "priya.patel@yugen.co"})
    
    if priya:
        print(f"✅ User Found: {priya.get('name')}")
        print(f"   ID: {priya.get('id')}")
        print(f"   Role: {priya.get('role')}")
        print(f"   Email: {priya.get('email')}")
        
        user_id = priya.get('id')
        role = priya.get('role')
        
        if role != 'member':
            print(f"\n⚠️ ISSUE: Priya's role is '{role}', not 'member'. RBAC won't restrict her!")
        
        # 2. Check tasks assigned to her
        tasks = await tasks_collection.find({"assigned_to": user_id}).to_list(100)
        print(f"\n📋 Tasks assigned to Priya (ID={user_id}): {len(tasks)}")
        
        # 3. Check total tasks in system
        total_tasks = await tasks_collection.count_documents({})
        print(f"📋 Total tasks in system: {total_tasks}")
        
    else:
        print("❌ User 'priya.patel@yugen.co' NOT FOUND!")

if __name__ == "__main__":
    asyncio.run(debug_priya_tasks())
