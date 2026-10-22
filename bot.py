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
import yt_dlp
# Import library TikTok
from TikTokApi import TikTokApi
import asyncio

# --- KEEP ALIVE SERVER ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
})

async def fetch_tiktok_bypass(url: str, workdir: str) -> tuple:
    vid_id = re.search(r"/video/(\d+)", url)
    if not vid_id:
        # Resolve shortlink
        res = S.get(url, allow_redirects=True)
        vid_id = re.search(r"/video/(\d+)", res.url)
        
    if not vid_id:
        raise Exception("ID Video TikTok tidak ditemukan.")

    # Pakai TikTokApi buat nembak API pake signature asli
    async with TikTokApi() as api:
        await api.create_sessions(ms_tokens=[], num_sessions=1, sleep_after=3)
        video = api.video(id=vid_id.group(1))
        data = await video.info()
        
        play_addr = data.get("video", {}).get("playAddr")
        author = data.get("author", {}).get("uniqueId", "Unknown")
        title = data.get("desc", "No Title")
        stats = data.get("stats", {})
        likes = stats.get("diggCount", 0)
        comments = stats.get("commentCount", 0)
        shares = stats.get("shareCount", 0)

        if not play_addr:
            raise Exception("Gagal dapet link playAddr.")

        # Download video pake requests biasa
        r = S.get(play_addr, stream=True, timeout=30, headers={"Referer": "https://www.tiktok.com/"})
        r.raise_for_status()
        
        fp = os.path.join(workdir, f"tt_{os.urandom(4).hex()}.mp4")
        with open(fp, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

        caption = (
            f"👤 *Uploader:* @{author}\n"
            f"❤️ *Likes:* {likes:,} | 💬 *Comments:* {comments:,}\n"
            f"🔁 *Shares:* {shares:,}\n\n"
            f"📝 *Description:*\n{title}"
        )
        return fp, caption

def fetch_yt(url: str, workdir: str) -> tuple:
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
        
        title = info.get("title", "No Title")
        uploader = info.get("uploader", "Unknown")
        likes = info.get("like_count", 0)
        caption = f"👤 *Uploader:* {uploader}\n❤️ *Likes:* {likes:,}\n\n📝 *Title:*\n{title}"
        return fp, caption

async def process_download(update: Update, url: str):
    workdir = tempfile.mkdtemp(prefix="tgdl_")
    reply_target = update.message if update.message else update.effective_message
    status_msg = await reply_target.reply_text("⏳ Memproses link...")
    
    try:
        if "tiktok" in url.lower():
            filepath, caption = await fetch_tiktok_bypass(url, workdir)
        else:
            loop = asyncio.get_running_loop()
            filepath, caption = await loop.run_in_executor(None, fetch_yt, url, workdir)

        if not filepath or not os.path.exists(filepath):
            raise Exception("File kosong.")

        p = Path(filepath)
        if p.stat().st_size / (1024 * 1024) > 49:
            await status_msg.edit_text("❌ File > 49 MB.")
            return

        await status_msg.edit_text("📤 Mengunggah video...")
        with open(p, "rb") as f:
            await reply_target.reply_video(
                video=f, caption=caption, parse_mode="Markdown",
                supports_streaming=True, read_timeout=120, write_timeout=120,
            )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Gagal: {str(e)[:100]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

async def cmd_vt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Format: `/vt <link>`")
        return
    await process_download(update, context.args[0])

async def cmd_yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Format: `/yt <link>`")
        return
    await process_download(update, context.args[0])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PHOTO_FILE_ID
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Memuat menu...")
    caption = "*Your ultimate OSINT & Anti-Scam Companion.*\n\nBongkar identitas penipu, lacak jejak digital, dan amankan data lo sendiri."
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📥 TikTok & YouTube Downloader", callback_data="menu_dl")]])

    if PHOTO_FILE_ID:
        banner_msg = await context.bot.send_photo(chat_id=update.effective_chat.id, photo=PHOTO_FILE_ID, caption=caption, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        try:
            with open("banner.jpg", "rb") as photo_file:
                banner_msg = await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_file, caption=caption, parse_mode="Markdown", reply_markup=reply_markup)
                if banner_msg.photo: PHOTO_FILE_ID = banner_msg.photo[-1].file_id
        except FileNotFoundError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=caption, parse_mode="Markdown", reply_markup=reply_markup)
    try: await msg.delete()
    except Exception: pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "menu_dl":
        await query.message.edit_caption(caption="*📥 TikTok & YouTube Downloader*\n\nKirim link atau ketik `/vt <link>` / `/yt <link>`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]))
    elif query.data == "menu_back":
        await query.message.edit_caption(caption="*Your ultimate OSINT & Anti-Scam Companion.*\n\nBongkar identitas penipu, lacak jejak digital, dan amankan data lo sendiri.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 TikTok & YouTube Downloader", callback_data="menu_dl")]]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = URL_RE.search(update.message.text or "")
    if m:
        await process_download(update, m.group(1))

async def main_async():
    keep_alive()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("vt", cmd_vt))
    application.add_handler(CommandHandler("yt", cmd_yt))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
