
from typing import Any, List, Optional, Union, Dict
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from bson import ObjectId

class ScopedCollection:
    """
    Wraps a motor collection to enforce agency_id scoping on all queries.
    """
    def __init__(self, collection: AsyncIOMotorCollection, agency_id: str, field_name: str = "agency_id", studio_field_name: str = "studio_id"):
        self._collection = collection
        self.agency_id = agency_id
        self.field_name = field_name
        self.studio_field_name = studio_field_name # Some models use 'studio_id' instead of 'agency_id'

    def _get_scope_filter(self) -> Dict[str, str]:
        """Returns the filter to inject based on the collection name or inspection."""
        # Simple heuristic: Tasks use 'studio_id', others use 'agency_id'
        # Ideally this should be configurable per collection but hardcoding common patterns is safer for now.
        if self._collection.name in ["tasks", "task_history"]:
             return {self.studio_field_name: self.agency_id}
        return {self.field_name: self.agency_id}

    def _merge_filter(self, filter: Optional[Dict] = None) -> Dict:
        """Merges user filter with scope filter."""
        if filter is None:
            filter = {}
        
        scope = self._get_scope_filter()
        
        # If user explicitly tries to override agency_id, we overwrite it back (Guardrail)
        # Or we could raise an error. For now, silent overwrite is safer to ensure isolation.
        merged = {**filter, **scope}
        return merged

    async def find_one(self, filter: Optional[Dict] = None, *args, **kwargs) -> Optional[Dict]:
        return await self._collection.find_one(self._merge_filter(filter), *args, **kwargs)

    def find(self, filter: Optional[Dict] = None, *args, **kwargs):
        # Return a cursor, but we can't easily wrap the cursor itself without a wrapper class.
        # Motor returns a cursor that we iterate. 
        # But the 'find' method just creates the cursor.
        return self._collection.find(self._merge_filter(filter), *args, **kwargs)

    async def count_documents(self, filter: Optional[Dict] = None, *args, **kwargs) -> int:
        return await self._collection.count_documents(self._merge_filter(filter), *args, **kwargs)

    async def insert_one(self, document: Dict, *args, **kwargs):
        # ENFORCE agency_id on insert
        scope = self._get_scope_filter()
        document.update(scope) 
        return await self._collection.insert_one(document, *args, **kwargs)

    async def insert_many(self, documents: List[Dict], *args, **kwargs):
        scope = self._get_scope_filter()
        for doc in documents:
            doc.update(scope)
        return await self._collection.insert_many(documents, *args, **kwargs)

    async def update_one(self, filter: Dict, update: Dict, *args, **kwargs):
        # We enforce scope on filter. 
        # We ALSO need to ensure they don't unset/change the agency_id in the update?
        # For now, scoping the filter is enough to prevent touching others' data.
        return await self._collection.update_one(self._merge_filter(filter), update, *args, **kwargs)

    async def update_many(self, filter: Dict, update: Dict, *args, **kwargs):
        return await self._collection.update_many(self._merge_filter(filter), update, *args, **kwargs)

    async def delete_one(self, filter: Dict, *args, **kwargs):
        return await self._collection.delete_one(self._merge_filter(filter), *args, **kwargs)

    async def delete_many(self, filter: Dict, *args, **kwargs):
        return await self._collection.delete_many(self._merge_filter(filter), *args, **kwargs)
    
    async def find_one_and_update(self, filter: Dict, update: Dict, *args, **kwargs):
        return await self._collection.find_one_and_update(self._merge_filter(filter), update, *args, **kwargs)
        
    def aggregate(self, pipeline: List[Dict], *args, **kwargs):
        # Inject match stage at the START of pipeline
        scope = self._get_scope_filter()
        match_stage = {"$match": scope}
        
        # If first stage is $match, we can merge. Otherwise prepend.
        new_pipeline = [match_stage] + pipeline
        return self._collection.aggregate(new_pipeline, *args, **kwargs)


class ScopedDatabase:
    """
    Wraps the database to return ScopedCollections.
    """
    def __init__(self, db: AsyncIOMotorDatabase, agency_id: str):
        self._db = db
        self.agency_id = agency_id

    def get_collection(self, name: str) -> ScopedCollection:
        return ScopedCollection(self._db.get_collection(name), self.agency_id)

    def __getattr__(self, name: str):
        # Allow accessing collections as attributes: db.users -> ScopedCollection
        # But motor properties (like name) should pass through if needed.
        # Best to stick to get_collection for explicit usage, but this is convenient.
        return self.get_collection(name)
