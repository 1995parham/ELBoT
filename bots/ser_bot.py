# In The Name Of God
# ========================================
# [] File Name : ser.py
#
# [] Creation Date : 26-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import bots.abstract_bot
import tgl


class SerBot(bots.abstract_bot.AbstractBot):
    bot_name = 'ser'

    def run_query(self, query: str, msg: tgl.Msg, peer: tgl.Peer):
        """
        return "seriously" in specific language
        :param query: str
        :return:
        """
        if len(query) < 1:
            peer.send_msg("seriously", reply=msg.id)
        else:
            pass
