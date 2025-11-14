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
import urllib.parse
from typing import Optional, Dict, Any
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Thumbnail
from pyrogram.errors import MessageNotModified, FloodWait

# ============================================
# CONFIGURATION - RENDER COMPATIBLE
# ============================================
API_ID = 2819362
API_HASH = "578ce3d09fadd539544a327c45b55ee4"
BOT_TOKEN = "8390475015:AAF8dauJYTWFwktTQABzG17_-JTN4r71R3M"

# Bot settings
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks - INCREASED FOR SPEED
DOWNLOAD_DIR = "./downloads"
JSON_DB_PATH = "./bot_data.json"
YTDL_PATH = "./yt-dlp"

# DEFAULT THUMBNAIL
DEFAULT_THUMB_ID = "AgACAgUAAxkBAAE9vJdpFKHL4lIezMqiAhL4U86UBU9HFAACcg5rGxoHoVRR8Xe3Z3RrUwEAAwIAA20AAzYE"

# Create directories
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================
# JSON STORAGE
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
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.data, f, indent=2)
        except:
            pass
    
    def get_thumbnail(self, user_id: int) -> Optional[str]:
        return self.data["thumbnails"].get(str(user_id))
    
    def set_thumbnail(self, user_id: int, file_id: str):
        self.data["thumbnails"][str(user_id)] = file_id
        self.save()
    
    def delete_thumbnail(self, user_id: int):
        if str(user_id) in self.data["thumbnails"]:
            del self.data["thumbnails"][str(user_id)]
            self.save()
    
    def get_setting(self, user_id: int, key: str, default: Any = True) -> Any:
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
# FLASK WEB SERVER
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "running", "bot": "URL Uploader Bot", "uptime": time.time()}

@app.route('/health')
def health():
    return {"status": "healthy"}, 200

def run_web_server():
    try:
        app.run(host='0.0.0.0', port=80, debug=False, use_reloader=False)
    except:
        pass

# ============================================
# BOT INITIALIZATION
# ============================================
bot = Client(
    "url_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    max_concurrent_transmissions=5,  # INCREASED
    parse_mode=enums.ParseMode.HTML,
    sleep_threshold=30
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
    try:
        for unit in ["", "K", "M", "G", "T"]:
            if abs(num) < 1024.0:
                return f"{num:3.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}P{suffix}"
    except:
        return "Unknown"

def clean_filename(filename: str) -> str:
    """CLEAN MESSY FILENAMES LIKE: www.1TamilMV.best_20-_20The_20Pet_20Detective..."""
    try:
        # Remove URL prefixes
        filename = re.sub(r'^https?://[^/]+/', '', filename)
        
        # Handle URL encoded spaces (%20) and other patterns
        filename = filename.replace('_20_', ' ').replace('_20', ' ').replace('%20', ' ')
        
        # Remove unwanted patterns
        filename = re.sub(r'www\.[^.]+\.(com|best|cc|io|in)[-_\.]', '', filename)
        
        # Clean up multiple spaces
        filename = ' '.join(filename.split())
        
        # Remove trailing numbers and dashes
        filename = re.sub(r'[-_\s]+\d+$', '', filename)
        
        # Fix extensions - prioritize common video extensions
        if not re.search(r'\.(mkv|mp4|avi|mov|webm)$', filename, re.IGNORECASE):
            # If it has video-related keywords, assume .mkv
            if any(keyword in filename.lower() for keyword in ['720p', '1080p', 'x264', 'x265', 'dvd', 'hd']):
                filename += '.mkv'
            else:
                filename += '.mp4'
        
        # Capitalize first letters for readability
        filename = ' '.join(word.capitalize() if word.islower() else word for word in filename.split())
        
        # Limit length
        return filename[:200]
    except Exception as e:
        logger.error(f"Filename cleaning error: {e}")
        return "video.mkv"

def is_valid_url(url: str) -> bool:
    """Check if URL is valid - SUPPORTS FILE-TO-LINK REDIRECTORS"""
    try:
        if not url or len(url) < 10:
            return False
        
        # Support for redirector URLs like file-to-link bots
        if any(domain in url for domain in ['onrender.com/download/', 'file.link/', 't.me/']):
            return True
            
        # Standard URL validation
        if not (url.startswith('http://') or url.startswith('https://')):
            return False
            
        if '.' not in url[8:]:
            return False
            
        return True
    except:
        return False

def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video"""
    try:
        youtube_patterns = [
            r'youtube\.com/watch\?v=',
            r'youtu\.be/',
            r'youtube\.com/shorts/',
            r'youtube\.com/embed/'
        ]
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in youtube_patterns)
    except:
        return False

# ============================================
# PROGRESS BAR - CUSTOM FORMAT
# ============================================
async def progress_callback(current: int, total: int, message: Message, start_time: float, filename: str, is_upload: bool = False):
    """CUSTOM PROGRESS BAR - Matches user's format exactly"""
    try:
        now = time.time()
        elapsed = now - start_time
        if elapsed < 0.1:
            return
            
        speed = current / elapsed if elapsed > 0 else 0
        
        progress = min(current / total, 1.0)
        percent = progress * 100
        
        if progress > 0:
            eta_seconds = (total - current) / speed if speed > 0 else 0
            minutes, seconds = divmod(int(eta_seconds), 60)
            eta = f"{minutes}m, {seconds}s" if minutes > 0 else f"{seconds}s"
        else:
            eta = "N/A"
        
        # Progress bar with solid squares
        bar_length = 10
        filled = int(bar_length * progress)
        bar = "▪" * filled + "▫" * (bar_length - filled)
        
        action = "Uploading" if is_upload else "Downloading"
        
        text = (
            f"{action}: <b>{percent:.2f}%</b>\n"
            f"[{bar}]\n"
            f"<code>{sizeof_fmt(current)} of {sizeof_fmt(total)}</code>\n"
            f"Speed: <code>{sizeof_fmt(speed)}/sec</code>\n"
            f"ETA: <code>{eta}</code>\n\n"
            f"📁 <b>{filename}</b>\n\n"
            f"Thanks for using 👑"
        )
        
        if int(elapsed) % 2 == 0:  # Update every 2 seconds
            try:
                await message.edit_text(text)
            except MessageNotModified:
                pass
            except:
                pass
            
    except:
        pass

# ============================================
# DOWNLOAD FUNCTIONS
# ============================================
async def download_file(url: str, filepath: str, message: Message, filename: str) -> bool:
    """Download file with redirect support - INCREASED SPEED"""
    try:
        # Clean filename first
        clean_name = clean_filename(filename)
        filepath = os.path.join(os.path.dirname(filepath), clean_name)
        
        async with aiohttp.ClientSession() as session:
            # Allow redirects and increase timeout
            timeout = aiohttp.ClientTimeout(total=7200, connect=60, sock_read=300)
            async with session.get(url, timeout=timeout, allow_redirects=True) as response:
                if response.status not in [200, 206]:
                    await message.edit_text(f"❌ Download failed! Status: {response.status}")
                    return False
                
                # Get final URL after redirects
                final_url = str(response.url)
                
                content_length = response.headers.get('Content-Length')
                if content_length:
                    total_size = int(content_length)
                    if total_size > MAX_FILE_SIZE:
                        await message.edit_text(f"❌ File too large! {sizeof_fmt(total_size)}")
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
                                await progress_callback(downloaded, total_size, message, start_time, clean_name)
                
                return True
    
    except asyncio.TimeoutError:
        await message.edit_text("❌ Download timeout!")
    except Exception as e:
        await message.edit_text(f"❌ Download error: {str(e)}")
    
    return False

async def download_youtube_video(url: str, format_id: str, filepath: str, message: Message) -> bool:
    """Download YouTube video - INCREASED SPEED"""
    try:
        await message.edit_text("📥 Starting YouTube download...")
        
        # Clean filename
        clean_name = clean_filename(os.path.basename(filepath))
        filepath = os.path.join(os.path.dirname(filepath), clean_name)
        
        cmd = [
            "yt-dlp",
            "-f", f"{format_id}+bestaudio/best",
            "--merge-output-format", "mp4",
            "--no-warnings",
            "--no-check-certificate",
            "--no-playlist",
            "--no-cache-dir",
            "-o", filepath,
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
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
                            if time.time() - last_update > 2:
                                await progress_callback(downloaded, MAX_FILE_SIZE, message, start_time, clean_name)
                                last_update = time.time()
                except:
                    pass
        
        await process.wait()
        
        if process.returncode == 0 and os.path.exists(filepath):
            # Rename to clean filename
            final_path = os.path.join(os.path.dirname(filepath), clean_filename(clean_name))
            os.rename(filepath, final_path)
            return True
        else:
            _, stderr = await process.communicate()
            error_msg = stderr.decode()[:200]
            await message.edit_text(f"❌ YouTube download failed!\n<code>{error_msg}</code>")
            return False
            
    except Exception as e:
        await message.edit_text(f"❌ YouTube error: {str(e)}")
        return False

async def get_youtube_formats(url: str, message: Message) -> Optional[list]:
    """Get YouTube formats"""
    try:
        cmd = [
            "yt-dlp",
            "-j",
            "--no-warnings",
            "--no-check-certificate",
            "--no-playlist",
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error = stderr.decode()[:200]
            await message.edit_text(f"❌ Could not fetch video info!\n<code>{error}</code>")
            return None
        
        data = json.loads(stdout.decode())
        
        formats = []
        for f in data.get("formats", []):
            if f.get("vcodec") != "none" and f.get("acodec") != "none":
                format_id = f.get("format_id", "")
                ext = f.get("ext", "mp4")
                height = f.get("height", 0)
                filesize = f.get("filesize", 0) or f.get("filesize_approx", 0)
                
                if height and height >= 360:
                    formats.append({
                        "id": format_id,
                        "ext": ext,
                        "quality": f"{height}p",
                        "size": filesize
                    })
        
        formats.sort(key=lambda x: x.get('height', 0), reverse=True)
        return formats[:5]
    
    except Exception as e:
        logger.error(f"Format fetch error: {e}")
        await message.edit_text("❌ Error parsing video info!")
        return None

# ============================================
# UPLOAD FUNCTION - FIXED: THUMBNAILS ATTACHED PROPERLY
# ============================================
async def upload_file(filepath: str, filename: str, message: Message, status_msg: Message):
    """Upload file with THUMBNAIL ATTACHED - FIXED"""
    try:
        start_time = time.time()
        file_size = os.path.getsize(filepath)
        user_id = message.from_user.id
        
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        # Get thumbnail path
        thumb_path = await get_user_thumbnail_path(user_id)
        
        # Clean filename
        clean_name = clean_filename(filename)
        
        # Determine file type
        lower_name = clean_name.lower()
        is_video = lower_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm'))
        is_audio = lower_name.endswith(('.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'))
        is_image = lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))
        
        await status_msg.edit_text(f"📤 Uploading...\n<b>{clean_name}</b>")
        
        # Upload with thumbnail for all types - FIXED
        if is_image:
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=filepath,
                caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}"
            )
        elif is_audio:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=filepath,
                caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}",
                thumb=thumb_path,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        elif is_video and not upload_as_doc:
            await bot.send_video(
                chat_id=message.chat.id,
                video=filepath,
                caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}",
                supports_streaming=True,
                thumb=thumb_path,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        else:
            # FIXED: Thumbnail for documents too!
            await bot.send_document(
                chat_id=message.chat.id,
                document=filepath,
                caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}",
                thumb=thumb_path,  # THUMBNAIL FOR FILES!
                file_name=clean_name,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        
        # Clean up
        try:
            await status_msg.delete()
        except:
            pass
        
        if os.path.exists(filepath):
            os.remove(filepath)
        
        json_db.increment_stats()
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await status_msg.edit_text(f"❌ Upload failed: {str(e)}")

# ============================================
# HANDLERS
# ============================================
@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_help_command(_, message: Message):
    """Handle /start and /help"""
    text = (
        "👑 <b>Enhanced URL Uploader Bot</b>\n\n"
        "<b>Features:</b>\n"
        "• High-speed downloads (5MB chunks)\n"
        "• YouTube video downloads\n"
        "• Custom filenames: <code>URL|name.mp4</code>\n"
        "• Custom thumbnails for all files\n"
        "• Clean filename formatting\n"
        "• File-to-link bot support\n\n"
        "<b>Commands:</b>\n"
        "/stats - Bot statistics\n"
        "/settings - Configure settings\n"
        "Send any URL to start!"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="stats"),
         InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
    ])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("stats") & filters.private)
async def stats_command(_, message: Message):
    """Handle /stats"""
    try:
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
            "📊 <b>Bot Statistics</b>\n\n"
            f"├ <b>Downloads:</b> {downloads}\n"
            f"├ <b>Cache:</b> {sizeof_fmt(total_size)}\n"
            f"├ <b>CPU:</b> {cpu}%\n"
            f"├ <b>RAM:</b> {ram}%\n"
            f"└ <b>Disk:</b> {disk}%"
        )
        await message.reply_text(text)
    except:
        await message.reply_text("❌ Error fetching stats")

@bot.on_message(filters.command("settings") & filters.private)
async def settings_command(_, message: Message):
    """Handle /settings"""
    try:
        user_id = message.from_user.id
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        mode = "📁 Document (file)" if upload_as_doc else "📹 Video (streaming)"
        text = f"⚙️ <b>Settings</b>\n\n<b>Upload Mode:</b> {mode}"
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("Toggle Upload Mode", callback_data="toggle_upload")],
            [InlineKeyboardButton("Delete Thumbnail", callback_data="del_thumb")],
            [InlineKeyboardButton("Close", callback_data="close")]
        ])
        
        await message.reply_text(text, reply_markup=buttons)
    except:
        await message.reply_text("❌ Error loading settings")

@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(_, message: Message):
    """Save custom thumbnail"""
    try:
        user_id = message.from_user.id
        file_id = message.photo.file_id
        
        json_db.set_thumbnail(user_id, file_id)
        await message.reply_text("✅ Thumbnail saved!\n\nWill be used for all video/file uploads.")
    except:
        await message.reply_text("❌ Failed to save thumbnail")

@bot.on_message(filters.text & filters.private)
async def handle_url(_, message: Message):
    """Handle URL with redirect support"""
    try:
        url = message.text.strip()
        
        if not url or len(url) < 10:
            await message.reply_text("❌ Invalid URL!")
            return
        
        # Parse custom filename
        custom_name = None
        if "|" in url:
            parts = url.split("|", 1)
            url = parts[0].strip()
            custom_name = parts[1].strip()
            if not custom_name:
                custom_name = None
        
        if not is_valid_url(url):
            await message.reply_text("❌ Invalid URL!")
            return
        
        if is_youtube_url(url):
            await handle_youtube_url(_, message, url, custom_name)
        else:
            await handle_direct_url(_, message, url, custom_name)
            
    except Exception as e:
        logger.error(f"URL handler error: {e}")
        await message.reply_text("❌ Error processing URL")

async def handle_direct_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle direct download with redirect support"""
    status_msg = None
    try:
        status_msg = await message.reply_text("🔍 Processing URL...")
        
        async with aiohttp.ClientSession() as session:
            # Support redirects with higher timeout
            timeout = aiohttp.ClientTimeout(total=7200)
            async with session.head(url, timeout=timeout, allow_redirects=True) as response:
                if response.status not in [200, 206]:
                    await status_msg.edit_text(f"❌ URL not accessible! Status: {response.status}")
                    return
                
                # Get final URL after redirects
                final_url = str(response.url)
                
                content_disposition = response.headers.get('Content-Disposition')
                
                # Generate clean filename
                original_name = os.path.basename(final_url.split('?')[0]) or "file"
                filename = custom_name or clean_filename(original_name)
                
                content_length = response.headers.get('Content-Length')
                if content_length:
                    file_size = int(content_length)
                    if file_size > MAX_FILE_SIZE:
                        await status_msg.edit_text(f"❌ File too large! {sizeof_fmt(file_size)}")
                        return
        
        # Download
        await status_msg.edit_text(f"📥 Downloading...\n<b>{filename}</b>")
        await asyncio.sleep(0.5)
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        success = await download_file(url, filepath, status_msg, filename)
        
        if success:
            await upload_file(filepath, filename, message, status_msg)
        else:
            await status_msg.edit_text("❌ Download failed!")
        
    except Exception as e:
        logger.error(f"Direct URL error: {e}")
        if status_msg:
            await status_msg.edit_text(f"❌ Error: {str(e)}")

async def handle_youtube_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle YouTube URL"""
    status_msg = None
    try:
        status_msg = await message.reply_text("🔍 Fetching video info...")
        
        formats = await get_youtube_formats(url, status_msg)
        
        if not formats:
            return
        
        # Show format selection
        buttons = []
        for f in formats[:5]:
            size_str = sizeof_fmt(f['size']) if f['size'] else "Unknown"
            btn_text = f"📹 {f['quality']} ({size_str})"
            callback_data = f"yt_{f['id']}_{f['ext']}_{custom_name or 'video'}"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
        
        buttons.append([InlineKeyboardButton("🔒 Close", callback_data="close")])
        
        await status_msg.edit_text(
            "🎬 <b>Select Quality:</b>",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error(f"YouTube handler error: {e}")
        if status_msg:
            await status_msg.edit_text("❌ Error processing YouTube URL")

@bot.on_callback_query()
async def handle_callback(_, callback_query: CallbackQuery):
    """Handle callbacks - FIXED"""
    try:
        data = callback_query.data
        user_id = callback_query.from_user.id
        
        if data == "help":
            await start_help_command(_, callback_query.message)
        elif data == "stats":
            await stats_command(_, callback_query.message)
        elif data == "settings":
            await settings_command(_, callback_query.message)
        elif data == "close":
            try:
                await callback_query.message.delete()
            except:
                pass
        elif data == "toggle_upload":
            current = json_db.get_setting(user_id, "upload_as_doc", True)
            json_db.set_setting(user_id, "upload_as_doc", not current)
            mode = "📹 Video" if not current else "📁 Document"
            await callback_query.answer(f"✅ Mode: {mode}")
            await settings_command(_, callback_query.message)
        elif data == "del_thumb":
            json_db.delete_thumbnail(user_id)
            await callback_query.answer("✅ Thumbnail deleted!")
            await settings_command(_, callback_query.message)
        elif data.startswith("yt_"):
            if not callback_query.message.reply_to_message:
                await callback_query.answer("❌ Original message not found!")
                return
            
            url = callback_query.message.reply_to_message.text
            if not url:
                await callback_query.answer("❌ No URL found!")
                return
            
            parts = data.split("_", 3)
            if len(parts) < 3:
                return
            
            format_id = parts[1]
            ext = parts[2]
            custom_name = parts[3] if len(parts) > 3 and parts[3] != 'video' else None
            
            await callback_query.answer("📥 Downloading...")
            
            status_msg = callback_query.message
            filename = custom_name or f"youtube_video_{int(time.time())}.{ext}"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            
            success = await download_youtube_video(url, format_id, filepath, status_msg)
            
            if success:
                await upload_file(filepath, filename, callback_query.message.reply_to_message, status_msg)
        
        try:
            await callback_query.answer()
        except:
            pass
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            await callback_query.answer("❌ Error")
        except:
            pass

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Starting URL Uploader Bot...")
    print("⚡ HIGH-SPEED MODE ENABLED")
    print("=" * 50)
    
    # Install yt-dlp if missing
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        print(f"✅ yt-dlp: {result.stdout.strip()}")
    except:
        print("📦 Installing yt-dlp...")
        try:
            subprocess.run(["pip", "install", "-q", "yt-dlp"], capture_output=True, timeout=60)
            print("✅ Installed")
        except Exception as e:
            print(f"❌ Install failed: {e}")
    
    # Start web server
    print("🌐 Starting web server...")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start bot
    print("🤖 Starting bot...")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
