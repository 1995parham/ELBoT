# In The Name Of God
# ========================================
# [] File Name : GoogleBot.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import urllib.request
import urllib.parse
import json

from elbot.core.AbstractBot import AbstractBot
from elbot.core.BotFather import BotFather
from elbot.core.Message import Message


class GoogleBot(AbstractBot):
    bot_name = 'google'

    def run_query(self, message: Message, father: BotFather):
        father.send_message(chat_id=message.chat.id, text=GoogleBot.search_google(message.text),
                            reply_to_message_id=message.message_id)

    @staticmethod
    def search_google(terms):
        """
        Returns the link and the description of the first result from a google search
        Telegram usage: google <search term>
        :param terms:
        :return:
        """

        query = terms
        query = urllib.parse.urlencode({'q': query})
        request = urllib.request.urlopen('http://ajax.googleapis.com/ajax/services/search/web?v=1.0&' + query).read()
        request = str(request, 'UTF-8')
        response = json.loads(request)
        results = response['responseData']['results']
        retval = ""
        for result in results:
            title = result['title']
            url = result['url']  # was URL in the original and that threw a name error exception
            title = title.translate({ord(k): None for k in u'<b>'})
            title = title.translate({ord(k): None for k in u'</b>'})
            retval += title + ' : ' + url + '\n'
        return retval
