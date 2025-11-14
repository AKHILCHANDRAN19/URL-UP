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
# CONFIGURATION - FIXED FOR RENDER.COM
# ============================================
API_ID = 2819362
API_HASH = "578ce3d09fadd539544a327c45b55ee4"
BOT_TOKEN = "8390475015:AAF8dauJYTWFwktTQABzG17_-JTN4r71R3M"

# Bot settings
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
BASE_DATA_DIR = "/app/data" if os.path.exists("/app") else os.path.expanduser("~")
DOWNLOAD_DIR = os.path.join(BASE_DATA_DIR, "downloads")
JSON_DB_PATH = os.path.join(BASE_DATA_DIR, "bot_data.json")
LOG_FILE_PATH = os.path.join(BASE_DATA_DIR, "bot.log")

# DEFAULT THUMBNAIL
DEFAULT_THUMB_ID = "AgACAgUAAxkBAAE9vJdpFKHL4lIezMqiAhL4U86UBU9HFAACcg5rGxoHoVRR8Xe3Z3RrUwEAAwIAA20AAzYE"

# Force ARIA2 check with explicit path
ARIA2_AVAILABLE = False
try:
    for path in ["/usr/bin/aria2c", "/usr/local/bin/aria2c", "aria2c"]:
        if subprocess.run([path, "--version"], capture_output=True, timeout=5, stdin=subprocess.DEVNULL).returncode == 0:
            ARIA2_AVAILABLE = True
            print("FOUND ARIA2 AT: {}".format(path))
            break
except Exception as e:
    print("ARIA2 CHECK FAILED: {}".format(e))

# Create directories with proper permissions
os.makedirs(DOWNLOAD_DIR, exist_ok=True, mode=0o755)
os.makedirs(os.path.dirname(JSON_DB_PATH), exist_ok=True, mode=0o755)

# ============================================
# JSON STORAGE
# ============================================
class JsonStorage:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = self.load()
    
    def load(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logging.error("JSON LOAD ERROR: {}".format(e))
        return {"thumbnails": {}, "settings": {}, "stats": {"total_downloads": 0}}
    
    def save(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logging.error("JSON SAVE ERROR: {}".format(e))
    
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
        return self.data["settings"].get(str(user_id), {}).get(key, default)
    
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
    except Exception as e:
        logging.error("WEB SERVER ERROR: {}".format(e))

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
                return "{:3.1f}{}{}".format(num, unit, suffix)
            num /= 1024.0
        return "{:.1f}P{}".format(num, suffix)
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
            path += '.mkv' if any(kw in path.lower() for kw in ['720p', '1080p', '2160p', '4k', 'x264', 'x265']) else '.mp4'
        
        return ' '.join(path.split())[:200]
    except:
        return "video.mkv"

def is_valid_url(url: str) -> bool:
    """Check if URL is valid"""
    try:
        if not url or len(url) < 10:
            return False
        
        if any(domain in url for domain in [
            'seedr.cc', 'file.link/', 't.me/', 'gofile.io/', 'pixeldrain.com/',
            'workers.dev/', 'github.com/', 'gitlab.com/', 'onrender.com/',
            'filebin.net/', 'transfer.sh/', 'animeflix.live/'
        ]):
            return True
            
        if not (url.startswith('http://') or url.startswith('https://')):
            return False
            
        return bool(re.search(r'\.[a-z]{2,6}', url, re.IGNORECASE))
    except:
        return False

def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video"""
    try:
        patterns = [r'youtube\.com/watch\?v=', r'youtu\.be/', r'youtube\.com/shorts/', r'youtube\.com/embed/']
        return any(re.search(p, url, re.IGNORECASE) for p in patterns)
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
# THUMBNAIL HANDLING - FIXED
# ============================================
async def download_thumbnail(thumb_id: str, filepath: str) -> bool:
    """Download and validate thumbnail file"""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # Download thumbnail
        await bot.download_media(thumb_id, file_name=filepath)
        
        # Validate and fix thumbnail
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # Open and convert to ensure it's a valid JPEG
            with Image.open(filepath) as img:
                # Convert to RGB if necessary
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Resize if too large (Telegram limit: 320x320)
                if img.width > 320 or img.height > 320:
                    img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                
                # Save as JPEG with quality 85
                img.save(filepath, 'JPEG', quality=85, optimize=True)
            
            # Check file size (Telegram limit: 200KB)
            if os.path.getsize(filepath) > 200 * 1024:
                # Compress further if needed
                with Image.open(filepath) as img:
                    img.save(filepath, 'JPEG', quality=70, optimize=True)
            
            logger.info("THUMBNAIL VALIDATED: {} ({} bytes)".format(filepath, os.path.getsize(filepath)))
            return True
        
        return False
    except Exception as e:
        logger.error("THUMBNAIL VALIDATION ERROR: {}".format(e))
        # Remove invalid file
        if os.path.exists(filepath):
            os.remove(filepath)
        return False

async def get_user_thumbnail_path(user_id: int) -> Optional[str]:
    """Get user's thumbnail path with fallback to default"""
    # Try user thumbnail first
    thumb_id = json_db.get_thumbnail(user_id)
    thumb_path = None
    
    if thumb_id:
        thumb_path = os.path.join(DOWNLOAD_DIR, "{}_thumb.jpg".format(user_id))
        if not os.path.exists(thumb_path):
            success = await download_thumbnail(thumb_id, thumb_path)
            if not success:
                thumb_path = None
    
    # Fallback to default thumbnail
    if not thumb_path:
        thumb_path = os.path.join(DOWNLOAD_DIR, "default_thumb.jpg")
        if not os.path.exists(thumb_path):
            success = await download_thumbnail(DEFAULT_THUMB_ID, thumb_path)
            if not success:
                return None
    
    return thumb_path if os.path.exists(thumb_path) else None

# ============================================
# FALLBACK DOWNLOADER (PURE PYTHON)
# ============================================
async def download_with_fallback(url: str, filepath: str, message: Message, filename: str) -> bool:
    """Fallback downloader using aiohttp"""
    try:
        clean_name = clean_filename(filename)
        final_path = os.path.join(os.path.dirname(filepath), clean_name)
        
        await message.edit_text("WARNING: Using standard downloader...\n{}\nSpeed will be slower than aria2".format(clean_name))
        logger.warning("FALLBACK DOWNLOAD: {}".format(url))
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                if response.status != 200:
                    await message.edit_text("HTTP Error: {}".format(response.status))
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
                        
                        if time.time() - last_update > 2:
                            await progress_callback(downloaded, total_size or 1, message, start_time, clean_name)
                            last_update = time.time()
        
        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            logger.info("FALLBACK DOWNLOAD COMPLETE: {}".format(final_path))
            return True
        else:
            await message.edit_text("Download failed - file is empty")
            return False
            
    except Exception as e:
        logger.error("FALLBACK ERROR: {}".format(e), exc_info=True)
        await message.edit_text("Download error: {}".format(str(e)))
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
        
        logger.info("STARTING ARIA2 DOWNLOAD: {} -> {}".format(url, final_path))
        
        cmd = [
            "aria2c",
            "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "--continue=true",
            "--summary-interval=1",
            "--dir={}".format(final_dir),
            "--out={}".format(clean_name),
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
            logger.debug("ARIA2: {}".format(line))
            
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
            logger.info("DOWNLOAD COMPLETE: {}".format(final_path))
            return True
        else:
            stderr = await process.stderr.read()
            error = stderr.decode('utf-8', errors='ignore')[:500]
            logger.error("ARIA2 FAILED: {}".format(error))
            await message.edit_text("Download failed!\n{}".format(error))
            return False
            
    except Exception as e:
        logger.error("ARIA2 ERROR: {}".format(str(e)), exc_info=True)
        return await download_with_fallback(url, filepath, message, filename)

# ============================================
# YOUTUBE FUNCTIONS
# ============================================
async def download_youtube_video(url: str, format_id: str, filepath: str, message: Message) -> bool:
    """Download YouTube video"""
    try:
        await message.edit_text("Starting YouTube download...")
        clean_name = clean_filename(os.path.basename(filepath))
        final_path = os.path.join(os.path.dirname(filepath), clean_name)
        
        cmd = [
            "yt-dlp",
            "-f", "{}+bestaudio/best".format(format_id),
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
            await message.edit_text("YouTube download failed!\n{}".format(error_msg))
            return False
            
    except Exception as e:
        logger.error("YOUTUBE DOWNLOAD ERROR: {}".format(e))
        await message.edit_text("YouTube error: {}".format(str(e)))
        return False

async def get_youtube_formats(url: str, message: Message) -> Optional[list]:
    """Get YouTube formats"""
    try:
        cmd = ["yt-dlp", "-j", "--no-warnings", "--no-check-certificate", "--no-playlist", url]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error = stderr.decode()[:200]
            await message.edit_text("Could not fetch video info!\n{}".format(error))
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
                        "quality": "{}p".format(height),
                        "size": filesize
                    })
        
        formats.sort(key=lambda x: x.get('height', 0), reverse=True)
        return formats[:5]
    
    except Exception as e:
        logger.error("FORMAT FETCH ERROR: {}".format(e))
        await message.edit_text("Error parsing video info!")
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
            eta = "{}m {}s".format(minutes, seconds) if minutes > 0 else "{}s".format(seconds)
        else:
            eta = "N/A"
        
        bar_length = 10
        filled = int(bar_length * progress)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        action = "UPLOADING" if is_upload else "DOWNLOADING"
        
        text = (
            "{}: {:.2f}%\n"
            "[{}]\n"
            "{} of {}\n"
            "SPEED: {}/sec\n"
            "ETA: {}\n\n"
            "FILE: {}\n\n"
            "BOT BY @YOURUSERNAME"
        ).format(
            action, percent, bar,
            sizeof_fmt(current), sizeof_fmt(total),
            sizeof_fmt(speed), eta, filename
        )
        
        if int(elapsed) % 2 == 0:
            try:
                await message.edit_text(text)
            except MessageNotModified:
                pass
            except Exception as e:
                logger.error("PROGRESS UPDATE ERROR: {}".format(e))
            
    except Exception as e:
        logger.error("PROGRESS CALLBACK ERROR: {}".format(e))

# ============================================
# UPLOAD FUNCTION - FIXED THUMBNAIL
# ============================================
async def upload_file(filepath: str, filename: str, message: Message, status_msg: Message):
    """Upload file with thumbnail - FIXED"""
    try:
        start_time = time.time()
        file_size = os.path.getsize(filepath)
        user_id = message.from_user.id
        
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        # CRITICAL FIX: Get thumbnail BEFORE upload
        thumb_path = await get_user_thumbnail_path(user_id)
        if thumb_path:
            logger.info("USING THUMBNAIL: {} ({} bytes)".format(thumb_path, os.path.getsize(thumb_path)))
        else:
            logger.warning("NO THUMBNAIL AVAILABLE")
        
        clean_name = clean_filename(filename)
        lower_name = clean_name.lower()
        is_video = lower_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm'))
        is_audio = lower_name.endswith(('.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'))
        is_image = lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))
        
        await status_msg.edit_text("UPLOADING...\n{}".format(clean_name))
        
        # Upload with thumbnail
        if is_image:
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=filepath,
                caption="SUCCESS: {}\nSIZE: {}".format(clean_name, sizeof_fmt(file_size))
            )
        elif is_audio:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=filepath,
                caption="SUCCESS: {}\nSIZE: {}".format(clean_name, sizeof_fmt(file_size)),
                thumb=thumb_path,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        elif is_video and not upload_as_doc:
            # CRITICAL: For videos, thumbnail is MANDATORY for preview
            await bot.send_video(
                chat_id=message.chat.id,
                video=filepath,
                caption="SUCCESS: {}\nSIZE: {}".format(clean_name, sizeof_fmt(file_size)),
                supports_streaming=True,
                thumb=thumb_path,  # This enables thumbnail preview
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        else:
            await bot.send_document(
                chat_id=message.chat.id,
                document=filepath,
                caption="SUCCESS: {}\nSIZE: {}".format(clean_name, sizeof_fmt(file_size)),
                thumb=thumb_path,
                file_name=clean_name,
                progress=progress_callback,
                progress_args=(status_msg, start_time, clean_name, True)
            )
        
        try:
            await status_msg.delete()
        except:
            pass
        
        # Clean up
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info("CLEANED UP: {}".format(filepath))
        
        json_db.increment_stats()
        
    except Exception as e:
        logger.error("UPLOAD ERROR: {}".format(e), exc_info=True)
        await status_msg.edit_text("UPLOAD FAILED: {}".format(str(e)))

# ============================================
# HANDLERS
# ============================================
@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_help_command(_, message: Message):
    """Handle /start and /help"""
    aria_status = "ACTIVE" if ARIA2_AVAILABLE else "UNAVAILABLE (SLOW MODE)"
    text = (
        "ARIA2 POWERED BOT\n\n"
        "STATUS: {}\n\n"
        "FEATURES:\n"
        "- Ultra-fast downloads (aria2c)\n"
        "- Custom thumbnails (JPEG format)\n"
        "- Clean filenames\n"
        "- Progress tracking\n\n"
        "COMMANDS:\n"
        "/stats - Bot statistics\n"
        "/settings - Configure settings\n"
        "/thumb - Set custom thumbnail\n\n"
        "SUPPORTED URLs:\n"
        "- Direct links (seedr.cc, file.link, etc.)\n"
        "- YouTube videos\n"
        "- File-to-link bot URLs"
    ).format(aria_status)
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("STATS", callback_data="stats"),
         InlineKeyboardButton("SETTINGS", callback_data="settings")]
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
            "STATISTICS\n\n"
            "DOWNLOADS: {}\n"
            "CACHE: {}\n"
            "CPU: {}%\n"
            "RAM: {}%\n"
            "DISK: {}%\n\n"
            "ARIA2: {}\n"
            "THUMBNAIL: AUTO-APPLIED"
        ).format(
            downloads, sizeof_fmt(total_size), cpu, ram, disk,
            "AVAILABLE" if ARIA2_AVAILABLE else "UNAVAILABLE"
        )
        await message.reply_text(text)
    except Exception as e:
        logger.error("STATS ERROR: {}".format(e))
        await message.reply_text("ERROR FETCHING STATS")

@bot.on_message(filters.command("settings") & filters.private)
async def settings_command(_, message: Message):
    """Handle /settings"""
    try:
        user_id = message.from_user.id
        upload_as_doc = json_db.get_setting(user_id, "upload_as_doc", True)
        
        mode = "DOCUMENT (FILE)" if upload_as_doc else "VIDEO (STREAMING)"
        text = "SETTINGS\n\nUPLOAD MODE: {}\nARIA2: {}".format(
            mode, "AVAILABLE" if ARIA2_AVAILABLE else "UNAVAILABLE"
        )
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("TOGGLE UPLOAD MODE", callback_data="toggle_upload")],
            [InlineKeyboardButton("DELETE THUMBNAIL", callback_data="del_thumb")],
            [InlineKeyboardButton("CLOSE", callback_data="close")]
        ])
        
        await message.reply_text(text, reply_markup=buttons)
    except Exception as e:
        logger.error("SETTINGS ERROR: {}".format(e))
        await message.reply_text("ERROR LOADING SETTINGS")

@bot.on_message(filters.command("thumb") & filters.private)
async def thumb_info(_, message: Message):
    """Info about setting thumbnail"""
    await message.reply_text(
        "SET CUSTOM THUMBNAIL\n\n"
        "Just send any photo to this bot and it will be used as thumbnail for all your video/file uploads!\n\n"
        "REQUIREMENTS:\n"
        "- JPEG format (auto-converted)\n"
        "- Max size: 200KB (auto-compressed)\n"
        "- Resolution: 320x320 max (auto-resized)"
    )

@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(_, message: Message):
    """Save custom thumbnail"""
    try:
        user_id = message.from_user.id
        file_id = message.photo.file_id
        
        # Validate it's a photo
        if not message.photo:
            await message.reply_text("Please send a valid photo!")
            return
        
        json_db.set_thumbnail(user_id, file_id)
        
        # Test download and validate
        test_path = os.path.join(DOWNLOAD_DIR, "{}_temp_thumb.jpg".format(user_id))
        if await download_thumbnail(file_id, test_path):
            os.remove(test_path)
            await message.reply_text("THUMBNAIL SAVED AND VALIDATED!\n\nWill be used for all video/file uploads.")
        else:
            await message.reply_text("WARNING: Thumbnail saved but may be invalid. Please send a JPEG image.")
            
    except Exception as e:
        logger.error("THUMBNAIL SAVE ERROR: {}".format(e))
        await message.reply_text("FAILED TO SAVE THUMBNAIL")

@bot.on_message(filters.text & filters.private)
async def handle_url(_, message: Message):
    """Handle URL"""
    try:
        url = message.text.strip()
        
        if not url or len(url) < 10:
            await message.reply_text("INVALID URL!")
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
            await message.reply_text("INVALID URL FORMAT!\n\nSupported: direct links, YouTube, file-to-link bots")
            return
        
        if is_youtube_url(url):
            await handle_youtube_url(_, message, url, custom_name)
        else:
            await handle_direct_url(_, message, url, custom_name)
            
    except Exception as e:
        logger.error("URL HANDLER ERROR: {}".format(e), exc_info=True)
        await message.reply_text("ERROR PROCESSING URL")

async def handle_direct_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle direct download"""
    status_msg = None
    try:
        status_msg = await message.reply_text("PROCESSING URL...")
        
        # Get filename
        parsed_url = urllib.parse.urlparse(url)
        
        if custom_name:
            filename = custom_name
        else:
            path_name = os.path.basename(parsed_url.path)
            if path_name and '.' in path_name:
                filename = urllib.parse.unquote(path_name)
            else:
                filename = "file_{}.mkv".format(int(time.time()))
        
        # Clean filename
        clean_name = clean_filename(filename)
        
        # Show filename
        downloader_name = "ARIA2" if ARIA2_AVAILABLE else "STANDARD"
        await status_msg.edit_text("DOWNLOADING...\n{}\nDownloader: {}".format(clean_name, downloader_name))
        
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
            await status_msg.edit_text("DOWNLOAD FAILED! CHECK LOGS FOR DETAILS.")
        
    except Exception as e:
        logger.error("DIRECT URL ERROR: {}".format(e), exc_info=True)
        if status_msg:
            await status_msg.edit_text("CRITICAL ERROR: {}".format(str(e)))
        else:
            await message.reply_text("CRITICAL ERROR: {}".format(str(e)))

async def handle_youtube_url(_, message: Message, url: str, custom_name: Optional[str]):
    """Handle YouTube URL"""
    status_msg = None
    try:
        status_msg = await message.reply_text("FETCHING VIDEO INFO...")
        
        formats = await get_youtube_formats(url, status_msg)
        
        if not formats:
            return
        
        # Show format selection
        buttons = []
        for f in formats:
            size_str = sizeof_fmt(f['size']) if f['size'] else "Unknown"
            btn_text = "{} ({})".format(f['quality'], size_str)
            callback_data = "yt_{}_{}_{}".format(f['id'], f['ext'], custom_name or 'video')
            buttons.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
        
        buttons.append([InlineKeyboardButton("CLOSE", callback_data="close")])
        
        await status_msg.edit_text(
            "SELECT QUALITY:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error("YOUTUBE HANDLER ERROR: {}".format(e), exc_info=True)
        if status_msg:
            await status_msg.edit_text("ERROR PROCESSING YOUTUBE URL")

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
            mode = "VIDEO" if not current else "DOCUMENT"
            await callback_query.answer("MODE: {}".format(mode))
            await settings_command(_, callback_query.message)
        elif data == "del_thumb":
            json_db.delete_thumbnail(user_id)
            await callback_query.answer("THUMBNAIL DELETED!")
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
                await callback_query.answer("NO URL FOUND!")
                return
            
            parts = data.split("_", 3)
            if len(parts) < 3:
                await callback_query.answer("INVALID CALLBACK DATA!")
                return
            
            format_id = parts[1]
            ext = parts[2]
            custom_name = parts[3] if len(parts) > 3 and parts[3] != 'video' else None
            
            await callback_query.answer("DOWNLOADING...")
            
            status_msg = callback_query.message
            filename = custom_name or "youtube_video_{}.{}".format(int(time.time()), ext)
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
        logger.error("CALLBACK ERROR: {}".format(e), exc_info=True)
        try:
            await callback_query.answer("ERROR PROCESSING CALLBACK")
        except:
            pass

if __name__ == "__main__":
    print("=" * 60)
    print("ARIA2 POWERED BOT STARTING...")
    print("MAXIMUM SPEED MODE")
    print("DATA DIRECTORY: {}".format(BASE_DATA_DIR))
    print("ARIA2 AVAILABLE: {}".format(ARIA2_AVAILABLE))
    print("=" * 60)
    
    # Check dependencies
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        print("YT-DLP OK: {}".format(result.stdout.strip()))
    except:
        print("INSTALLING YT-DLP...")
        subprocess.run(["pip", "install", "-q", "yt-dlp"], capture_output=True)
    
    if ARIA2_AVAILABLE:
        try:
            result = subprocess.run(["aria2c", "--version"], capture_output=True, text=True, timeout=10)
            print("ARIA2C OK: {}".format(result.stdout.split('\n')[0]))
        except:
            print("ARIA2C VERIFICATION FAILED")
    else:
        print("USING FALLBACK DOWNLOADER (SLOWER BUT RELIABLE)")
    
    # Start web server
    print("STARTING WEB SERVER...")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start bot
    print("STARTING BOT...")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nBOT STOPPED GRACEFULLY")
    except Exception as e:
        print("BOT ERROR: {}".format(e))
        logger.error("BOT CRASH: {}".format(e), exc_info=True)
