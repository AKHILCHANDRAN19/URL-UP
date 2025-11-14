import os
import re
import math
import time
import asyncio
import aiohttp
import json
import logging
import subprocess
import threading
from typing import Optional, Dict, Any
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified, FloodWait

# ============================================
# CONFIGURATION - RENDER COMPATIBLE
# ============================================
API_ID = 2819362
API_HASH = "578ce3d09fadd539544a327c45b55ee4"
BOT_TOKEN = "8390475015:AAF8dauJYTWFwktTQABzG17_-JTN4r71R3M"

# Bot settings
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
DOWNLOAD_DIR = "./downloads"
JSON_DB_PATH = "./bot_data.json"  # JSON storage instead of database
YTDL_PATH = "./yt-dlp"  # Path to yt-dlp binary

# DEFAULT THUMBNAIL (from your provided photo)
DEFAULT_THUMB_ID = "AgACAgUAAxkBAAE9vJdpFKHL4lIezMqiAhL4U86UBU9HFAACcg5rGxoHoVRR8Xe3Z3RrUwEAAwIAA20AAzYE"

# Create directories
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================
# JSON STORAGE MANAGER (NO DATABASE)
# ============================================
class JsonStorage:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = self.load()
    
    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"thumbnails": {}, "settings": {}, "stats": {"total_downloads": 0}}
    
    def save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_thumbnail(self, user_id: int) -> Optional[str]:
        return self.data["thumbnails"].get(str(user_id))
    
    def set_thumbnail(self, user_id: int, file_id: str):
        self.data["thumbnails"][str(user_id)] = file_id
        self.save()
    
    def delete_thumbnail(self, user_id: int):
        if str(user_id) in self.data["thumbnails"]:
            del self.data["thumbnails"][str(user_id)]
            self.save()
    
    def get_setting(self, user_id: int, key: str, default: Any = False) -> Any:
        user_settings = self.data["settings"].get(str(user_id), {})
        return user_settings.get(key, default)
    
    def set_setting(self, user_id: int, key: str, value: Any):
        if str(user_id) not in self.data["settings"]:
            self.data["settings"][str(user_id)] = {}
        self.data["settings"][str(user_id)][key] = value
        self.save()
    
    def increment_stats(self):
        self.data["stats"]["total_downloads"] += 1
        self.save()
    
    def get_stats(self) -> Dict[str, Any]:
        return self.data["stats"]

json_db = JsonStorage(JSON_DB_PATH)

# ============================================
# FLASK WEB SERVER (FOR RENDER)
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return {
        "status": "running",
        "bot": "URL Uploader Bot",
        "timestamp": time.time()
    }

@app.route('/health')
def health():
    return {"status": "healthy"}, 200

def run_web_server():
    """Run Flask server in background thread"""
    app.run(host='0.0.0.0', port=80, debug=False, use_reloader=False)

# ============================================
# BOT INITIALIZATION
# ============================================
bot = Client(
    "url_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    max_concurrent_transmissions=3,
    parse_mode=enums.ParseMode.HTML
)

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================
# HELPER FUNCTIONS
# ============================================
def sizeof_fmt(num: float, suffix: str = "B") -> str:
    """Format file size in human readable format"""
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"

def is_valid_url(url: str) -> bool:
    """Check if URL is valid"""
    regex = re.compile(
        r'^https?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(regex.match(url))

def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video"""
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})', re.IGNORECASE)
    return bool(youtube_regex.match(url))

def get_filename_from_url(url: str, content_disposition: Optional[str] = None, custom_name: Optional[str] = None) -> str:
    """Extract filename from URL, headers, or custom name"""
    if custom_name:
        return custom_name
    
    if content_disposition:
        fname = re.findall('filename="(.+)"', content_disposition)
        if fname:
            return fname[0]
    
    filename = os.path.basename(url.split('?')[0]) or "file"
    filename = re.sub(r'[^\w\-_\. ]', '_', filename)
    if '.' not in filename:
        filename += '.mp4'
    return filename[:100]

# ============================================
# PROGRESS CALLBACKS
# ============================================
async def progress_callback(current: int, total: int, message: Message, start_time: float, filename: str, is_upload: bool = False):
    """Show download/upload progress"""
    try:
        now = time.time()
        elapsed = now - start_time
        speed = current / elapsed if elapsed > 0 else 0
        
        progress = min(current / total, 1.0)
        percent = progress * 100
        
        if progress > 0:
            eta_seconds = (total - current) / speed if speed > 0 else 0
            eta = time.strftime("%H:%M:%S", time.gmtime(eta_seconds))
        else:
            eta = "N/A"
        
        bar_length = 12
        filled = int(bar_length * progress)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        current_size = sizeof_fmt(current)
        total_size = sizeof_fmt(total)
        
        action = "📤 Uploading" if is_upload else "📥 Downloading"
        
        text = (
            f"{action}: <b>{filename}</b>\n"
            f"[{bar}] {percent:.1f}%\n"
            f"├ Speed: {sizeof_fmt(speed)}/s\n"
            f"├ Progress: {current_size} / {total_size}\n"
            f"└ ETA: {eta}"
        )
        
        if int(elapsed) % 3 == 0:
            await message.edit_text(text)
            
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except:
        pass

# ============================================
# DOWNLOAD FUNCTIONS
# ============================================
async def download_file(url: str, filepath: str, message: Message, filename: str) -> bool:
    """Download file from URL with progress"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                if response.status != 200:
                    await message.edit_text(f"❌ Download failed! Status: {response.status}")
                    return False
                
                content_length = response.headers.get('Content-Length')
                if content_length:
                    total_size = int(content_length)
                    if total_size > MAX_FILE_SIZE:
                        await message.edit_text(
                            f"❌ File too large! {sizeof_fmt(total_size)} > {sizeof_fmt(MAX_FILE_SIZE)}"
                        )
                        return False
                else:
                    total_size = 0
                
                downloaded = 0
                start_time = time.time()
                
                with open(filepath, 'wb') as f:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                await progress_callback(downloaded, total_size, message, start_time, filename)
                
                return True
    
    except asyncio.TimeoutError:
        await message.edit_text("❌ Download timeout!")
    except Exception as e:
        await message.edit_text(f"❌ Download error: {str(e)}")
    
    return False

async def download_youtube_video(url: str, format_id: str, filepath: str, message: Message) -> bool:
    """Download YouTube video using yt-dlp"""
    try:
        await message.edit_text("📥 Starting YouTube download...")
        
        # Build yt-dlp command
        cmd = [
            "yt-dlp",
            "-f", f"{format_id}+bestaudio/best",
            "--merge-output-format", "mp4",
            "--no-warnings",
            "--no-check-certificate",
            "-o", filepath,
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Monitor progress
        start_time = time.time()
        last_update = 0
        
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            
            line = line.decode().strip()
            if "download:" in line:
                try:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.endswith('%'):
                            percent = float(part.strip('%'))
                            downloaded = int(percent * 0.01 * MAX_FILE_SIZE)
                            if time.time() - last_update > 3:
                                await progress_callback(downloaded, MAX_FILE_SIZE, message, start_time, os.path.basename(filepath))
                                last_update = time.time()
                except:
                    pass
        
        await process.wait()
        
        if process.returncode == 0 and os.path.exists(filepath):
            return True
        else:
            await message.edit_text("❌ YouTube download failed!")
            return False
            
    except Exception as e:
        await message.edit_text(f"❌ YouTube error: {str(e)}")
        return False

async def get_youtube_formats(url: str, message: Message) -> Optional[list]:
    """Get available YouTube formats"""
    try:
        cmd = [
            "yt-dlp",
            "-j",
            "--no-warnings",
            "--no-check-certificate",
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            await message.edit_text("❌ Could not fetch video info!")
            return None
        
        data = json.loads(stdout.decode())
        
        formats = []
        for f in data.get("formats", []):
            if f.get("vcodec") != "none" and f.get("acodec") == "none":
                format_id = f.get("format_id", "")
                ext = f.get("ext", "mp4")
                height = f.get("height", 0)
                filesize = f.get("filesize", 0) or f.get("filesize_approx", 0)
                
                if height and filesize:
                    formats.append({
                        "id": format_id,
                        "ext": ext,
                        "quality": f"{height}p",
                        "size": filesize
                    })
        
        return formats
    
    except Exception as e:
        logger.error(f"Format fetch error: {e}")
        return None

# ============================================
# THUMBNAIL HANDLING
# ============================================
async def download_thumbnail(thumb_id: str, filepath: str) -> bool:
    """Download thumbnail file"""
    try:
        await bot.download_media(thumb_id, file_name=filepath)
        return True
    except:
        return False

async def get_user_thumbnail(user_id: int) -> Optional[str]:
    """Get user's thumbnail (custom or default)"""
    custom = json_db.get_thumbnail(user_id)
    return custom if custom else DEFAULT_THUMB_ID

# ============================================
# UPLOAD FUNCTION
# ============================================
async def upload_file(filepath: str, filename: str, message: Message, status_msg: Message):
    """Upload file to Telegram"""
    try:
        start_time = time.time()
        file_size = os.path.getsize(filepath)
        user_id = message.from_user.id
        
        # Get upload mode
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", False)
        
        # Get thumbnail
        thumb_path = None
        thumb_id = await get_user_thumbnail(user_id)
        if thumb_id:
            thumb_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_thumb.jpg")
            await download_thumbnail(thumb_id, thumb_path)
        
        # Determine file type
        lower_name = filename.lower()
        is_video = lower_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm'))
        is_audio = lower_name.endswith(('.mp3', '.wav', '.flac', '.m4a', '.ogg'))
        is_image = lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))
        
        await status_msg.edit_text(f"📤 Uploading...\n<b>{filename}</b>")
        
        # Upload based on type and settings
        if is_image:
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=filepath,
                caption=f"✅ <b>{filename}</b>\n📦 {sizeof_fmt(file_size)}"
            )
        elif is_audio:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=filepath,
                caption=f"✅ <b>{filename}</b>\n📦 {sizeof_fmt(file_size)}",
                thumb=thumb_path,
                progress=progress_callback,
                progress_args=(status_msg, start_time, filename, True)
            )
        elif is_video and not upload_as_doc:
            await bot.send_video(
                chat_id=message.chat.id,
                video=filepath,
                caption=f"✅ <b>{filename}</b>\n📦 {sizeof_fmt(file_size)}",
                supports_streaming=True,
                thumb=thumb_path,
                progress=progress_callback,
                progress_args=(status_msg, start_time, filename, True)
            )
        else:
            await bot.send_document(
                chat_id=message.chat.id,
                document=filepath,
                caption=f"✅ <b>{filename}</b>\n📦 {sizeof_fmt(file_size)}",
                thumb=thumb_path,
                file_name=filename,
                progress=progress_callback,
                progress_args=(status_msg, start_time, filename, True)
            )
        
        # Clean up
        await status_msg.delete()
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        if os.path.exists(filepath):
            os.remove(filepath)
        
        json_db.increment_stats()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Upload failed: {str(e)}")

# ============================================
# HANDLERS
# ============================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message: Message):
    """Handle /start command"""
    text = (
        "👋 <b>Enhanced URL Uploader Bot</b>\n\n"
        "• Download direct URLs\n"
        "• Download YouTube videos\n"
        "• Custom filenames (URL|name.mp4)\n"
        "• Custom thumbnails (send photo)\n"
        "• Progress tracking\n\n"
        "<b>Commands:</b> /help, /stats, /settings"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Help", callback_data="help")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats"),
         InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
    ])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("help") & filters.private)
async def help_command(_, message: Message):
    """Handle /help command"""
    text = (
        "📚 <b>Help</b>\n\n"
        "<b>Direct URL:</b> Send any link\n"
        "<b>YouTube:</b> Send video URL\n"
        "<b>Custom Name:</b> <code>URL|filename.mp4</code>\n"
        "<b>Thumbnail:</b> Send me a photo\n"
        "<b>Settings:</b> Toggle upload modes\n"
    )
    await message.reply_text(text)

@bot.on_message(filters.command("stats") & filters.private)
async def stats_command(_, message: Message):
    """Handle /stats command"""
    files = os.listdir(DOWNLOAD_DIR)
    total_files = len(files)
    total_size = sum(os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) for f in files)
    
    try:
        import psutil
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
    except:
        cpu = ram = disk = "N/A"
    
    stats = json_db.get_stats()
    downloads = stats["total_downloads"]
    
    text = (
        "📊 <b>Statistics</b>\n\n"
        f"├ <b>Downloads:</b> {downloads}\n"
        f"├ <b>Cache:</b> {sizeof_fmt(total_size)}\n"
        f"├ <b>CPU:</b> {cpu}%\n"
        f"├ <b>RAM:</b> {ram}%\n"
        f"└ <b>Disk:</b> {disk}%"
    )
    await message.reply_text(text)

@bot.on_message(filters.command("settings") & filters.private)
async def settings_command(_, message: Message):
    """Handle /settings command"""
    user_id = message.from_user.id
    upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", False)
    
    text = "⚙️ <b>Settings</b>\n\n<b>Upload Mode:</b> "
    text += "📹 <b>Video</b> (streaming)" if not upload_as_doc else "📁 <b>Document</b> (file)"
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Toggle Upload Mode", callback_data="toggle_upload")],
        [InlineKeyboardButton("Delete Thumbnail", callback_data="del_thumb")],
        [InlineKeyboardButton("Close", callback_data="close")]
    ])
    
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(_, message: Message):
    """Save custom thumbnail"""
    user_id = message.from_user.id
    file_id = message.photo.file_id
    
    json_db.set_thumbnail(user_id, file_id)
    await message.reply_text("✅ Thumbnail saved!")

@bot.on_message(filters.text & filters.private)
async def handle_url(_, message: Message):
    """Handle URL messages"""
    url = message.text.strip()
    
    if not is_valid_url(url):
        await message.reply_text("❌ Invalid URL!")
        return
    
    # Parse custom filename
    custom_name = None
    if "|" in url:
        parts = url.split("|", 1)
        url = parts[0].strip()
        custom_name = parts[1].strip()
    
    # Check if YouTube URL
    if is_youtube_url(url):
        await handle_youtube_url(_, message, url, custom_name)
    else:
        await handle_direct_url(_, message, url, custom_name)

async def handle_direct_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle direct download URL"""
    status_msg = await message.reply_text("🔍 Processing URL...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=30) as response:
                if response.status != 200:
                    await status_msg.edit_text(f"❌ URL not accessible! Status: {response.status}")
                    return
                
                content_disposition = response.headers.get('Content-Disposition')
                filename = get_filename_from_url(url, content_disposition, custom_name)
                
                content_length = response.headers.get('Content-Length')
                if content_length:
                    file_size = int(content_length)
                    if file_size > MAX_FILE_SIZE:
                        await status_msg.edit_text(f"❌ File too large!")
                        return
        
        # Download
        await status_msg.edit_text(f"📥 Downloading...\n<b>{filename}</b>")
        await asyncio.sleep(1)
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        success = await download_file(url, filepath, status_msg, filename)
        
        if success:
            await upload_file(filepath, filename, message, status_msg)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")

async def handle_youtube_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle YouTube URL"""
    status_msg = await message.reply_text("🔍 Fetching video info...")
    
    formats = await get_youtube_formats(url, status_msg)
    
    if not formats:
        return
    
    # Show format selection
    buttons = []
    for f in formats[:5]:
        btn_text = f"📹 {f['quality']} ({sizeof_fmt(f['size'])})"
        callback_data = f"yt_{f['id']}_{f['ext']}_{custom_name or ''}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
    
    buttons.append([InlineKeyboardButton("🔒 Close", callback_data="close")])
    
    await status_msg.edit_text(
        "🎬 <b>Select Quality:</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ============================================
# CALLBACK HANDLER
# ============================================
@bot.on_callback_query()
async def handle_callback(_, callback_query: CallbackQuery):
    """Handle button callbacks"""
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    try:
        if data == "help":
            await help_command(_, callback_query.message)
        elif data == "stats":
            await stats_command(_, callback_query.message)
        elif data == "settings":
            await settings_command(_, callback_query.message)
        elif data == "close":
            await callback_query.message.delete()
        elif data == "toggle_upload":
            current = json_db.get_setting(user_id, "upload_as_doc", False)
            json_db.set_setting(user_id, "upload_as_doc", not current)
            await settings_command(_, callback_query.message)
            await callback_query.answer("✅ Updated!")
        elif data == "del_thumb":
            json_db.delete_thumbnail(user_id)
            await callback_query.answer("✅ Deleted!")
        elif data.startswith("yt_"):
            parts = data.split("_", 3)
            if len(parts) < 3:
                return
            
            format_id = parts[1]
            ext = parts[2]
            custom_name = parts[3] if len(parts) > 3 else None
            
            await callback_query.answer("📥 Downloading...")
            
            status_msg = callback_query.message
            url = callback_query.message.reply_to_message.text
            
            filename = custom_name or f"youtube_video.{ext}"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            
            success = await download_youtube_video(url, format_id, filepath, status_msg)
            
            if success:
                await upload_file(filepath, filename, callback_query.message.reply_to_message, status_msg)
        
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Callback error: {e}")

# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    print("🚀 Starting Bot...")
    print(f"📂 Downloads: {os.path.abspath(DOWNLOAD_DIR)}")
    print(f"📊 Database: {os.path.abspath(JSON_DB_PATH)}")
    
    # Check yt-dlp
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        print("✅ yt-dlp found")
    except:
        print("⚠️  Installing yt-dlp...")
        subprocess.run(["pip", "install", "yt-dlp"], capture_output=True)
    
    # Start Flask server in background
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print("✅ Web server started on port 80")
    
    # Start bot
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
