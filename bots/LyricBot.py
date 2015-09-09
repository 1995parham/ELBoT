# In The Name Of God
# ========================================
# [] File Name : LyricBot.py
#
# [] Creation Date : 26-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import suds.client

from elbot.AbstractBot import AbstractBot
from elbot.Message import Message
from elbot.BotFather import BotFather


class LyricBot(AbstractBot):
    bot_name = 'lyric'

    def run_query(self, message: Message, father: BotFather):
        father.send_message(chat_id=message.chat.id,
                            text=LyricBot.get_lyric(message.text.split(',')[0], message.text.split(',')[1]),
                            reply_to_message_id=message.message_id,
                            disable_web_page_preview=True)

    @staticmethod
    def get_lyric(artist, song):
        client = suds.client.Client('http://lyrics.wikia.com/server.php?wsdl')

        retval = '404 Not Found'
        response = client.service.checkSongExists(artist, song)
        if response:
            response = client.service.getSongResult(artist, song)
            retval = """
{0:#^54}
{1}
{3:=^54}
{2}
{3:=^54}
Please note that currently, the API only returns a small portion of the lyrics (about 1/7th of them).
This is because of the licensing restrictions
""".format(' ' + response.artist + ' : ' + response.song + ' ', str(response.lyrics.encode("ISO-8859-1"), 'UTF-8'),
           response.url, "")
        return retval


# Just for test :-)
if __name__ == '__main__':
    print(LyricBot.get_lyric('Poets of the Fall', 'Daze'))
