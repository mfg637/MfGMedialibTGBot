import secrets
import telebot
import medialib_db


def main():
    bot = telebot.TeleBot(secrets.API_key)

    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        bot.reply_to(message, "Howdy, how are you doing?")

    @bot.message_handler(commands=['safe',])
    def send_welcome(message):
        ORIGIN_URL_TEMPLATE = {
            "derpibooru": "https://derpibooru.org/images/{}",
            "ponybooru": "https://ponybooru.org/images/{}",
            "twibooru": "https://twibooru.org/{}",
            "e621": "https://e621.net/posts/{}",
            "furbooru": "https://furbooru.org/images/{}",
            "furaffinity": "https://www.furaffinity.net/view/{}/"
        }
        ORIGIN_PREFIX = {
            "derpibooru": "db",
            "ponybooru": "pb",
            "twibooru": "tb",
            "e621": "ef",
            "furbooru": "fb",
            "furaffinity": "fa"
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
            text_response.append("*Title*: {}\n".format(content_metadata[2]))
        if content_metadata[4] is not None:
            text_response.append("*Description*: {}\n".format(content_metadata[4]))
        if content_metadata[6] is not None and content_metadata[7] is not None:
            text_response.append(
                "*Source*: {}\n".format(ORIGIN_PREFIX[content_metadata[6]].format(content_metadata[7]))
            )
        text_response.append("*Medialib ID*: {}".format(content_id))
        medialib_connection.close()
        bot.reply_to(message, "\n".join(text_response))

    @bot.message_handler(func=lambda message: True)
    def echo_all(message):
        bot.reply_to(message, message.__repr__())

    bot.infinity_polling()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    main()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
