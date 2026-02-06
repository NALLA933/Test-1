import logging
import json
import os
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from bson import ObjectId, Decimal128
import re

# Custom JSON encoder to handle datetime and other types
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, Decimal128):
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        return super().default(obj)

from shivu import (
    application,
    collection,
    user_totals_collection,
    user_collection,
    group_user_totals_collection,
    top_global_groups_collection,
    pm_users,
    user_balance_coll,
    shivuu
)

LOGGER = logging.getLogger(__name__)

# Backup settings
BACKUP_CHAT_ID = -1003702395415
AUTHORIZED_BACKUP_USER = 7818323042

# ---------------- HELPER FUNCTIONS ---------------- #

def serialize_document(obj):
    """
    Recursively serialize MongoDB documents to JSON-compatible format.
    Handles datetime, ObjectId, Decimal128, nested dicts, and lists.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, Decimal128):
        return str(obj)
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    elif isinstance(obj, dict):
        return {key: serialize_document(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [serialize_document(item) for item in obj]
    elif isinstance(obj, tuple):
        return [serialize_document(item) for item in obj]
    elif isinstance(obj, set):
        return [serialize_document(item) for item in obj]
    else:
        return obj

# ---------------- BACKUP FUNCTIONS ---------------- #

async def create_database_backup():
    """
    Creates a complete backup of all database collections.
    Returns a dictionary with all data.
    """
    backup_data = {
        'timestamp': datetime.now().isoformat(),
        'collections': {}
    }
    
    collections_to_backup = {
        'anime_characters': collection,
        'user_totals': user_totals_collection,
        'user_collection': user_collection,
        'group_user_totals': group_user_totals_collection,
        'top_global_groups': top_global_groups_collection,
        'pm_users': pm_users,
        'user_balance': user_balance_coll
    }
    
    for coll_name, coll in collections_to_backup.items():
        try:
            data = await coll.find({}).to_list(length=None)
            # Recursively serialize all documents
            serialized_data = [serialize_document(doc) for doc in data]
            
            backup_data['collections'][coll_name] = serialized_data
            LOGGER.info(f"Backed up {len(data)} documents from {coll_name}")
        except Exception as e:
            LOGGER.error(f"Error backing up {coll_name}: {e}")
            backup_data['collections'][coll_name] = {'error': str(e)}
    
    return backup_data
