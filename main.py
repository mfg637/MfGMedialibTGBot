import io
import logging
import pathlib
import time

import telegram.error

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
    print("effective_chat", update.effective_chat)
    print("effective_user", update.effective_user)
    response = (
        "Welcome to @mfg637's personal media library.\n"
        "Type /safe to get random SFW image.\n"
        "Type /tag `tag_wildcard` to search the tag\n"
        "Other commands is not supported."
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=response)

async def default_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm not a chatbot!")

def query_parser(query:str):
    tag_groups = []
    query_worlds = query.split(" ")
    current_group = {"not": False, "tags": [""], "count": 0}
    for word in query_worlds:
        if word in {"and", "AND"}:
            if current_group["count"] > 0:
                tag_groups.append(current_group)
                current_group = {"not": False, "tags": [""], "count": 0}
        elif word in {"not", "NOT"}:
            current_group["not"] = True
        else:
            tag_name = word.replace("_", " ")
            current_group["tags"].append(tag_name)
            current_group["count"] += 1
    if current_group["count"] > 0:
        tag_groups.append(current_group)
    return tag_groups


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
    query_string = update.message.text[6:]
    tags_groups.extend(query_parser(query_string))
    print("tags_groups", tags_groups)
    try:
        raw_content_list = medialib_db.files_by_tag_search.get_media_by_tags(
            *tags_groups,
            limit=1,
            offset=0,
            order_by=medialib_db.files_by_tag_search.ORDERING_BY.RANDOM,
            filter_hidden=medialib_db.files_by_tag_search.HIDDEN_FILTERING.FILTER
        )
    except IndexError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="not found any images by your query"
        )
    if len(raw_content_list) == 0:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="not found any images by your query"
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
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, photo=image_file, caption="\n".join(text_response)
            )
        except telegram.error.BadRequest:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))


async def tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    query_string = update.message.text[5:]
    response_lines = []
    if '*' in query_string:
        tag_aliases = medialib_db.tags_indexer.wildcard_tag_search(query_string, medialib_connection)
        for tag_alias in tag_aliases:
            tag_info = medialib_db.tags_indexer.get_tag_info_by_tag_id(tag_alias[0], medialib_connection)
            response_lines.append(
                "{} → id{}: {} ({})".format(tag_alias[1], tag_info[0], tag_info[1], tag_info[2])
            )
        if len(tag_aliases) == 0:
            response_lines.append("not found")
    else:
        response_lines.append("not implemented")
    medialib_connection.close()
    i = 0
    while i < len(response_lines):
        send_response = response_lines[i:(i + 10)]
        if i == 0 and len(response_lines) > 10:
            send_response.insert(0, "There are long list. Please, wait until all contents will be send.")
        if len(send_response) > 0:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="\n".join(send_response)
            )
        time.sleep(2)
        i += 10
    if len(response_lines) > 10:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="END"
        )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")


if __name__ == '__main__':
    application = ApplicationBuilder().token(secrets.API_key).build()

    start_handler = CommandHandler('start', start)
    help_handler = CommandHandler('help', start)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), default_answer)
    safe_handler = CommandHandler('safe', safe)
    tag_handler = CommandHandler('tag', tag)
    unknown_handler = MessageHandler(filters.COMMAND, unknown)

    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(echo_handler)
    application.add_handler(safe_handler)
    application.add_handler(tag_handler)
    application.add_handler(unknown_handler)

    application.run_polling()
