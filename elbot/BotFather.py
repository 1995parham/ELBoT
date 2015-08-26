# In The Name Of God
# ========================================
# [] File Name : BotFather.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import requests


class BotFather:
    """
    The main class which run commands and returns their response
    :type: str
    :type: str
    """

    def __init__(self, hash_id):
        self.hash_id = hash_id
        self.base_url = 'https://api.telegram.org/bot' + self.hash_id + '/'

    def send_message(self, chat_id: int, text: str, disable_web_page_preview: bool=False,
                     reply_to_message_id: int=0, reply_markup=None):
        """
        Use this method to send text messages. On success, the sent Message is returned.
        :param chat_id: Unique identifier for the message recipient — User or GroupChat id
        :param text: Text of the message to be sent
        :param disable_web_page_preview: Disables link previews for links in this message
        :param reply_to_message_id: If the message is a reply, ID of the original message
        :param reply_markup: Additional interface options. A JSON-serialized object for a custom reply keyboard,
                             instructions to hide keyboard or to force a reply from the user.
        :return: None
        """
        params = {
            'chat_id': str(chat_id),
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
        }
        if reply_to_message_id != 0:
            params['reply_to_message_id'] = reply_to_message_id
        if reply_markup is not None:
            params['reply_markup'] = reply_markup
        requests.get(url=self.base_url + 'sendMessage', params=params)
