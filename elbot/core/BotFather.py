# In The Name Of God
# ========================================
# [] File Name : BotFather.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

import json
import threading
import time

import requests

from ..domain.update import Update
from ..domain.keyboard import ReplyKeyboardMarkup, \
     ReplyKeyboardMarkdownJSONEncoder


class BotFather(threading.Thread):
    """
    The main class which run commands and returns their response
    :type hash_id: str
    :type base_url: str
    :type bots: list[]
    """

    def __init__(self, hash_id, bots=None):
        if not bots:
            bots = []
        super().__init__(name="Bot Thread {}".format(hash_id))
        self.setDaemon(False)
        self.hash_id = hash_id
        self.base_url = 'https://api.telegram.org/bot' + self.hash_id + '/'
        self.bots = bots

    def send_message(self, chat_id: int, text: str,
                     disable_web_page_preview: bool = False,
                     reply_to_message_id: int = 0,
                     reply_markup: ReplyKeyboardMarkup = None,
                     parse_mode: str = ""):
        """
        Use this method to send text messages.
        On success, the sent Message is returned.
        :param chat_id: Unique identifier for the message recipient —
                        User or GroupChat id
        :param text: Text of the message to be sent
        :param disable_web_page_preview: Disables link previews for
                                        links in this message
        :param reply_to_message_id: If the message is a reply,
                                    ID of the original message
        :param reply_markup: Additional interface options. A JSON-serialized object for a custom reply keyboard,
                             instructions to hide keyboard or to force a reply from the user.
        :param parse_mode: Send Markdown, if you want Telegram apps to show bold, italic and inline URLs in your
                           bot's message. For the moment, only Telegram for Android supports this.
        :return: None
        """
        params = {
            'chat_id': str(chat_id),
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
        }
        if reply_to_message_id != 0:
            params['reply_to_message_id'] = reply_to_message_id
        if reply_markup is not None and isinstance(reply_markup,
                                                   ReplyKeyboardMarkup):
            params['reply_markup'] = json.dumps(reply_markup,
                                                cls=ReplyKeyboardMarkdownJSONEncoder)
        if parse_mode is not None:
            params['parse_mode'] = parse_mode
        requests.post(url=self.base_url + 'sendMessage', data=params,
                      headers={
                          'content-type': 'application/x-www-form-urlencoded'})

    def send_chat_action(self, chat_id: int, action: str):

        """
        Use this method to send text messages. On success, the sent Message is returned.
        :param chat_id: Unique identifier for the message recipient — User or GroupChat id
        :param action: Type of action to broadcast. Choose one, depending on what the user is about to receive:
                       typing for text messages,
                       upload_photo for photos,
                       record_video or upload_video for videos,
                       record_audio or upload_audio for audio files,
                       upload_document for general files,
                       find_location for location data.
        :return: None
        """
        params = {
            'chat_id': str(chat_id),
            'action': action,
        }
        requests.post(url=self.base_url + 'sendChatAction', data=params,
                      headers={
                          'content-type': 'application/x-www-form-urlencoded'})

    def send_video(self, chat_id: int, video: str,
                   reply_to_message_id: int = 0, caption: str = ""):
        """

        :param chat_id: Unique identifier for the message recipient — User or GroupChat id
        :param video: Path to video
        :param reply_to_message_id: If the message is a reply, ID of the original message
        :return: None
        """
        params = {
            'chat_id': str(chat_id),
        }
        if reply_to_message_id != 0:
            params['reply_to_message_id'] = reply_to_message_id
        if caption != "":
            params['caption'] = caption
        requests.post(url=self.base_url + 'sendVideo', params=params,
                      files={'video': open(video, 'rb')})

    def get_updates(self, offset: int = 0, limit: int = 0,
                    timeout: int = 0) -> [Update.Update]:
        """
        Use this method to receive incoming updates using long polling. An Array of Update objects is returned.
        :param offset: Identifier of the first update to be returned.
                       Must be greater by one than the highest among the identifiers of previously received updates.
                       By default, updates starting with the earliest unconfirmed update are returned.
                       An update is considered confirmed as soon as getUpdates is called with an offset higher than
                       its update_id.
        :param limit: Limits the number of updates to be retrieved. Values between 1—100 are accepted. Defaults to 100
        :param timeout: Timeout in seconds for long polling. Defaults to 0, i.e. usual short polling
        :return: list[Update.Update]
        """
        updates = []
        params = {}
        if offset != 0:
            params['offset'] = offset
        if limit != 0:
            params['limit'] = limit
        if timeout != 0:
            params['timeout'] = timeout
        response = requests.get(url=self.base_url + 'getUpdates',
                                params=params)
        response = json.loads(response.text)
        if response['ok']:
            for obj in response['result']:
                update = Update.UpdateDictDecoder.decode(obj)
                updates.append(update)
        else:
            print("We get error at {}".format(response['result']))
        return updates

    def run(self):
        update_id = 0
        while True:
            try:
                updates = self.get_updates(offset=update_id)
                if len(updates) != 0:
                    update_id = updates[-1].update_id + 1
                for update in updates:
                    message = update.message
                    for bot in self.bots:
                        if message.text is not None and message.text.startswith("/" + bot.bot_name):
                            query = message.text[1 + len(bot.bot_name):]
                            query = query.lstrip()
                            query = query.rstrip()
                            message.text = query
                            bot.run_query(message, self)
            except Exception as ex:
                print("Error: {}".format(ex))
                raise ex
        time.sleep(10)
