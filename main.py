import enum
import io
import logging
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

UNKNOWN_COMMAND_TEXT_RESPONSE = "Sorry, I didn't understand that command."

def get_user_data(update, connection) -> medialib_db.User:
    return medialib_db.register_user_and_get_info(
        update.effective_user.id, "telegram", connection, username=update.effective_user.username
    )

def get_query_from_text(text: str):
    query_data = text.split(" ", 1)
    if len(query_data) == 2:
        return query_data[1]
    else:
        return ''

def get_permission_level(update, connection, user_data):
    permission_level = user_data.access_level
    if update.effective_chat.type != telegram.constants.ChatType.PRIVATE:
        chat_data: medialib_db.TGChat = medialib_db.register_channel_and_get_info(
            update.effective_chat.id, update.effective_chat.title, connection
        )
        permission_level = chat_data.access_level
    return permission_level

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    medialib_connection.close()
    print("effective_chat", update.effective_chat)
    print("effective_user", update.effective_user)
    response_lines = []
    if permission_level > medialib_db.ACCESS_LEVEL.BAN:
        response_lines.append("Welcome to @mfg637's personal media library.")
        response_lines.append("Type /safe to get random SFW image.")
        response_lines.append("Type /tag `tag_wildcard` to search the tag")
        if permission_level >= medialib_db.ACCESS_LEVEL.SUGGESTIVE:
            response_lines.append("Type /suggestive to get suggestive image.")
            if permission_level >= medialib_db.ACCESS_LEVEL.NSFW:
                response_lines.append("Type /nsfw to get some NSFW image.")
                response_lines.append("Type /explicit to get explicit rated image.")
        response_lines.append("Type /best `POST_ID` to get best available image.")
        response_lines.append("Type /webp `POST_ID` to get WEBP image if available.")
        response_lines.append("Other commands is not supported.")
    else:
        response_lines.append("You are banned. Have a nice day.")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(response_lines))

async def default_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm not a chatbot!")

def query_parser(query:str):
    tag_groups = []
    query_worlds = query.split(" ")
    current_group = {"not": False, "tags": [], "count": 0}
    for word in query_worlds:
        if word in {"and", "AND"}:
            if current_group["count"] > 0:
                tag_groups.append(current_group)
                current_group = {"not": False, "tags": [""], "count": 0}
        elif word in {"not", "NOT"}:
            current_group["not"] = True
        else:
            if len(word) > 0:
                tag_name = word.replace("_", " ")
                if tag_name.isdigit():
                    tag_name = int(tag_name)
                current_group["tags"].append(tag_name)
                current_group["count"] += 1
    if current_group["count"] > 0:
        tag_groups.append(current_group)
    return tag_groups

def filter_pride_tags():
    ORIENTATION_WORDS = ["bisexual", "gay", "futa", "intersex", "lesbian", "transgender", "solo male"]
    pride_tags = []
    for orientation in ORIENTATION_WORDS:
        pride_tags.append({"not": True, "tags": [orientation], "count": 1})
    return pride_tags

def filter_bad_tags():
    bad_tags = []
    for bad_word in secrets.bad_words:
        bad_tags.append({"not": True, "tags": [bad_word], "count": 1})
    return bad_tags

ORIGIN_URL_TEMPLATE = {
    "derpibooru": "https://derpibooru.org/images/{}",
    "ponybooru": "https://ponybooru.org/images/{}",
    "twibooru": "https://twibooru.org/{}",
    "e621": "https://e621.net/posts/{}",
    "furbooru": "https://furbooru.org/images/{}",
    "furaffinity": "https://www.furaffinity.net/view/{}/"
}

def get_image(context, update, raw_content_list, medialib_connection, post_id):
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

    text_response.append("Post ID: {}".format(post_id))

    return image_file, text_response

async def safe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    if permission_level == medialib_db.ACCESS_LEVEL.BAN:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="you are not allowed to do this request")
        medialib_connection.close()
        return

    tags_groups = [{"not": False, "tags": ["safe"], "count": 1}]
    query_string = get_query_from_text(update.message.text)
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
    post_id = medialib_db.register_post(user_data.id, raw_content_list[0][0], medialib_connection)
    image_file, text_response = get_image(context, update, raw_content_list, medialib_connection, post_id)

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


async def suggestive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    if permission_level < medialib_db.ACCESS_LEVEL.SUGGESTIVE:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=UNKNOWN_COMMAND_TEXT_RESPONSE)
        medialib_connection.close()
        return

    tags_groups = [{"not": False, "tags": ["suggestive"], "count": 1}]
    tags_groups.extend(filter_bad_tags())
    if permission_level >= medialib_db.ACCESS_LEVEL.GAY:
        tags_groups.extend(filter_pride_tags())
    query_string = get_query_from_text(update.message.text)
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

    post_id = medialib_db.register_post(user_data.id, raw_content_list[0][0], medialib_connection)
    image_file, text_response = get_image(context, update, raw_content_list, medialib_connection, post_id)

    medialib_connection.close()

    if image_file is not None:
        try:
            spoilered = True
            if update.effective_chat.type == telegram.constants.ChatType.PRIVATE:
                spoilered = False
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=image_file,
                caption="\n".join(text_response),
                has_spoiler=spoilered
            )
        except telegram.error.BadRequest:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))


async def nsfw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    if permission_level < medialib_db.ACCESS_LEVEL.NSFW:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=UNKNOWN_COMMAND_TEXT_RESPONSE)
        medialib_connection.close()
        return

    tags_groups = [{"not": True, "tags": ["safe"], "count": 1}]
    tags_groups.extend(filter_bad_tags())
    if permission_level >= medialib_db.ACCESS_LEVEL.GAY:
        tags_groups.extend(filter_pride_tags())
    query_string = get_query_from_text(update.message.text)
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

    post_id = medialib_db.register_post(user_data.id, raw_content_list[0][0], medialib_connection)
    image_file, text_response = get_image(context, update, raw_content_list, medialib_connection, post_id)

    medialib_connection.close()

    if image_file is not None:
        try:
            spoilered = True
            if update.effective_chat.type == telegram.constants.ChatType.PRIVATE:
                spoilered = False
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=image_file,
                caption="\n".join(text_response),
                has_spoiler=spoilered
            )
        except telegram.error.BadRequest:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))


async def explicit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    if permission_level < medialib_db.ACCESS_LEVEL.NSFW:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=UNKNOWN_COMMAND_TEXT_RESPONSE)
        medialib_connection.close()
        return

    tags_groups = [{"not": False, "tags": ["explicit"], "count": 1}]
    tags_groups.extend(filter_bad_tags())
    if permission_level >= medialib_db.ACCESS_LEVEL.GAY:
        tags_groups.extend(filter_pride_tags())
    query_string = get_query_from_text(update.message.text)
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

    post_id = medialib_db.register_post(user_data.id, raw_content_list[0][0], medialib_connection)
    image_file, text_response = get_image(context, update, raw_content_list, medialib_connection, post_id)

    medialib_connection.close()

    if image_file is not None:
        try:
            spoilered = True
            if update.effective_chat.type == telegram.constants.ChatType.PRIVATE:
                spoilered = False
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=image_file,
                caption="\n".join(text_response),
                has_spoiler=spoilered
            )
        except telegram.error.BadRequest:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(text_response))


async def tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    permission_level = get_permission_level(update, medialib_connection, user_data)
    if permission_level == medialib_db.ACCESS_LEVEL.BAN:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="you are not allowed to do this request")
        medialib_connection.close()
        return

    query_string = get_query_from_text(update.message.text)
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

class UPLOAD_TYPE(enum.Enum):
    BEST = enum.auto()
    WEBP = enum.auto()

async def file_uploader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("BEST HANDLER")
    query = update.message.text.split(" ", 1)
    query_string = ''
    if len(query) == 2:
        query_string = query[1]
    post_id = None
    mode = None
    if "best" in query[0]:
        mode = UPLOAD_TYPE.BEST
    elif "webp" in query[0]:
        mode = UPLOAD_TYPE.WEBP
    else:
        raise NotImplemented(query[0])
    try:
        post_id = int(query_string)
    except:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Invalid Post ID."
        )
        return
    medialib_connection = medialib_db.common.make_connection()
    user_data = get_user_data(update, medialib_connection)
    if user_data.access_level == medialib_db.ACCESS_LEVEL.BAN:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="you are not allowed to do this request")
        medialib_connection.close()
        return
    post_data = medialib_db.get_post(post_id, medialib_connection)
    if post_data is None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Post not found."
        )
        medialib_connection.close()
        return
    if post_data[1] != user_data.id:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="That post is not yours."
        )
        medialib_connection.close()
        return

    content_id = post_data[2]
    content_metadata = medialib_db.get_content_metadata_by_content_id(content_id, medialib_connection)

    file_path = medialib_db.config.relative_to.joinpath(content_metadata[1])

    if file_path.suffix == ".srs":
        representations = medialib_db.get_representation_by_content_id(content_id, medialib_connection)
        if len(representations):
            if mode == UPLOAD_TYPE.BEST:
                file_path = representations[0].file_path
            elif mode == UPLOAD_TYPE.WEBP:
                webp_source = None
                for representation in representations:
                    if representation.format == "webp":
                        webp_source = representation.file_path
                file_path = webp_source
    elif mode == UPLOAD_TYPE.WEBP:
        if file_path.suffix != ".webp":
            file_path = None
    medialib_connection.close()

    if file_path is not None:
        if file_path.suffix == ".mpd":
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Sorry. Cannot post this file."
            )
            return

        await context.bot.send_document(
            chat_id=update.effective_chat.id, document=file_path
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="File not found."
        )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text=UNKNOWN_COMMAND_TEXT_RESPONSE)


if __name__ == '__main__':
    application = ApplicationBuilder().token(secrets.API_key).build()

    start_handler = CommandHandler('start', start)
    help_handler = CommandHandler('help', start)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), default_answer)
    safe_handler = CommandHandler('safe', safe)
    suggestive_handler = CommandHandler('suggestive', suggestive)
    nsfw_handler = CommandHandler('nsfw', nsfw)
    explicit_handler = CommandHandler('explicit', explicit)
    tag_handler = CommandHandler('tag', tag)
    best_handler = CommandHandler('best', file_uploader)
    webp_handler = CommandHandler('webp', file_uploader)
    unknown_handler = MessageHandler(filters.COMMAND, unknown)

    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(echo_handler)
    application.add_handler(safe_handler)
    application.add_handler(suggestive_handler)
    application.add_handler(nsfw_handler)
    application.add_handler(explicit_handler)
    application.add_handler(tag_handler)
    application.add_handler(best_handler)
    application.add_handler(webp_handler)
    application.add_handler(unknown_handler)

    application.run_polling()
