import os
import re
import asyncio
import tempfile
import shutil
from pathlib import Path
from flask import Flask
import threading

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import requests

# --- KEEP ALIVE SERVER (BIAR RENDER NGGAK MATI) ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    # Matiin reloader & debug biar ga bikin tabrakan thread
    app_web.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# --- KONFIGURASI BOT ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PHOTO_FILE_ID = None

URL_RE = re.compile(
    r"(https?://(?:www\.)?(?:"
    r"tiktok\.com/@[^\s/]+/video/\d+|"
    r"(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+|"
    r"youtube\.com/watch\?v=[\w-]+|"
    r"youtu\.be/[\w-]+|"
    r"youtube\.com/shorts/[\w-]+"
    r")[^\s]*)",
    re.IGNORECASE,
)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

def download_file(url: str, workdir: str) -> str:
    res = S.get(url, stream=True, timeout=30, headers={"Referer": "https://www.tiktok.com/"})
    res.raise_for_status()
    fp = os.path.join(workdir, f"vid_{os.urandom(4).hex()}.mp4")
    with open(fp, "wb") as f:
        for chunk in res.iter_content(chunk_size=65536):
            f.write(chunk)
    return fp

def fetch_tiktok_data(url: str, workdir: str) -> tuple:
    try:
        res = S.get("https://www.tikwm.com/api/", params={"url": url}, timeout=15)
        res.raise_for_status()
        data = res.json()
        
        if data.get("code") != 0:
            raise Exception("API limit/diblokir.")
            
        info = data.get("data", {})
        play_url = info.get("play") or info.get("hdplay")
        if play_url.startswith("/"):
            play_url = "https://www.tikwm.com" + play_url
            
        author = info.get("author", {}).get("unique_id", "Unknown")
        title = info.get("title", "No Title")
        likes = info.get("digg_count", 0)
        comments = info.get("comment_count", 0)
        saves = info.get("collect_count", 0)
        shares = info.get("share_count", 0)
        
        caption = (
            f"👤 *Uploader:* @{author}\n"
            f"❤️ *Likes:* {likes:,} | 💬 *Comments:* {comments:,}\n"
            f"⭐ *Saves:* {saves:,} | 🔁 *Shares:* {shares:,}\n\n"
            f"📝 *Description:*\n{title}"
        )
        
        filepath = download_file(play_url, workdir)
        return filepath, caption
        
    except Exception as e:
        raise Exception(f"Gagal narik data TikTok: {str(e)}")

def fetch_yt_dlp(url: str, workdir: str) -> tuple:
    import yt_dlp
    opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 48 * 1024 * 1024,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fp = ydl.prepare_filename(info)
        if not os.path.exists(fp):
            base, _ = os.path.splitext(fp)
            for ext in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base + ext): 
                    fp = base + ext
                    break
        
        title = info.get("title", "No Title")
        uploader = info.get("uploader", "Unknown")
        likes = info.get("like_count", 0)
        
        caption = f"👤 *Uploader:* {uploader}\n❤️ *Likes:* {likes:,}\n\n📝 *Title:*\n{title}"
        return fp, caption

def _download_sync(url: str, workdir: str) -> tuple:
    if "tiktok" in url.lower():
        return fetch_tiktok_data(url, workdir)
    else:
        return fetch_yt_dlp(url, workdir)

async def process_download(update: Update, url: str):
    workdir = tempfile.mkdtemp(prefix="tgdl_")
    reply_target = update.message if update.message else update.effective_message
    status_msg = await reply_target.reply_text("⏳ Memproses link...")
    
    try:
        loop = asyncio.get_running_loop()
        filepath, caption = await loop.run_in_executor(None, _download_sync, url, workdir)

        if not filepath or not os.path.exists(filepath):
            raise Exception("File kosong.")

        p = Path(filepath)
        if p.stat().st_size / (1024 * 1024) > 49:
            await status_msg.edit_text("❌ File > 49 MB (Batas API Telegram).")
            return

        await status_msg.edit_text("📤 Mengunggah video...")
        with open(p, "rb") as f:
            await reply_target.reply_video(
                video=f,
                caption=caption,
                parse_mode="Markdown",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Gagal: {str(e)[:100]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

async def cmd_vt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Kirim link-nya juga. Contoh: `/vt https://vt.tiktok.com/...`", parse_mode="Markdown")
        return
    url = args[0]
    if not url.startswith("http"):
        await update.message.reply_text("⚠️ Format link nggak valid. Harusnya diawali `http`.")
        return
    await process_download(update, url)

async def cmd_yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Kirim link-nya juga. Contoh: `/yt https://youtu.be/...`", parse_mode="Markdown")
        return
    url = args[0]
    if not url.startswith("http"):
        await update.message.reply_text("⚠️ Format link nggak valid. Harusnya diawali `http`.")
        return
    await process_download(update, url)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PHOTO_FILE_ID
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Memuat menu...")

    caption = (
        "*Your ultimate OSINT & Anti-Scam Companion.*\n\n"
        "Bongkar identitas penipu, lacak jejak digital, dan amankan data lo sendiri. "
        "Gak ada tempat sembunyi buat para scammer."
    )

    keyboard = [
        [InlineKeyboardButton("📥 TikTok & YouTube Downloader", callback_data="menu_dl")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if PHOTO_FILE_ID:
        banner_msg = await context.bot.send_photo(
            chat_id=update.effective_chat.id, photo=PHOTO_FILE_ID, caption=caption,
            parse_mode="Markdown", reply_markup=reply_markup,
        )
    else:
        try:
            with open("banner.jpg", "rb") as photo_file:
                banner_msg = await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=photo_file, caption=caption,
                    parse_mode="Markdown", reply_markup=reply_markup,
                )
                if banner_msg.photo: PHOTO_FILE_ID = banner_msg.photo[-1].file_id
        except FileNotFoundError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=caption,
                parse_mode="Markdown", reply_markup=reply_markup,
            )
    
    try: await msg.delete()
    except Exception: pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_dl":
        await query.message.edit_caption(
            caption=(
                "*📥 TikTok & YouTube Downloader*\n\n"
                "Silakan kirim link video TikTok atau YouTube yang mau lo unduh. "
                "Atau lo bisa langsung ketik command `/vt <link>` atau `/yt <link>`.\n\n"
                "Bot bakal otomatis mendeteksi link dan mengirim videonya dalam format MP4."
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]
            ])
        )
    elif query.data == "menu_back":
        caption = (
            "*Your ultimate OSINT & Anti-Scam Companion.*\n\n"
            "Bongkar identitas penipu, lacak jejak digital, dan amankan data lo sendiri. "
            "Gak ada tempat sembunyi buat para scammer."
        )
        await query.message.edit_caption(
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 TikTok & YouTube Downloader", callback_data="menu_dl")]
            ])
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m: return
    url = re.sub(r"[^\w/\-:?=&.]+$", "", m.group(1))
    await process_download(update, url)

# --- FUNGSI UTAMA ASYNC ---
async def main_async():
    # 1. Nyalain web server di background
    keep_alive()

    # 2. Setup bot
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("vt", cmd_vt))
    application.add_handler(CommandHandler("yt", cmd_yt))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 3. Inisialisasi & jalanin polling secara async
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    
    # 4. Tahan proses biar ga langsung exit
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
