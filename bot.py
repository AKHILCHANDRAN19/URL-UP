import os
import sys
import re
import math
import time
import asyncio
import aiohttp
import json
import logging
import threading
import urllib.parse
import subprocess
from typing import Optional, Dict, Any
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified, FloodWait
from PIL import Image

# ============================================
# CONFIGURATION - RENDER.COM OPTIMIZED
# ============================================
API_ID = 2819362
API_HASH = "578ce3d09fadd539544a327c45b55ee4"
BOT_TOKEN = "8390475015:AAF8dauJYTWFwktTQABzG17_-JTN4r71R3M"

# Bot settings
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
BASE_DATA_DIR = "/app/data" if os.path.exists("/app") else os.getcwd()
DOWNLOAD_DIR = os.path.join(BASE_DATA_DIR, "downloads")
JSON_DB_PATH = os.path.join(BASE_DATA_DIR, "bot_data.json")
LOG_FILE_PATH = os.path.join(BASE_DATA_DIR, "bot.log")

# DEFAULT THUMBNAIL
DEFAULT_THUMB_ID = "AgACAgUAAxkBAAE9vJdpFKHL4lIezMqiAhL4U86UBU9HFAACcg5rGxoHoVRR8Xe3Z3RrUwEAAwIAA20AAzYE"

# ARIA2 AVAILABILITY CHECK
ARIA2_AVAILABLE = False
try:
    result = subprocess.run(["aria2c", "--version"], capture_output=True, timeout=5)
    ARIA2_AVAILABLE = result.returncode == 0
    print(f"✅ Aria2 available: {ARIA2_AVAILABLE}")
except:
    print("❌ Aria2 not available, using fallback downloader")

# Create directories
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(JSON_DB_PATH), exist_ok=True)

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
    return {"status": "running", "bot": "URL Uploader Bot", "aria2": ARIA2_AVAILABLE}

@app.route('/health')
def health():
    return {"status": "healthy", "aria2": ARIA2_AVAILABLE}, 200

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
    handlers=[logging.FileHandler(LOG_FILE_PATH), logging.StreamHandler()]
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
    """Check if URL is valid"""
    try:
        if not url or len(url) < 10:
            return False
        
        if any(domain in url for domain in [
            'onrender.com/download/', 'file.link/', 't.me/', 'seedr.cc/',
            'fento.me/', 'gofile.io/', 'pixeldrain.com/', 'workers.dev/',
            'github.com/', 'gitlab.com/', 'animeflix.live/', 'filebin.net/', 'transfer.sh/'
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
            r'youtube\.com/watch\?v=', r'youtu\.be/', r'youtube\.com/shorts/', r'youtube\.com/embed/'
        ]
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in youtube_patterns)
    except:
        return False

def parse_size(size_str: str) -> int:
    """Parse size string like '1.2GiB' to bytes"""
    try:
        size_str = size_str.strip()
        if not size_str:
            return 0
        match = re.match(r'([\d\.]+)\s*([KMGT]i?B)?', size_str)
        if not match:
            return 0
        number = float(match.group(1))
        unit = match.group(2) or 'B'
        multipliers = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4,
                       'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4}
        return int(number * multipliers.get(unit, 1))
    except:
        return 0

# ============================================
# THUMBNAIL HANDLING
# ============================================
async def download_thumbnail(thumb_id: str, filepath: str) -> bool:
    """Download thumbnail file"""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        await bot.download_media(thumb_id, file_name=filepath)
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0
    except:
        return False

async def get_user_thumbnail_path(user_id: int) -> Optional[str]:
    """Get user's thumbnail path"""
    thumb_id = json_db.get_thumbnail(user_id) or DEFAULT_THUMB_ID
    if not thumb_id:
        return None
    thumb_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_thumb.jpg")
    if not os.path.exists(thumb_path):
        success = await download_thumbnail(thumb_id, thumb_path)
        if not success and thumb_id != DEFAULT_THUMB_ID:
            success = await download_thumbnail(DEFAULT_THUMB_ID, thumb_path)
        if not success:
            return None
    return thumb_path if os.path.exists(thumb_path) else None

# ============================================
# FALLBACK DOWNLOADER (PURE PYTHON)
# ============================================
async def download_with_fallback(url: str, filepath: str, message: Message, filename: str) -> bool:
    """Fallback downloader using aiohttp when aria2 is unavailable"""
    try:
        clean_name = clean_filename(filename)
        final_path = os.path.join(os.path.dirname(filepath), clean_name)
        
        await message.edit_text(f"⚠️ Aria2 unavailable, using standard downloader...\n<b>{clean_name}</b>")
        logger.warning(f"Using fallback downloader for: {url}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                if response.status != 200:
                    await message.edit_text(f"❌ HTTP Error: {response.status}")
                    return False
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                start_time = time.time()
                last_update = 0
                
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                with open(final_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Update progress every 2 seconds
                        if time.time() - last_update > 2:
                            await progress_callback(downloaded, total_size or 1, message, start_time, clean_name)
                            last_update = time.time()
        
        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            logger.info(f"✅ Fallback download complete: {final_path}")
            return True
        else:
            await message.edit_text("❌ Download failed - file is empty")
            return False
            
    except Exception as e:
        logger.error(f"❌ Fallback download error: {str(e)}", exc_info=True)
        await message.edit_text(f"❌ Download error: {str(e)}")
        return False

# ============================================
# ARIA2 DOWNLOAD FUNCTION
# ============================================
async def download_with_aria2(url: str, filepath: str, message: Message, filename: str) -> bool:
    """Download using aria2c"""
    if not ARIA2_AVAILABLE:
        return await download_with_fallback(url, filepath, message, filename)
    
    try:
        clean_name = clean_filename(filename)
        final_dir = os.path.dirname(filepath)
        final_path = os.path.join(final_dir, clean_name)
        os.makedirs(final_dir, exist_ok=True)
        
        logger.info(f"Starting aria2 download: {url} -> {final_path}")
        
        cmd = [
            "aria2c",
            "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "--continue=true",
            "--summary-interval=1",
            f"--dir={final_dir}",
            f"--out={clean_name}",
            "--console-log-level=error",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--max-concurrent-downloads=8",
            "--max-tries=10",
            "--retry-wait=5",
            "--timeout=60",
            "--check-certificate=false",
            "--async-dns=false",
            "--seed-time=0",
            "--bt-enable-lpd=false",
            "--bt-max-peers=0",
            "--allow-overwrite=true",
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
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
                if not line:
                    break
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                continue
            
            line = line.decode('utf-8', errors='ignore').strip()
            logger.debug(f"aria2: {line}")
            
            if "DL:" in line and "%" in line:
                try:
                    percent_match = re.search(r'(\d+\.?\d*)%', line)
                    if percent_match:
                        percent = float(percent_match.group(1))
                        size_match = re.search(r'([0-9.]+[KMGT]?i?B)\s*/\s*([0-9.]+[KMGT]?i?B)', line)
                        if size_match:
                            downloaded = parse_size(size_match.group(1))
                            total = parse_size(size_match.group(2))
                            if time.time() - last_update > 2:
                                await progress_callback(downloaded, total, message, start_time, clean_name)
                                last_update = time.time()
                except:
                    pass
        
        await process.wait()
        
        if process.returncode == 0 and os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            logger.info(f"✅ Download complete: {final_path}")
            return True
        else:
            stderr = await process.stderr.read()
            error = stderr.decode('utf-8', errors='ignore')[:500]
            logger.error(f"❌ Aria2 failed: {error}")
            await message.edit_text(f"❌ Download failed!\n<code>{error}</code>")
            return False
            
    except Exception as e:
        logger.error(f"❌ Aria2 error: {str(e)}", exc_info=True)
        await message.edit_text(f"❌ Aria2 error: {str(e)}")
        return False

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
            if "download:" in line:
                try:
                    parts = line.split()
                    for part in parts:
                        if part.endswith('%'):
                            percent = float(part.strip('%'))
                            downloaded = int(percent * 0.01 * MAX_FILE_SIZE)
                            if time.time() - last_update > 2:
                                await progress_callback(downloaded, MAX_FILE_SIZE, message, start_time, clean_name)
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
        logger.error(f"YouTube download error: {e}")
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
# PROGRESS BAR
# ============================================
async def progress_callback(current: int, total: int, message: Message, start_time: float, filename: str, is_upload: bool = False):
    """CUSTOM PROGRESS BAR"""
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
        
        if int(elapsed) % 2 == 0:
            try:
                await message.edit_text(text)
            except MessageNotModified:
                pass
            except Exception as e:
                logger.error(f"Progress update error: {e}")
            
    except Exception as e:
        logger.error(f"Progress callback error: {e}")

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
            await bot.send_document(
                chat_id=message.chat.id,
                document=filepath,
                caption=f"✅ <b>{clean_name}</b>\n📦 {sizeof_fmt(file_size)}",
                thumb=thumb_path,
                file_name=clean_name,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        
        try:
            await status_msg.delete()
        except:
            pass
        
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up: {filepath}")
        
        json_db.increment_stats()
        
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Upload failed: {str(e)}")

# ============================================
# HANDLERS
# ============================================
@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_help_command(_, message: Message):
    """Handle /start and /help"""
    aria_status = "✅ Aria2 Active" if ARIA2_AVAILABLE else "⚠️ Aria2 Unavailable (Using Fallback)"
    text = (
        "👑 <b>ARIA2 POWERED BOT</b>\n\n"
        f"<b>Status:</b> {aria_status}\n\n"
        "<b>Features:</b>\n"
        "• ⚡ Ultra-fast downloads (aria2c)\n"
        "• 16 connections per server\n"
        "• Custom thumbnails\n"
        "• Clean filenames\n"
        "• Progress tracking\n\n"
        "<b>Commands:</b>\n"
        "/stats - Bot statistics\n"
        "/settings - Configure settings\n"
        "Send any URL to start!\n\n"
        "<b>Supported URLs:</b>\n"
        "• Direct links (seedr.cc, file.link, etc.)\n"
        "• YouTube videos\n"
        "• File-to-link bot URLs"
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
        files = [f for f in os.listdir(DOWNLOAD_DIR) if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))]
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
            f"└ <b>Disk:</b> {disk}%\n\n"
            f"<b>Aria2 Status:</b> {'✅ Available' if ARIA2_AVAILABLE else '❌ Unavailable'}"
        )
        await message.reply_text(text)
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await message.reply_text("❌ Error fetching stats")

@bot.on_message(filters.command("settings") & filters.private)
async def settings_command(_, message: Message):
    """Handle /settings"""
    try:
        user_id = message.from_user.id
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        mode = "📁 Document (file)" if upload_as_doc else "📹 Video (streaming)"
        text = f"⚙️ <b>Settings</b>\n\n<b>Upload Mode:</b> {mode}\n<b>Aria2:</b> {'✅' if ARIA2_AVAILABLE else '❌'}"
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("Toggle Upload Mode", callback_data="toggle_upload")],
            [InlineKeyboardButton("Delete Thumbnail", callback_data="del_thumb")],
            [InlineKeyboardButton("Close", callback_data="close")]
        ])
        
        await message.reply_text(text, reply_markup=buttons)
    except Exception as e:
        logger.error(f"Settings error: {e}")
        await message.reply_text("❌ Error loading settings")

@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(_, message: Message):
    """Save custom thumbnail"""
    try:
        user_id = message.from_user.id
        file_id = message.photo.file_id
        
        json_db.set_thumbnail(user_id, file_id)
        await message.reply_text("✅ Thumbnail saved!\n\nWill be used for all video/file uploads.")
    except Exception as e:
        logger.error(f"Thumbnail save error: {e}")
        await message.reply_text("❌ Failed to save thumbnail")

@bot.on_message(filters.text & filters.private)
async def handle_url(_, message: Message):
    """Handle URL"""
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
            await message.reply_text("❌ Invalid URL format!\n\nSupported: direct links, YouTube, file-to-link bots")
            return
        
        if is_youtube_url(url):
            await handle_youtube_url(_, message, url, custom_name)
        else:
            await handle_direct_url(_, message, url, custom_name)
            
    except Exception as e:
        logger.error(f"URL handler error: {e}", exc_info=True)
        await message.reply_text("❌ Error processing URL")

async def handle_direct_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle direct download"""
    status_msg = None
    try:
        status_msg = await message.reply_text("🔍 Processing URL...")
        
        # Get filename
        parsed_url = urllib.parse.urlparse(url)
        
        if custom_name:
            filename = custom_name
        else:
            path_name = os.path.basename(parsed_url.path)
            if path_name and '.' in path_name:
                filename = urllib.parse.unquote(path_name)
            else:
                filename = f"file_{int(time.time())}.mkv"
        
        # Clean filename
        clean_name = clean_filename(filename)
        
        # Show filename
        await status_msg.edit_text(f"📥 Downloading...\n<b>{clean_name}</b>\n{'⚡ Using Aria2' if ARIA2_AVAILABLE else '🐌 Using Fallback'}")
        
        # Use ARIA2 or fallback
        filepath = os.path.join(DOWNLOAD_DIR, clean_name)
        if ARIA2_AVAILABLE:
            success = await download_with_aria2(url, filepath, status_msg, clean_name)
        else:
            success = await download_with_fallback(url, filepath, status_msg, clean_name)
        
        if success:
            await upload_file(filepath, clean_name, message, status_msg)
        else:
            if os.path.exists(filepath):
                os.remove(filepath)
            await status_msg.edit_text("❌ Download failed! Check logs for details.")
        
    except Exception as e:
        logger.error(f"Direct URL error: {e}", exc_info=True)
        if status_msg:
            await status_msg.edit_text(f"❌ Critical error: {str(e)}")
        else:
            await message.reply_text(f"❌ Critical error: {str(e)}")

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
        for f in formats:
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
        logger.error(f"YouTube handler error: {e}", exc_info=True)
        if status_msg:
            await status_msg.edit_text("❌ Error processing YouTube URL")

@bot.on_callback_query()
async def handle_callback(_, callback_query: CallbackQuery):
    """Handle callbacks"""
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
            # Extract URL
            url = None
            if callback_query.message.reply_to_message:
                url = callback_query.message.reply_to_message.text
            else:
                text = callback_query.message.text
                if text and "http" in text:
                    lines = text.split('\n')
                    for line in lines:
                        if line.startswith('http'):
                            url = line.strip()
                            break
            
            if not url:
                await callback_query.answer("❌ No URL found!")
                return
            
            parts = data.split("_", 3)
            if len(parts) < 3:
                await callback_query.answer("❌ Invalid callback data!")
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
                target_message = callback_query.message.reply_to_message or callback_query.message
                await upload_file(filepath, filename, target_message, status_msg)
        
        try:
            await callback_query.answer()
        except:
            pass
            
    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        try:
            await callback_query.answer("❌ Error processing callback")
        except:
            pass

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 ARIA2 POWERED BOT STARTING...")
    print("⚡ MAXIMUM SPEED MODE")
    print(f"📁 Data Directory: {BASE_DATA_DIR}")
    print(f"🛠️ Aria2 Available: {ARIA2_AVAILABLE}")
    print("=" * 60)
    
    # Check dependencies
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        print("✅ yt-dlp OK:", result.stdout.strip())
    except:
        print("📦 Installing yt-dlp...")
        subprocess.run(["pip", "install", "-q", "yt-dlp"], capture_output=True)
    
    if ARIA2_AVAILABLE:
        try:
            result = subprocess.run(["aria2c", "--version"], capture_output=True, text=True, timeout=10)
            print("✅ aria2c OK:", result.stdout.split('\n')[0])
        except:
            print("❌ aria2c verification failed")
    else:
        print("⚠️  Using fallback downloader (slower but reliable)")
    
    # Start web server
    print("🌐 Starting web server...")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start bot
    print("🤖 Starting bot...")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped gracefully")
    except Exception as e:
        print(f"❌ Bot error: {e}")
        logger.error(f"Bot crash: {e}", exc_info=True)
