# In The Name Of God
# ========================================
# [] File Name : wiki.py
#
# [] Creation Date : 27-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import urllib.request
import tgl
import bots.abstract_bot


class WikiBot(bots.abstract_bot.AbstractBot):
    bot_name = 'wiki'

    def run_query(self, query: str, msg: tgl.Msg, peer: tgl.Peer):
        """
        Returns a wiki link and the first paragraph of the page
        Telegram usage: wiki <search term>
        :param query: search term
        """

        main_page = 'http://en.wikipedia.org/wiki/Main_Page'

        search_term = query.replace(' ', '_')

        if len(search_term) < 1:
            url = main_page
        else:
            url = 'http://en.wikipedia.org/wiki/' + search_term
            try:
                urllib.request.urlopen(url)
            except urllib.request.HTTPError:
                url = main_page

        peer.send_msg(url, reply=msg.id)
