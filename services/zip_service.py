import os
import uuid
import zipfile
import tempfile
import httpx
import traceback
from datetime import datetime, timezone
import asyncio

from database import db as raw_db
from utils.r2 import s3_client, R2_BUCKET_NAME, generate_presigned_get_url
from logging_config import get_logger

logger = get_logger("zip_service")

async def process_background_zip(job_id: str, slug: str, tab_id: str, file_ids: list):
    """
    Background worker that streams files from R2 in 1MB chunks, zips them on disk,
    uploads to R2, and sets a presigned download URL.
    Memory footprint is capped at ~1MB + zip overhead.
    """
    try:
        logger.info(f"Starting zip job {job_id} for album {slug}")
        album = await raw_db.albums.find_one({"slug": slug})
        if not album:
            raise ValueError("Album not found")

        target_files = []
        for tab in album.get("tabs", []):
            if tab_id and tab["id"] != tab_id:
                continue
            for f in tab.get("files", []):
                # If file_ids is empty, zip everything in the tab.
                if not file_ids or f["id"] in file_ids:
                    target_files.append(f)
                    
        if not target_files:
            raise ValueError("No matching files found to zip")

        await raw_db.zip_jobs.update_one({"id": job_id}, {"$set": {"progress.total": len(target_files)}})

        zip_filename = f"{slug}-bulk-{uuid.uuid4().hex[:8]}.zip"
        temp_zip_path = os.path.join(tempfile.gettempdir(), zip_filename)
        
        count = 0
        # Stream chunks into zipfile on disk
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for f in target_files:
                    if not f.get("r2_key"): 
                        continue
                        
                    url = generate_presigned_get_url(f["r2_key"])
                    try:
                        async with client.stream('GET', url) as response:
                            if response.status_code == 200:
                                with zf.open(f["file_name"], 'w') as zip_file:
                                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                                        zip_file.write(chunk)
                    except Exception as req_err:
                        logger.error(f"Failed to stream file {f['file_name']} into zip: {req_err}")
                    
                    count += 1
                    # Update progress every 5 files to reduce DB write spam
                    if count % 5 == 0 or count == len(target_files):
                        await raw_db.zip_jobs.update_one({"id": job_id}, {"$set": {"progress.completed": count}})
                    
        logger.info(f"Zip created locally: {temp_zip_path}, uploading to R2")
        
        # Upload assembled zip back to R2
        r2_key = f"zips/{job_id}/{zip_filename}"
        s3_client.upload_file(temp_zip_path, R2_BUCKET_NAME, r2_key, ExtraArgs={"ContentType": "application/zip"})
        
        # Generate final presigned payload
        final_url = generate_presigned_get_url(r2_key, expires_in=259200) # 72 hours
        
        # Cleanup temp disk
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
            
        await raw_db.zip_jobs.update_one({"id": job_id}, {
            "$set": {
                "status": "completed",
                "download_url": final_url,
                "r2_key": r2_key
            }
        })
        logger.info(f"Zip job {job_id} successfully marked as completed.")
        
    except Exception as e:
        logger.error(f"Zip job {job_id} failed: {e}")
        traceback.print_exc()
        await raw_db.zip_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
