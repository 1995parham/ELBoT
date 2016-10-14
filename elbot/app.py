# In The Name Of God
# ========================================
# [] File Name : app.py
#
# [] Creation Date : 11-10-2016
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
import time
import requests
import json
import threading
import re

from .domain.update import Update, UpdateDictDecoder
from .domain.keyboard import ReplyKeyboardMarkup, \
     ReplyKeyboardMarkdownJSONEncoder


class ELBot:
    def __init__(self, hash_id):
        self.hash_id = hash_id
        self.base_url = 'https://api.telegram.org/bot' + self.hash_id + '/'
        self.message_handlers = {}

    def send_message(self, chat_id: int, text: str,
                     disable_web_page_preview: bool = False,
                     reply_to_message_id: int = 0,
                     reply_markup: ReplyKeyboardMarkup = None,
                     parse_mode: str = ""):
        '''
        Use this method to send text messages.
        On success, the sent Message is returned.
        :param chat_id: Unique identifier for the message recipient —
                        User or GroupChat id
        :param text: Text of the message to be sent
        :param disable_web_page_preview: Disables link previews for
                                        links in this message
        :param reply_to_message_id: If the message is a reply,
                                    ID of the original message
        :param reply_markup: Additional interface options. A JSON-serialized
                             object for a custom reply keyboard,
                             instructions to hide keyboard or to force a reply
                             from the user.
        :param parse_mode: Send Markdown, if you want Telegram apps to show
                           bold, italic and inline URLs in your
                           bot's message. For the moment,
                           only Telegram for Android supports this.
        :return: None
        '''
        params = {
            'chat_id': str(chat_id),
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
        }

        if reply_to_message_id != 0:
            params['reply_to_message_id'] = reply_to_message_id

        if reply_markup is not None and isinstance(reply_markup,
                                                   ReplyKeyboardMarkup):
            params['reply_markup'] = \
                    json.dumps(reply_markup,
                               cls=ReplyKeyboardMarkdownJSONEncoder)

        if parse_mode is not None:
            params['parse_mode'] = parse_mode

        requests.post(url=self.base_url + 'sendMessage', data=params,
                      headers={
                          'content-type': 'application/x-www-form-urlencoded'})

    def get_updates(self, offset: int = 0, limit: int = 0,
                    timeout: int = 0) -> [Update]:
        '''
        Use this method to receive incoming updates using long polling.
        An Array of Update objects is returned.
        :param offset: Identifier of the first update to be returned.
                       Must be greater by one than the highest among the
                       identifiers of previously received updates.
                       By default, updates starting with the earliest
                       unconfirmed update are returned.
                       An update is considered confirmed as soon as getUpdates
                       is called with an offset higher than
                       its update_id.
        :param limit: Limits the number of updates to be retrieved.
                      Values between 1—100 are accepted. Defaults to 100
        :param timeout: Timeout in seconds for long polling. Defaults to 0,
                        i.e. usual short polling
        :return: list[Update]
        '''
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
                update = UpdateDictDecoder.decode(obj)
                updates.append(update)
        else:
            print("We get error at {}".format(response['result']))
        return updates

    def message(self, message: str):
        def _message(fn):
            pattern = re.compile(message)
            self.message_handlers[pattern] = fn
            return fn
        return _message

    def run(self):
        update_id = 0
        while True:
            try:
                updates = self.get_updates(offset=update_id)
                if len(updates) != 0:
                    update_id = updates[-1].update_id + 1
                for update in updates:
                    message = update.message
                    for pattern in self.message_handlers:
                        if pattern.match(message.text) is not None:
                            threading.Thread(
                                target=self.message_handlers[pattern],
                                daemon=True).start()
                    print(message.text)
            except Exception as ex:
                print("Error: {}".format(ex))
                raise ex
        time.sleep(10)
