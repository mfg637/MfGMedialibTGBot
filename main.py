import io
import logging
import pathlib
import pyimglib

from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes

import medialib_db
import secrets

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = (
        "Welcome to @mfg637's personal media library.\n"
        "Type /safe to get random SFW image.\n"
        "Other commands is not supported."
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=response)

async def default_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm not a chatbot!")

async def safe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ORIGIN_URL_TEMPLATE = {
        "derpibooru": "https://derpibooru.org/images/{}",
        "ponybooru": "https://ponybooru.org/images/{}",
        "twibooru": "https://twibooru.org/{}",
        "e621": "https://e621.net/posts/{}",
        "furbooru": "https://furbooru.org/images/{}",
        "furaffinity": "https://www.furaffinity.net/view/{}/"
    }

    tags_groups = [{"not": False, "tags": ["safe"], "count": 1}]
    raw_content_list = medialib_db.files_by_tag_search.get_media_by_tags(
        *tags_groups,
        limit=1,
        offset=0,
        order_by=medialib_db.files_by_tag_search.ORDERING_BY.RANDOM,
        filter_hidden=medialib_db.files_by_tag_search.HIDDEN_FILTERING.FILTER
    )
    medialib_connection = medialib_db.common.make_connection()
    content_id = raw_content_list[0][0]
    content_metadata = medialib_db.get_content_metadata_by_content_id(content_id, medialib_connection)

    text_response = []
    if content_metadata[2] is not None:
        text_response.append("Title: {}".format(content_metadata[2]))
    if content_metadata[4] is not None:
        if len(content_metadata[4]) < 512:
            text_response.append("Description: {}".format(content_metadata[4]))
        else:
            text_response.append("Description is too long.")
    if content_metadata[6] is not None and content_metadata[7] is not None:
        text_response.append(
            "Source: {}".format(ORIGIN_URL_TEMPLATE[content_metadata[6]].format(content_metadata[7]))
        )
    file_path = medialib_db.config.relative_to.joinpath(content_metadata[1])
    image_file = None

    if file_path.suffix == ".srs":
        representations = medialib_db.get_representation_by_content_id(content_id, medialib_connection)
        if len(representations) == 0:
            logging.debug("register representations for content id = {}".format(content_id))
            cursor = medialib_connection.cursor()
            medialib_db.srs_indexer.srs_update_representations(content_id, file_path, cursor)
            medialib_connection.commit()
            cursor.close()
            representations = medialib_db.get_representation_by_content_id(content_id, medialib_connection)
        if len(representations):
            text_response.append("Representations:")
            for representation in representations:
                text_response.append(
                    "level {} — {}".format(representation.compatibility_level, representation.format)
                )
            if representations[-1].format == "webp":
                image_file = representations[-1].file_path
            else:
                file_path = representations[0].file_path
    elif file_path.suffix == ".webp":
        image_file = file_path
    elif file_path.suffix in {".jpeg", ".jpg"}:
        if pyimglib.decoders.jpeg.is_JPEG(file_path):
            jpeg = pyimglib.decoders.jpeg.JPEGDecoder(file_path)
            arithmetic = False
            try:
                if jpeg.arithmetic_coding():
                    arithmetic = True
            except ValueError:
                arithmetic = True
            if arithmetic:
                img = pyimglib.decoders.open_image(file_path)
                print(img)
                img.thumbnail((1024, 1024))
                print(img)
                buffer = io.BytesIO()
                img.save(buffer, "WEBP", quality=90, method=4)
                image_file = buffer.getvalue()
            else:
                image_file = file_path
    elif file_path.suffix in {".avif",}:
        image_file = None
    else:
        img = pyimglib.decoders.open_image(file_path)
        if isinstance(img, pyimglib.decoders.frames_stream.FramesStream):
            _img = img.next_frame()
            img.close()
            img = _img
        print(img)
        img.thumbnail((1024, 1024))
        print(img)
        buffer = io.BytesIO()
        img.save(buffer, "WEBP", quality=90, method=4)
        image_file = buffer.getvalue()
    if type(image_file) is bytes and len(image_file) == 0:
        image_file = None

    text_response.append("Medialib ID: {}".format(content_id))
    medialib_connection.close()

    if image_file is not None:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id, photo=image_file, caption="\n".join(text_response)
        )
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")


if __name__ == '__main__':
    application = ApplicationBuilder().token(secrets.API_key).build()

    start_handler = CommandHandler('start', start)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), default_answer)
    safe_handler = CommandHandler('safe', safe)
    unknown_handler = MessageHandler(filters.COMMAND, unknown)

    application.add_handler(start_handler)
    application.add_handler(echo_handler)
    application.add_handler(safe_handler)
    application.add_handler(unknown_handler)

    application.run_polling()
