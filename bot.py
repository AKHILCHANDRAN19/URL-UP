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
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified, FloodWait
from PIL import Image

# ============================================
# CONFIGURATION
# ============================================
API_ID = 2819362
API_HASH = "578ce3d09fadd539544a327c45b55ee4"
BOT_TOKEN = "8390475015:AAF8dauJYTWFwktTQABzG17_-JTN4r71R3M"

# Bot settings
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
DOWNLOAD_DIR = "/app/data/downloads"
JSON_DB_PATH = "/app/data/bot_data.json"

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
    max_concurrent_transmissions=10,
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
    """Format file size"""
    try:
        for unit in ["", "K", "M", "G", "T"]:
            if abs(num) < 1024.0:
                return f"{num:3.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}P{suffix}"
    except:
        return "Unknown"

def clean_filename(url_or_name: str) -> str:
    """Clean messy filenames"""
    try:
        if url_or_name.startswith('http'):
            parsed = urllib.parse.urlparse(url_or_name)
            path = urllib.parse.unquote(parsed.path.split('/')[-1])
            if not path:
                path = "video.mkv"
        else:
            path = url_or_name
        
        path = path.replace('_20_', ' ').replace('_20', ' ').replace('%20', ' ')
        path = re.sub(r'^(www\.)?[^.]+\.(com|best|cc|cab|wtf|eu|in|nl)[-_\.]', '', path)
        path = re.sub(r'[^\w\s\-_\.\(\)]', ' ', path)
        
        if not re.search(r'\.(mkv|mp4|avi|mov|webm)$', path, re.IGNORECASE):
            if any(kw in path.lower() for kw in ['720p', '1080p', '2160p', '4k', 'x264', 'x265', 'dvd', 'hq']):
                path += '.mkv'
            else:
                path += '.mp4'
        
        return ' '.join(path.split())[:200]
    except:
        return "video.mkv"

def is_valid_url(url: str) -> bool:
    """Check if URL is valid - SUPPORTS FILE-TO-LINK BOTS"""
    try:
        if not url or len(url) < 10:
            return False
        
        # SUPPORT: All file-to-link bot URLs
        if any(domain in url for domain in [
            'onrender.com/download/',
            'file.link/',
            't.me/',
            'seedr.cc/',
            'fento.me/',
            'gofile.io/',
            'pixeldrain.com/',
            'workers.dev/',
            'github.com/',
            'gitlab.com/'
        ]):
            return True
            
        if not (url.startswith('http://') or url.startswith('https://')):
            return False
            
        if not re.search(r'\.[a-z]{2,6}', url, re.IGNORECASE):
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
# ARIA2 DOWNLOAD FUNCTION - HIGH SPEED (UPDATED)
# ============================================
async def download_with_aria2(url: str, filepath: str, message: Message, filename: str) -> bool:
    """DOWNLOAD USING ARIA2C - MAXIMUM SPEED"""
    try:
        clean_name = clean_filename(filename)
        final_path = os.path.join(os.path.dirname(filepath), clean_name)
        
        # === FIX: Added explicit path and improved arguments for reliability ===
        cmd = [
            "/usr/bin/aria2c",  # Explicit path to avoid PATH issues on Render/servers
            "--console-log-level=warn", # Use 'warn' to reduce noise, errors still show
            "--summary-interval=1",
            # Standard browser User-Agent helps with "file-to-link" bots
            "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "--check-certificate=false",
            "--follow-metalink=true", # Crucial for some file-to-link bots
            "--follow-torrent=true",
            "--max-connection-per-server=16",
            "--min-split-size=1M",
            "--split=16",
            "--max-tries=10",
            "--retry-wait=5",
            "--continue=true",
            f"--dir={os.path.dirname(final_path)}",
            f"--out={os.path.basename(final_path)}",
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        start_time = time.time()
        last_update = 0
        
        # Reading output line-by-line to show progress
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_str = line.decode('utf-8', 'ignore').strip()
            # In-progress download summary line from aria2c looks like:
            # [#d3e02b 2.3MiB/4.9MiB(47%) CN:1 DL:321KiB]
            if "DL:" in line_str and "%" in line_str:
                try:
                    if time.time() - last_update > 2: # Update every 2 seconds to avoid flood waits
                        match = re.search(r'(\d+MiB)/(\d+MiB)\((\d+)%\).*DL:\s*([\d\.]+[KMG]?i?B/s)', line_str)
                        if match:
                            downloaded_str, total_str, percent_str, speed_str = match.groups()
                            downloaded = parse_size(downloaded_str.replace("MiB","MB"))
                            total = parse_size(total_str.replace("MiB","MB"))
                            await progress_callback(downloaded, total, message, start_time, clean_name)
                            last_update = time.time()
                except Exception:
                    pass # Ignore progress parsing errors
        
        # Wait for the process to finish and get the output
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0 and os.path.exists(final_path):
            return True
        else:
            # === FIX: Provide DETAILED error message on failure ===
            error_message = stderr.decode('utf-8', 'ignore').strip()
            logger.error(f"Aria2 Error: {error_message}")
            final_error = error_message.split("Exception:")[-1].strip() if "Exception:" in error_message else error_message
            await message.edit_text(f"❌ <b>Download failed!</b>\n\n<b>Reason:</b>\n<code>{final_error or 'Unknown aria2c error. Check logs.'}</code>")
            return False
            
    except FileNotFoundError:
        logger.error("aria2c executable not found at /usr/bin/aria2c")
        await message.edit_text("❌ <b>Deployment Error:</b>\n`aria2c` command not found on the server. Please ensure it is installed correctly via `apt-get install aria2`.")
        return False
    except Exception as e:
        logger.error(f"Aria2 exception: {e}")
        await message.edit_text(f"❌ An unexpected error occurred during download: {str(e)}")
        return False


def parse_size(size_str: str) -> int:
    """Parse size string like '1.2GiB' to bytes"""
    try:
        size_str = size_str.strip().upper()
        if not size_str: return 0
            
        val = float(re.search(r'([\d\.]+)', size_str).group(1))
        unit = re.search(r'([KMGT])', size_str)
        unit = unit.group(1) if unit else 'B'

        multipliers = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
        return int(val * multipliers.get(unit, 1))
    except:
        return 0

# ============================================
# THUMBNAIL HANDLING
# ============================================
async def download_thumbnail(thumb_id: str, filepath: str) -> bool:
    """Download thumbnail file"""
    try:
        await bot.download_media(thumb_id, file_name=filepath)
        return os.path.exists(filepath)
    except:
        return False

async def get_user_thumbnail_path(user_id: int) -> Optional[str]:
    """Get user's thumbnail path"""
    thumb_id = json_db.get_thumbnail(user_id) or DEFAULT_THUMB_ID
    if not thumb_id:
        return None
    
    thumb_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_thumb.jpg")
    
    # Re-download if it doesn't exist, to ensure it's always available
    if not os.path.exists(thumb_path):
        success = await download_thumbnail(thumb_id, thumb_path)
        if not success:
            return None
    
    return thumb_path if os.path.exists(thumb_path) else None

# ============================================
# YOUTUBE FUNCTIONS
# ============================================
async def download_youtube_video(url: str, format_id: str, filepath: str, message: Message) -> bool:
    """Download YouTube video"""
    try:
        await message.edit_text("📥 Starting YouTube download...")
        
        clean_name = clean_filename(os.path.basename(filepath))
        final_path = os.path.join(os.path.dirname(filepath), clean_name)
        
        cmd = [
            "yt-dlp",
            "-f", f"{format_id}+bestaudio/best",
            "--merge-output-format", "mp4",
            "--no-warnings",
            "--no-check-certificate",
            "--no-playlist",
            "--no-cache-dir",
            "-o", final_path,
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
            if "[download]" in line and "%" in line:
                try:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.endswith('%'):
                            percent = float(part.strip('%'))
                            # This is an approximation as yt-dlp doesn't always give total size
                            # We'll use a placeholder total size for progress calculation
                            placeholder_total = 1000 * 1024 * 1024 # Assume 1GB
                            downloaded = int(percent * 0.01 * placeholder_total)
                            if time.time() - last_update > 2:
                                await progress_callback(downloaded, placeholder_total, message, start_time, clean_name)
                                last_update = time.time()
                except:
                    pass
        
        await process.wait()
        
        if process.returncode == 0 and os.path.exists(final_path):
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
        cmd = ["yt-dlp", "-j", "--no-warnings", "--no-check-certificate", "--no-playlist", url]
        
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error = stderr.decode()[:200]
            await message.edit_text(f"❌ Could not fetch video info!\n<code>{error}</code>")
            return None
        
        data = json.loads(stdout.decode())
        
        formats = []
        for f in data.get("formats", []):
            if f.get("vcodec") != "none": # simplified to check for video stream
                format_id = f.get("format_id", "")
                ext = f.get("ext", "mp4")
                height = f.get("height", 0)
                filesize = f.get("filesize", 0) or f.get("filesize_approx", 0)
                
                if height and height >= 360:
                    formats.append({"id": format_id, "ext": ext, "quality": f"{height}p", "size": filesize})
        
        unique_formats = {f['quality']: f for f in formats}.values()
        sorted_formats = sorted(list(unique_formats), key=lambda x: int(x['quality'][:-1]), reverse=True)
        return sorted_formats[:5]
    
    except Exception as e:
        logger.error(f"Format fetch error: {e}")
        await message.edit_text("❌ Error parsing video info!")
        return None

# ============================================
# PROGRESS BAR - CUSTOM FORMAT
# ============================================
async def progress_callback(current: int, total: int, message: Message, start_time: float, filename: str, is_upload: bool = False):
    """CUSTOM PROGRESS BAR"""
    try:
        now = time.time()
        elapsed = now - start_time
        if elapsed < 1: return
            
        speed = current / elapsed
        progress = min(current / total, 1.0) if total > 0 else 0
        percent = progress * 100
        
        eta = "N/A"
        if progress > 0 and speed > 0:
            eta_seconds = (total - current) / speed
            minutes, seconds = divmod(int(eta_seconds), 60)
            eta = f"{minutes}m {seconds}s"
        
        bar_length = 10
        filled = int(bar_length * progress)
        bar = "▰" * filled + "▱" * (bar_length - filled)
        
        action = "⏫ Uploading" if is_upload else "⏬ Downloading"
        
        text = (
            f"<b>{action}:</b>\n"
            f"<code>{filename}</code>\n\n"
            f"<b>[{bar}] {percent:.1f}%</b>\n"
            f"{sizeof_fmt(current)} of {sizeof_fmt(total)}\n"
            f"<b>Speed:</b> {sizeof_fmt(speed)}/s\n"
            f"<b>ETA:</b> {eta}"
        )
        
        await message.edit_text(text)
            
    except MessageNotModified:
        pass
    except Exception as e:
        logger.warning(f"Progress callback error: {e}")


# ============================================
# UPLOAD FUNCTION
# ============================================
async def upload_file(filepath: str, filename: str, message: Message, status_msg: Message):
    """Upload file with thumbnail"""
    try:
        start_time = time.time()
        file_size = os.path.getsize(filepath)
        user_id = message.from_user.id
        
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        thumb_path = await get_user_thumbnail_path(user_id)
        
        clean_name = clean_filename(filename)
        
        lower_name = clean_name.lower()
        is_video = lower_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm'))
        is_audio = lower_name.endswith(('.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'))
        is_image = lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))
        
        await status_msg.edit_text(f"📤 Uploading...\n<b>{clean_name}</b>")
        
        progress_args = {
            "message": status_msg,
            "start_time": start_time,
            "filename": clean_name,
            "is_upload": True
        }

        if is_image:
            await bot.send_photo(chat_id=message.chat.id, photo=filepath, caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}")
        elif is_audio:
            await bot.send_audio(chat_id=message.chat.id, audio=filepath, caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}", thumb=thumb_path, progress=progress_callback, progress_args=list(progress_args.values()))
        elif is_video and not upload_as_doc:
            await bot.send_video(chat_id=message.chat.id, video=filepath, caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}", supports_streaming=True, thumb=thumb_path, progress=progress_callback, progress_args=list(progress_args.values()))
        else:
            await bot.send_document(chat_id=message.chat.id, document=filepath, caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}", thumb=thumb_path, file_name=clean_name, progress=progress_callback, progress_args=list(progress_args.values()))
        
        await status_msg.delete()
        
        if os.path.exists(filepath):
            os.remove(filepath)
        if thumb_path and os.path.exists(thumb_path) and f"{user_id}_thumb.jpg" in thumb_path:
             try: os.remove(thumb_path) # Clean up downloaded thumb
             except: pass
        
        json_db.increment_stats()
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await status_msg.edit_text(f"❌ Upload failed: {str(e)}")
    finally:
        # Final cleanup
        if os.path.exists(filepath):
            os.remove(filepath)

# ============================================
# HANDLERS
# ============================================
@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_help_command(_, message: Message):
    """Handle /start and /help"""
    text = (
        "👑 <b>URL Uploader Bot (Aria2 Powered)</b>\n\n"
        "Send me any direct HTTP/HTTPS link and I will download and upload it for you.\n\n"
        "<b>Features:</b>\n"
        "• ⚡ Ultra-fast downloads with `aria2c`\n"
        "• 📺 YouTube video download support\n"
        "• 🖼️ Custom thumbnails\n"
        "• 📑 Clean filenames and progress tracking\n\n"
        "<b>How to use:</b>\n"
        "1. Send an image to set it as a thumbnail.\n"
        "2. Send a direct download link.\n"
        "3. For custom filename, send link in format: `URL | new name.ext`"
    )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("📊 Stats", callback_data="stats")]])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("stats") & filters.private)
async def stats_command(_, message: Message):
    """Handle /stats"""
    try:
        files = os.listdir(DOWNLOAD_DIR)
        total_size = sum(os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) for f in files)
        
        try:
            import psutil
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
        except ImportError:
            cpu = ram = disk = "N/A"
        
        stats = json_db.get_stats()
        text = (
            "📊 <b>Bot Statistics</b>\n\n"
            f"├ <b>Total Downloads:</b> {stats['total_downloads']}\n"
            f"├ <b>Cached Files Size:</b> {sizeof_fmt(total_size)}\n"
            f"├ <b>CPU Usage:</b> {cpu}%\n"
            f"├ <b>RAM Usage:</b> {ram}%\n"
            f"└ <b>Disk Usage:</b> {disk}%"
        )
        await message.reply_text(text)
    except Exception as e:
        await message.reply_text(f"❌ Error fetching stats: {e}")

@bot.on_message(filters.command("settings") & filters.private)
async def settings_command(_, message: Message):
    """Handle /settings"""
    user_id = message.from_user.id
    upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
    mode = "📁 Document" if upload_as_doc else "📹 Video (Streamable)"
    
    text = f"⚙️ <b>Settings</b>\n\nYour current upload mode is set to: <b>{mode}</b>."
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Toggle Upload Mode", callback_data="toggle_upload")],
        [InlineKeyboardButton("🗑️ Delete Thumbnail", callback_data="del_thumb")],
        [InlineKeyboardButton("✖️ Close", callback_data="close")]
    ])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(_, message: Message):
    """Save custom thumbnail"""
    user_id = message.from_user.id
    file_id = message.photo.file_id
    json_db.set_thumbnail(user_id, file_id)
    await message.reply_text("✅ Thumbnail saved successfully!", quote=True)

@bot.on_message(filters.text & filters.private)
async def handle_url(_, message: Message):
    """Handle URL"""
    try:
        url = message.text.strip()
        if not is_valid_url(url.split('|')[0].strip()):
            await message.reply_text("❌ This doesn't look like a valid URL. Please send a valid direct download link.")
            return
        
        custom_name = None
        if "|" in url:
            url, custom_name = map(str.strip, url.split("|", 1))
        
        if is_youtube_url(url):
            await handle_youtube_url(bot, message, url, custom_name)
        else:
            await handle_direct_url(bot, message, url, custom_name)
            
    except Exception as e:
        logger.error(f"URL handler error: {e}")
        await message.reply_text("❌ An error occurred while processing your URL.")

async def handle_direct_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle direct download - NOW USES ARIA2"""
    status_msg = await message.reply_text("🔍 Analyzing URL...", quote=True)
    try:
        filename = custom_name or os.path.basename(urllib.parse.urlparse(url).path) or f"download_{int(time.time())}"
        clean_name = clean_filename(filename)
        
        await status_msg.edit_text(f"📥 Preparing to download...\n<b>{clean_name}</b>")
        
        filepath = os.path.join(DOWNLOAD_DIR, clean_name)
        success = await download_with_aria2(url, filepath, status_msg, clean_name)
        
        if success and os.path.exists(filepath):
            await upload_file(filepath, clean_name, message, status_msg)
        elif not success and await status_msg.get_edit_date():
             # If download_with_aria2 already sent an error, don't send another one
             pass
        else:
             await status_msg.edit_text("❌ Download failed! Please check the link and try again.")
        
    except Exception as e:
        logger.error(f"Direct URL error: {e}")
        await status_msg.edit_text(f"❌ An error occurred: {str(e)}")

async def handle_youtube_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle YouTube URL"""
    status_msg = await message.reply_text("🔍 Fetching YouTube video info...", quote=True)
    try:
        formats = await get_youtube_formats(url, status_msg)
        if not formats:
            return
        
        buttons = []
        for f in formats:
            size_str = sizeof_fmt(f['size']) if f['size'] else "N/A"
            btn_text = f"📹 {f['quality']} ({size_str})"
            callback_data = f"yt_{f['id']}_{f['ext']}_{custom_name or 'video'}"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
        
        buttons.append([InlineKeyboardButton("✖️ Close", callback_data="close")])
        await status_msg.edit_text("🎬 <b>Select Video Quality:</b>", reply_markup=InlineKeyboardMarkup(buttons))
        
    except Exception as e:
        logger.error(f"YouTube handler error: {e}")
        await status_msg.edit_text("❌ Error processing YouTube URL.")

@bot.on_callback_query()
async def handle_callback(_, cb: CallbackQuery):
    """Handle callbacks"""
    data = cb.data
    user_id = cb.from_user.id
    
    try:
        if data == "settings":
            await settings_command(bot, cb.message)
        elif data == "stats":
            await stats_command(bot, cb.message)
        elif data == "close":
            await cb.message.delete()
        elif data == "toggle_upload":
            current = json_db.get_setting(user_id, "upload_as_doc", True)
            json_db.set_setting(user_id, "upload_as_doc", not current)
            mode = "📹 Video" if current else "📁 Document" # Note: logic is reversed, if it WAS doc, it's now video
            await cb.answer(f"✅ Upload mode changed to {mode}", show_alert=True)
            await settings_command(bot, cb.message)
        elif data == "del_thumb":
            json_db.delete_thumbnail(user_id)
            await cb.answer("✅ Custom thumbnail deleted!", show_alert=True)
            await settings_command(bot, cb.message)
        elif data.startswith("yt_"):
            if not cb.message.reply_to_message:
                await cb.answer("❌ Original message not found!", show_alert=True)
                return
            
            url = cb.message.reply_to_message.text.split('|')[0].strip()
            
            parts = data.split("_", 3)
            format_id, ext = parts[1], parts[2]
            custom_name = parts[3] if len(parts) > 3 and parts[3] != 'video' else None
            
            await cb.message.edit_text("📥 Download starting...")
            
            status_msg = cb.message
            filename = custom_name or f"youtube_video_{int(time.time())}.{ext}"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            
            success = await download_youtube_video(url, format_id, filepath, status_msg)
            
            if success:
                await upload_file(filepath, filename, cb.message.reply_to_message, status_msg)
        
        await cb.answer()
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await cb.answer("❌ An error occurred.", show_alert=True)

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 ARIA2 POWERED BOT STARTING...")
    print("=" * 50)
    
    try:
        result = subprocess.run(["/usr/bin/aria2c", "--version"], capture_output=True, text=True, check=True)
        print(f"✅ aria2c found: {result.stdout.splitlines()[0]}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ CRITICAL: aria2c NOT FOUND at /usr/bin/aria2c!")
        print("Install on Render/Debian: apt-get update && apt-get install -y aria2")
        exit(1) # Exit if aria2c is missing

    print("🌐 Starting web server...")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    print("🤖 Starting bot...")
    bot.run()
