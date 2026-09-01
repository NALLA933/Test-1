from typing import Any

# Replaces duplicate RARITY_MAP dictionaries across 8 files
RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ",
    2: "🔵 ʀᴀʀᴇ",
    3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ",
    6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ",
    8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
    14: "🍭 ᴋᴀᴡᴀɪɪ",
    15: "🧬 ʜʏʙʀɪᴅ",
}

# Replaces SMALL_CAPS_MAP across 8+ files. We use the most robust a-zA-Z mapping.
# (Self-mappings for digits/punctuation are omitted since .get(c, c) handles them natively).
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
}

# Replaces to_small_caps / small_caps functions across 14 files
def to_small_caps(text: Any) -> str:
    """Convert text to small caps using SMALL_CAPS_MAP."""
    if not text:
        return ""
    text = str(text) if text is not None else 'Unknown'
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

# Note: get_rarity_display has diverging implementations in the codebase.
# This is a safe base version, but edge cases should be manually reviewed.
def get_rarity_display(rarity_val: Any) -> str:
    """Replaces get_rarity_display across 5 files."""
    if rarity_val is None:
        return to_small_caps("ɴ/ᴀ")
    
    if isinstance(rarity_val, dict):
        rarity_val = rarity_val.get('rarity', 'Unknown')
        
    if isinstance(rarity_val, int):
        return RARITY_MAP.get(rarity_val, f"⚪ ᴜɴᴋɴᴏᴡɴ ({rarity_val})")
        
    if isinstance(rarity_val, str):
        if rarity_val.isdigit():
            return RARITY_MAP.get(int(rarity_val), to_small_caps(rarity_val))
        return rarity_val
        
    return str(rarity_val)
