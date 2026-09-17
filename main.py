import os
import re
import json
import logging
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "-1003731982238")

# 3 destination channels
DEST_CHANNELS = [
    os.getenv("DEST_CHANNEL_1", "-1002192056669"),
    os.getenv("DEST_CHANNEL_2", "-1004325944173"),
    os.getenv("DEST_CHANNEL_3", "-1004450442203"),
]

REPLACE_USERNAME = os.getenv("REPLACE_USERNAME", "@LearnWithVishnu")
FOOTER = os.getenv("FOOTER", "@LearnWithVishnu 💫 | @SkillWithCourse")
WATERMARK_TEXT = os.getenv(
    "WATERMARK_TEXT",
    "@LearnWithVishnu 💫 | @SkillWithCourse"
)

EXTRA_LINKS = [
    "https://t.me/+Qu0lkdS9bik4ZTdl",
    "https://t.me/+c1dA64xFxf1kZjI1",
]

FONT_PATH = os.getenv(
    "FONT_PATH",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)
COUNTER_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "counter.json"
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def validate_settings():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not SOURCE_CHANNEL:
        raise RuntimeError("SOURCE_CHANNEL is missing.")
    if len(DEST_CHANNELS) != 3 or any(not x for x in DEST_CHANNELS):
        raise RuntimeError("All 3 destination channels are required.")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram bot is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health server listening on port %s", port)
    server.serve_forever()


# ---------------- COUNTER ----------------

def load_counter():
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("count", 1)
    except Exception as e:
        logger.warning("Could not load counter: %s", e)
    return 1


def save_counter(n):
    with open(COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump({"count": n}, f)


def next_count():
    n = load_counter()
    save_counter(n + 1)
    return n


# ---------------- WATERMARK ----------------

def add_watermark(image_bytes):
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(20, width // 18)

    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except Exception:
        font = ImageFont.load_default()

    text = WATERMARK_TEXT
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = (width - text_w) // 2
    y = height - text_h - int(height * 0.04)

    draw.rectangle(
        [(0, y - 14), (width, y + text_h + 14)],
        fill=(0, 0, 0, 160),
    )
    draw.text(
        (x + 2, y + 2),
        text,
        font=font,
        fill=(0, 0, 0, 200),
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )

    result = Image.alpha_composite(img, overlay).convert("RGB")

    output = BytesIO()
    output.name = "watermarked.jpg"
    result.save(output, format="JPEG", quality=92)
    output.seek(0)

    return output


# ---------------- TEXT ----------------

def process_text(text, number, source_link):
    text = re.sub(r"@\w+", REPLACE_USERNAME, text or "")

    links = []
    if source_link:
        links.append(source_link)

    links.extend(EXTRA_LINKS)

    links_text = "\n\n".join(links)

    if links_text:
        return (
            f"{number}. {text}\n\n"
            f"{links_text}\n\n"
            f"{FOOTER}"
        ).strip()

    return f"{number}. {text}\n\n{FOOTER}".strip()


def build_source_link(channel_id, message_id):
    cid = str(channel_id)

    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"

    return f"https://t.me/{cid.lstrip('@')}/{message_id}"


# ---------------- SEND TO ALL 3 ----------------

async def send_to_destinations(ctx, msg, caption):
    errors = []

    # For an image, create the watermark once and send it to all 3.
    if msg.photo:
        file = await ctx.bot.get_file(msg.photo[-1].file_id)
        img_bytes = await file.download_as_bytearray()
        watermarked = bytes(add_watermark(bytes(img_bytes)).getvalue())

        for channel_id in DEST_CHANNELS:
            try:
                image_file = BytesIO(watermarked)
                image_file.name = "watermarked.jpg"

                await ctx.bot.send_photo(
                    chat_id=channel_id,
                    photo=image_file,
                    caption=caption,
                )
                logger.info("Image sent to %s", channel_id)
            except Exception as e:
                errors.append(f"{channel_id}: {e}")
                logger.exception("Failed to send image to %s", channel_id)

    elif msg.video:
        for channel_id in DEST_CHANNELS:
            try:
                await ctx.bot.send_video(
                    chat_id=channel_id,
                    video=msg.video.file_id,
                    caption=caption,
                )
                logger.info("Video sent to %s", channel_id)
            except Exception as e:
                errors.append(f"{channel_id}: {e}")
                logger.exception("Failed to send video to %s", channel_id)

    elif msg.document:
        for channel_id in DEST_CHANNELS:
            try:
                await ctx.bot.send_document(
                    chat_id=channel_id,
                    document=msg.document.file_id,
                    caption=caption,
                )
                logger.info("Document sent to %s", channel_id)
            except Exception as e:
                errors.append(f"{channel_id}: {e}")
                logger.exception("Failed to send document to %s", channel_id)

    elif msg.text:
        for channel_id in DEST_CHANNELS:
            try:
                await ctx.bot.send_message(
                    chat_id=channel_id,
                    text=caption,
                )
                logger.info("Text sent to %s", channel_id)
            except Exception as e:
                errors.append(f"{channel_id}: {e}")
                logger.exception("Failed to send text to %s", channel_id)

    else:
        raise ValueError("Unsupported media type")

    if errors:
        raise RuntimeError("Some destinations failed: " + " | ".join(errors))


# ---------------- COMMANDS ----------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Bot chal raha hai!\n\n"
        f"Next post number: {load_counter()}\n\n"
        f"3 destination channels active hain.\n\n"
        f"/set 50 - next post #51 se\n"
        f"/count - current number dekho"
    )


async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /set 50")
        return

    n = int(ctx.args[0])
    save_counter(n + 1)

    await update.message.reply_text(
        f"Set ho gaya! Next post: #{n + 1}"
    )


async def cmd_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Next post number: {load_counter()}"
    )


# ---------------- DM / FORWARD ----------------

async def handle_forwarded(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if not msg:
        return

    if not (msg.photo or msg.video or msg.document or msg.text):
        return

    number = next_count()
    source_link = ""

    try:
        origin = msg.forward_origin

        if (
            origin
            and getattr(origin, "chat", None)
            and getattr(origin, "message_id", None)
        ):
            source_link = build_source_link(
                origin.chat.id,
                origin.message_id,
            )
    except Exception as e:
        logger.warning("Could not build forwarded source link: %s", e)

    raw_text = msg.caption or msg.text or ""
    caption = process_text(raw_text, number, source_link)

    try:
        await send_to_destinations(ctx, msg, caption)
        await msg.reply_text(
            f"Post #{number} teeno destination channels me bhej diya!"
        )
    except Exception as e:
        logger.exception("Error sending forwarded post")
        await msg.reply_text(f"Error: {e}")


# ---------------- SOURCE CHANNEL AUTO ----------------

async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post

    if not msg:
        return

    chat_id_str = str(msg.chat.id)
    chat_username = f"@{msg.chat.username}" if msg.chat.username else None

    if chat_id_str != SOURCE_CHANNEL and chat_username != SOURCE_CHANNEL:
        return

    number = next_count()
    source_link = build_source_link(
        msg.chat.id,
        msg.message_id,
    )

    raw_text = msg.caption or msg.text or ""
    caption = process_text(raw_text, number, source_link)

    try:
        await send_to_destinations(ctx, msg, caption)
        logger.info("Channel post #%s sent to all destinations.", number)
    except Exception:
        logger.exception("Error sending channel post #%s", number)


def main():
    validate_settings()

    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("count", cmd_count))

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_forwarded,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POSTS,
            handle_channel_post,
        )
    )

    logger.info("Bot started!")
    logger.info("Source: %s", SOURCE_CHANNEL)
    logger.info("Destinations: %s", DEST_CHANNELS)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
