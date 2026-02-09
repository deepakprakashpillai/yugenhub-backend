"""
Migration Script: Legacy Embedded Deliverables → Tasks Collection
=================================================================
This script migrates deliverables that were stored inside project.events[].deliverables
to the unified Tasks collection.

Run with: python migrate_deliverables.py [--dry-run]
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv('MONGO_URI')
DRY_RUN = '--dry-run' in sys.argv

async def migrate():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['yugen_hub']
    projects = db['projects']
    tasks = db['tasks']
    
    migrated = 0
    skipped = 0
    errors = []
    
    print("=" * 60)
    print("Legacy Deliverables Migration Script")
    print("=" * 60)
    if DRY_RUN:
        print("🔍 DRY RUN MODE - No changes will be made\n")
    else:
        print("⚡ LIVE MODE - Changes will be committed\n")
    
    async for project in projects.find():
        project_id = str(project['_id'])
        project_code = project.get('code', 'UNKNOWN')
        agency_id = project.get('agency_id', 'default_agency')
        events = project.get('events', [])
        
        for event in events:
            event_id = event.get('id')
            event_type = event.get('type', 'Unknown Event')
            deliverables = event.get('deliverables', [])
            
            for deliv in deliverables:
                # Map legacy deliverable to Task
                task_doc = {
                    'id': deliv.get('id', str(uuid.uuid4())),
                    'title': deliv.get('type', 'Untitled Deliverable'),
                    'description': deliv.get('notes', ''),
                    'quantity': deliv.get('quantity', 1),
                    'type': 'project',
                    'category': 'deliverable',
                    'project_id': project_id,
                    'event_id': event_id,
                    'studio_id': agency_id,
                    'status': _map_status(deliv.get('status', 'Pending')),
                    'priority': 'medium',
                    'assigned_to': None,
                    'created_by': None,  # Unknown from legacy data
                    'due_date': _parse_date(deliv.get('due_date')),
                    'created_at': datetime.now(),
                    'updated_at': datetime.now(),
                    '_migrated_from': 'legacy_embedded_deliverable',
                    '_migration_date': datetime.now().isoformat()
                }
                
                # Check if already migrated (by matching id)
                existing = await tasks.find_one({'id': task_doc['id']})
                if existing:
                    skipped += 1
                    continue
                
                if DRY_RUN:
                    print(f"  [DRY] Would migrate: {project_code} / {event_type} / {task_doc['title']}")
                else:
                    try:
                        await tasks.insert_one(task_doc)
                        print(f"  ✅ Migrated: {project_code} / {event_type} / {task_doc['title']}")
                    except Exception as e:
                        print(f"  ❌ Error: {project_code} / {event_type} / {task_doc['title']} - {e}")
                        errors.append({'project': project_code, 'event': event_type, 'error': str(e)})
                        continue
                
                migrated += 1
    
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"  Migrated: {migrated}")
    print(f"  Skipped (already exists): {skipped}")
    print(f"  Errors: {len(errors)}")
    
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  - {e['project']} / {e['event']}: {e['error']}")
    
    if DRY_RUN:
        print("\n🔍 This was a dry run. Run without --dry-run to apply changes.")
    else:
        print("\n✅ Migration complete!")

def _map_status(legacy_status: str) -> str:
    """Map legacy status strings to new Task status enum"""
    mapping = {
        'pending': 'todo',
        'in progress': 'in_progress',
        'completed': 'done',
        'delivered': 'done',
    }
    return mapping.get(legacy_status.lower(), 'todo')

def _parse_date(date_str):
    """Parse date string to datetime, return None if invalid"""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except:
        return None

if __name__ == '__main__':
    asyncio.run(migrate())
