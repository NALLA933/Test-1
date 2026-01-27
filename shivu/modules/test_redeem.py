"""
Debug script to test character redemption
Run this to see what's happening with the database
"""

import asyncio
from shivu import user_collection, collection, db

async def test_character_addition():
    """Test if we can add a character to user collection"""
    
    print("=== TESTING CHARACTER ADDITION ===\n")
    
    # Test user ID
    test_user_id = 123456789
    test_character_id = 1  # Replace with actual character ID from your database
    
    # Step 1: Check if character exists in main collection
    print(f"Step 1: Checking if character {test_character_id} exists...")
    character = await collection.find_one({"id": test_character_id})
    if not character:
        character = await collection.find_one({"id": str(test_character_id)})
    
    if not character:
        print(f"❌ Character {test_character_id} NOT FOUND in anime_characters_lol collection!")
        print("Try with a different character ID that exists in your database.")
        return
    
    print(f"✅ Character found: {character.get('name')} from {character.get('anime')}")
    print(f"   Full character data: {character}\n")
    
    # Step 2: Check current user collection state
    print(f"Step 2: Checking user {test_user_id} current state...")
    user_before = await user_collection.find_one({"id": test_user_id})
    if user_before:
        char_count_before = len(user_before.get("characters", []))
        print(f"✅ User exists with {char_count_before} characters")
        print(f"   User data: {user_before}\n")
    else:
        print(f"ℹ️  User doesn't exist yet (will be created)\n")
    
    # Step 3: Prepare character entry
    print("Step 3: Preparing character entry...")
    character_entry = {
        "id": character.get("id"),
        "name": character.get("name"),
        "anime": character.get("anime"),
        "rarity": character.get("rarity"),
        "img_url": character.get("img_url")
    }
    print(f"   Character entry: {character_entry}\n")
    
    # Step 4: Try to add character
    print("Step 4: Adding character to user collection...")
    try:
        result = await user_collection.update_one(
            {"id": test_user_id},
            {
                "$push": {"characters": character_entry},
                "$setOnInsert": {
                    "id": test_user_id,
                    "balance": 0,
                    "favorites": []
                }
            },
            upsert=True
        )
        print(f"✅ Update operation completed!")
        print(f"   Matched: {result.matched_count}")
        print(f"   Modified: {result.modified_count}")
        print(f"   Upserted ID: {result.upserted_id}\n")
    except Exception as e:
        print(f"❌ ERROR during update: {e}\n")
        return
    
    # Step 5: Verify the character was added
    print("Step 5: Verifying character was added...")
    user_after = await user_collection.find_one({"id": test_user_id})
    if user_after:
        char_count_after = len(user_after.get("characters", []))
        print(f"✅ User now has {char_count_after} characters")
        print(f"   Latest characters: {user_after.get('characters', [])[-3:]}")  # Last 3 characters
    else:
        print(f"❌ ERROR: User not found after update!")
    
    print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(test_character_addition())
